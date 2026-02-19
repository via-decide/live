const express = require("express");
const cors = require("cors");
const fetch = require("node-fetch"); // node-fetch@2
const path = require("path");

const app = express();

// If you serve frontend from same server, CORS isn't needed.
// Keep it enabled anyway for local dev.
app.use(cors());

// ---- CONFIG ----
const PORT = process.env.PORT || 3000;
const providerRefreshMs = Number(process.env.PROVIDER_REFRESH_MS || 5000);

const METALS_API_KEY = process.env.METALS_API_KEY; // required for metals
const METALS_BASE = "USD";
// These symbols may vary by plan/provider. Adjust if your Metals API uses different codes.
const METALS_SYMBOLS = ["XAU", "XAG", "XCU", "XZN"];

const EIA_API_KEY = process.env.EIA_API_KEY; // required for crude (EIA is not truly intraday)
const EIA_SERIES_ID = process.env.EIA_SERIES_ID || "PET.RWTC.D";

// ---- CACHE (what /prices returns) ----
let cache = {
  ok: false,
  providerRefreshMs,
  err: "warming up",
  data: {
    instruments: {
      gold:   { price: null, unit: "USD/XAU", source: "Metals-API", updated: null },
      silver: { price: null, unit: "USD/XAG", source: "Metals-API", updated: null },
      copper: { price: null, unit: "USD/XCU", source: "Metals-API", updated: null },
      zinc:   { price: null, unit: "USD/XZN", source: "Metals-API", updated: null },
      crude:  { price: null, unit: "USD/bbl", source: "EIA",       updated: null },
    },
  },
};

async function fetchMetals() {
  if (!METALS_API_KEY) throw new Error("Missing METALS_API_KEY");
  const url =
    `https://metals-api.com/api/latest` +
    `?access_key=${encodeURIComponent(METALS_API_KEY)}` +
    `&base=${encodeURIComponent(METALS_BASE)}` +
    `&symbols=${encodeURIComponent(METALS_SYMBOLS.join(","))}`;

  const res = await fetch(url, { timeout: 15000 });
  if (!res.ok) throw new Error(`Metals API HTTP ${res.status}`);
  const json = await res.json();
  if (json.success === false) throw new Error(json.error?.info || "Metals API error");
  return json;
}

async function fetchCrudeEIA() {
  if (!EIA_API_KEY) throw new Error("Missing EIA_API_KEY");

  const url =
    `https://api.eia.gov/series/` +
    `?api_key=${encodeURIComponent(EIA_API_KEY)}` +
    `&series_id=${encodeURIComponent(EIA_SERIES_ID)}`;

  const res = await fetch(url, { timeout: 15000 });
  if (!res.ok) throw new Error(`EIA HTTP ${res.status}`);
  const json = await res.json();

  const series = json.series && json.series[0];
  const latest = series && series.data && series.data[0]; // [date,value]
  if (!latest) throw new Error("EIA: no latest datapoint");

  const crude = Number(latest[1]);
  if (!Number.isFinite(crude)) throw new Error("EIA: crude not numeric");

  return { crude, unit: series.units || "USD/bbl" };
}

async function refreshProviders() {
  try {
    const nowIso = new Date().toISOString();

    const [m, c] = await Promise.all([
      fetchMetals(),
      fetchCrudeEIA(),
    ]);

    const rates = m.rates || {};
    // Metals API might return rates as "USD per metal unit" depending on provider.
    // We keep it consistent with what the API returns.
    cache = {
      ok: true,
      providerRefreshMs,
      err: null,
      data: {
        instruments: {
          gold:   { price: typeof rates.XAU === "number" ? rates.XAU : null, unit: "USD/XAU", source: "Metals-API", updated: nowIso },
          silver: { price: typeof rates.XAG === "number" ? rates.XAG : null, unit: "USD/XAG", source: "Metals-API", updated: nowIso },
          copper: { price: typeof rates.XCU === "number" ? rates.XCU : null, unit: "USD/XCU", source: "Metals-API", updated: nowIso },
          zinc:   { price: typeof rates.XZN === "number" ? rates.XZN : null, unit: "USD/XZN", source: "Metals-API", updated: nowIso },
          crude:  { price: c.crude, unit: c.unit, source: "EIA", updated: nowIso },
        },
      },
    };
  } catch (e) {
    // keep last known data, but mark provider error
    cache = {
      ...cache,
      ok: false,
      err: String(e && e.message ? e.message : e),
      providerRefreshMs,
    };
  }
}

// --- API ---
app.get("/prices", (req, res) => {
  res.json(cache);
});

// --- OPTIONAL: Serve your frontend from /public ---
// Put your HTML file at: ./public/index.html
app.use("/", express.static(path.join(__dirname, "public")));

// --- Start ---
app.listen(PORT, async () => {
  await refreshProviders(); // warm cache
  setInterval(refreshProviders, providerRefreshMs);
  console.log(`Server running: http://127.0.0.1:${PORT}`);
  console.log(`API endpoint:   http://127.0.0.1:${PORT}/prices`);
});
