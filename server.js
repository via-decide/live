const express = require('express');
const cors = require('cors');
const fetch = require('node-fetch');

const PORT = 3000;

// Env vars (API keys must never be exposed to frontend)
const METALS_API_KEY = process.env.METALS_API_KEY || '';
const EIA_API_KEY = process.env.EIA_API_KEY || '';
const PROVIDER_REFRESH_MS = toPositiveInt(process.env.PROVIDER_REFRESH_MS, 5000);

const app = express();
app.use(cors());

// In-memory cache
const cache = {
  providerRefreshMs: PROVIDER_REFRESH_MS,
  lastProviderRefreshAt: 0,
  ok: false,
  lastError: null,
  data: {
    instruments: {
      gold: null,
      silver: null,
      copper: null,
      zinc: null,
      crude: null
    }
  }
};

function toPositiveInt(value, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  const i = Math.floor(n);
  return i > 0 ? i : fallback;
}

function nowMs() {
  return Date.now();
}

function isoNow() {
  return new Date().toISOString();
}

function safeNumber(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

function normalizeInstrument(name, price, unit, source, updatedIso) {
  const p = safeNumber(price);
  if (p === null) return null;
  return {
    name,
    price: p,
    unit,
    source,
    updated: updatedIso || isoNow()
  };
}

async function fetchMetalsLatest() {
  if (!METALS_API_KEY) {
    throw new Error('Missing METALS_API_KEY');
  }

  // Metals-API latest endpoint (base USD)
  // We request both ZNC and XZN and pick whichever exists, to be resilient across symbol naming.
  const symbols = ['XAU', 'XAG', 'XCU', 'ZNC', 'XZN'].join(',');
  const url = `https://metals-api.com/api/latest?access_key=${encodeURIComponent(METALS_API_KEY)}&base=USD&symbols=${encodeURIComponent(symbols)}`;

  const resp = await fetch(url, { method: 'GET', timeout: 10000 });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`Metals-API HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }

  const json = await resp.json();
  if (!json || json.success !== true || !json.rates) {
    const msg = json && json.error && json.error.info ? json.error.info : 'Unexpected Metals-API response';
    throw new Error(msg);
  }

  const rates = json.rates || {};
  const updatedIso = json.date ? new Date(json.date + 'T00:00:00Z').toISOString() : isoNow();

  const gold = normalizeInstrument('Gold', rates.XAU, 'USD/oz', 'Metals-API', updatedIso);
  const silver = normalizeInstrument('Silver', rates.XAG, 'USD/oz', 'Metals-API', updatedIso);
  const copper = normalizeInstrument('Copper', rates.XCU, 'USD/oz', 'Metals-API', updatedIso);

  const zincRate = (typeof rates.ZNC !== 'undefined') ? rates.ZNC : rates.XZN;
  const zinc = normalizeInstrument('Zinc', zincRate, 'USD/oz', 'Metals-API', updatedIso);

  return { gold, silver, copper, zinc };
}

async function fetchEiaWti() {
  if (!EIA_API_KEY) {
    throw new Error('Missing EIA_API_KEY');
  }

  // EIA API v1 series endpoint (per requirement)
  const seriesId = 'PET.RWTC.D';
  const url = `https://api.eia.gov/series/?api_key=${encodeURIComponent(EIA_API_KEY)}&series_id=${encodeURIComponent(seriesId)}`;

  const resp = await fetch(url, { method: 'GET', timeout: 10000 });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`EIA HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }

  const json = await resp.json();
  const series = json && json.series && json.series[0];
  if (!series || !Array.isArray(series.data) || series.data.length === 0) {
    throw new Error('Unexpected EIA series response');
  }

  const latest = series.data[0]; // [date, value]
  const dateStr = latest && latest[0] ? String(latest[0]) : null;
  const value = latest && latest[1];

  // Date strings can be 'YYYYMMDD' or 'YYYY-MM-DD' depending on series; handle both.
  let updatedIso = isoNow();
  if (dateStr) {
    if (/^\d{8}$/.test(dateStr)) {
      const y = dateStr.slice(0, 4);
      const m = dateStr.slice(4, 6);
      const d = dateStr.slice(6, 8);
      updatedIso = new Date(`${y}-${m}-${d}T00:00:00Z`).toISOString();
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
      updatedIso = new Date(dateStr + 'T00:00:00Z').toISOString();
    }
  }

  const source = series.name ? `EIA (${seriesId})` : `EIA ${seriesId}`;
  const crude = normalizeInstrument('Crude Oil (WTI)', value, 'USD/bbl', source, updatedIso);

  return { crude };
}

async function refreshProvidersOnce() {
  const startedAt = nowMs();
  const errors = [];

  const next = {
    instruments: {
      gold: null,
      silver: null,
      copper: null,
      zinc: null,
      crude: null
    }
  };

  // Fetch in parallel
  const [metalsRes, eiaRes] = await Promise.allSettled([
    fetchMetalsLatest(),
    fetchEiaWti()
  ]);

  if (metalsRes.status === 'fulfilled') {
    next.instruments.gold = metalsRes.value.gold;
    next.instruments.silver = metalsRes.value.silver;
    next.instruments.copper = metalsRes.value.copper;
    next.instruments.zinc = metalsRes.value.zinc;
  } else {
    errors.push(String(metalsRes.reason && metalsRes.reason.message ? metalsRes.reason.message : metalsRes.reason));
  }

  if (eiaRes.status === 'fulfilled') {
    next.instruments.crude = eiaRes.value.crude;
  } else {
    errors.push(String(eiaRes.reason && eiaRes.reason.message ? eiaRes.reason.message : eiaRes.reason));
  }

  const ok = errors.length === 0 &&
    next.instruments.gold && next.instruments.silver && next.instruments.copper && next.instruments.zinc && next.instruments.crude;

  cache.providerRefreshMs = PROVIDER_REFRESH_MS;
  cache.lastProviderRefreshAt = startedAt;
  cache.ok = ok;
  cache.lastError = errors.length ? errors.join(' | ') : null;
  cache.data = { instruments: next.instruments };
}

function startRefreshLoop() {
  // Immediate refresh on boot
  refreshProvidersOnce().catch((err) => {
    cache.ok = false;
    cache.lastError = String(err && err.message ? err.message : err);
    cache.lastProviderRefreshAt = nowMs();
  });

  setInterval(() => {
    refreshProvidersOnce().catch((err) => {
      cache.ok = false;
      cache.lastError = String(err && err.message ? err.message : err);
      cache.lastProviderRefreshAt = nowMs();
    });
  }, PROVIDER_REFRESH_MS);
}

// GET /prices (frontend polls every 1000ms)
app.get('/prices', (req, res) => {
  res.json({
    ok: !!cache.ok,
    providerRefreshMs: cache.providerRefreshMs,
    data: {
      instruments: {
        gold: cache.data.instruments.gold,
        silver: cache.data.instruments.silver,
        copper: cache.data.instruments.copper,
        zinc: cache.data.instruments.zinc,
        crude: cache.data.instruments.crude
      }
    }
  });
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Proxy server listening on http://127.0.0.1:${PORT}`);
  console.log(`Provider refresh every ${PROVIDER_REFRESH_MS}ms`);
  if (!METALS_API_KEY) console.log('Warning: METALS_API_KEY is not set');
  if (!EIA_API_KEY) console.log('Warning: EIA_API_KEY is not set');
  startRefreshLoop();
});
