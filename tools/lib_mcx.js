const fs = require("fs");
const path = require("path");
const fetch = require("node-fetch");

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function toNumber(x) {
  if (x == null) return null;
  const v = String(x).trim().replace(/,/g, "");
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Very tolerant CSV parser (no quoted commas support).
 * If MCX file is different, adjust parser accordingly.
 */
function parseCsvLoose(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (!lines.length) return [];
  const header = lines[0].split(",").map(s => s.trim());
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(",").map(s => s.trim());
    if (cols.length < Math.min(5, header.length)) continue;
    const obj = {};
    for (let j = 0; j < header.length; j++) obj[header[j]] = cols[j];
    rows.push(obj);
  }
  return rows;
}

async function downloadToFile(url, outPath) {
  const res = await fetch(url, { timeout: 20000 });
  if (!res.ok) throw new Error(`Download failed HTTP ${res.status}`);
  const buf = await res.buffer();
  ensureDir(path.dirname(outPath));
  fs.writeFileSync(outPath, buf);
  return outPath;
}

function readTextFile(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

/**
 * Find GOLD contract row.
 * NOTE: MCX bhavcopy columns vary. We match loosely by common keys.
 */
function extractGoldRow(rows) {
  // Try common column names (case-insensitive)
  const norm = (s) => String(s || "").toLowerCase();

  const candidates = rows.filter(r => {
    const keys = Object.keys(r);
    const maybeSymbol =
      r.SYMBOL || r.Symbol || r.INSTRUMENT || r.Instrument || r.COMMODITY || r.Commodity || r["COMMODITY_NAME"];
    const v = norm(maybeSymbol);
    return v.includes("gold");
  });

  if (!candidates.length) return null;

  // If there is an expiry column, prefer nearest expiry by lexical (works if YYYY-MM-DD)
  const getExpiry = (r) => r.EXPIRY || r.Expiry || r["EXPIRY_DATE"] || r["Expiry Date"] || null;
  candidates.sort((a,b) => {
    const ea = getExpiry(a) || "9999-12-31";
    const eb = getExpiry(b) || "9999-12-31";
    return String(ea).localeCompare(String(eb));
  });

  return candidates[0];
}

function mapRowToBar(row) {
  // Loose mapping: these keys may differ — adjust once you see your file header.
  const get = (...keys) => {
    for (const k of keys) if (row[k] != null) return row[k];
    return null;
  };

  const o = toNumber(get("OPEN", "Open", "OPEN_PRICE", "OPENPR"));
  const h = toNumber(get("HIGH", "High", "HIGH_PRICE", "HIGHPR"));
  const l = toNumber(get("LOW", "Low", "LOW_PRICE", "LOWPR"));
  const c = toNumber(get("CLOSE", "Close", "CLOSE_PRICE", "CLOSEPR", "SETTLE", "Settle", "SETTLEMENT_PRICE"));
  const pc = toNumber(get("PREVCLOSE", "PREV_CLOSE", "Prev Close", "PREVIOUS_CLOSE", "PREVCLOSE_PRICE"));
  const vol = toNumber(get("VOLUME", "Volume", "VOLUME_TRD", "TRD_QTY"));
  const val = toNumber(get("VALUE", "Value", "TRD_VAL", "TURNOVER"));
  const oi = toNumber(get("OI", "Open Interest", "OPEN_INTEREST", "OPENINT"));

  const expiry = get("EXPIRY", "Expiry", "EXPIRY_DATE", "Expiry Date") || null;
  const symbol = get("SYMBOL", "Symbol", "INSTRUMENT", "Instrument", "COMMODITY", "Commodity", "COMMODITY_NAME") || "GOLD";

  return { symbol, expiry, o, h, l, c, pc, vol, val, oi };
}

module.exports = {
  parseCsvLoose,
  downloadToFile,
  readTextFile,
  extractGoldRow,
  mapRowToBar
};
