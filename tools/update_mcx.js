const fs = require("fs");
const path = require("path");

const { ema, atr14, zscore } = require("./indicators");
const {
  parseCsvLoose,
  downloadToFile,
  readTextFile,
  extractGoldRow,
  mapRowToBar
} = require("./lib_mcx");

const DATA_DIR = path.join(__dirname, "..", "data");
const HISTORY_PATH = path.join(DATA_DIR, "mcx_gold_history.jsonl");
const LATEST_PATH = path.join(DATA_DIR, "mcx_gold_latest.json");

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }

function loadHistory() {
  if (!fs.existsSync(HISTORY_PATH)) return [];
  const lines = fs.readFileSync(HISTORY_PATH, "utf8").split(/\r?\n/).filter(Boolean);
  return lines.map(l => JSON.parse(l));
}

function appendHistory(record) {
  ensureDir(DATA_DIR);
  fs.appendFileSync(HISTORY_PATH, JSON.stringify(record) + "\n");
}

function saveLatest(record) {
  ensureDir(DATA_DIR);
  fs.writeFileSync(LATEST_PATH, JSON.stringify(record, null, 2));
}

function computeScore(history) {
  // history: [{date, o,h,l,c, oi, vol, ...}] sorted oldest->newest
  if (history.length < 60) {
    return {
      score: 0,
      verdict: "HOLD",
      confidence: 0,
      signals: { reason: "need >= 60 days history" }
    };
  }

  const closes = history.map(x => x.c).filter(n => typeof n === "number");
  const vols = history.map(x => x.vol).filter(n => typeof n === "number");
  const ois = history.map(x => x.oi).filter(n => typeof n === "number");

  const last = history[history.length - 1];
  const prev = history[history.length - 2];

  const ret1d = (prev?.c && last?.c) ? (last.c / prev.c - 1) : 0;

  const idx5 = history.length - 6;
  const base5 = history[idx5]?.c;
  const ret5d = (base5 && last?.c) ? (last.c / base5 - 1) : 0;

  const ema20 = ema(closes, 20);
  const ema50 = ema(closes, 50);
  const emaSpread = (ema20 != null && ema50 != null && ema50 !== 0) ? (ema20 / ema50 - 1) : 0;

  const atr = atr14(history.map(x => ({ h: x.h, l: x.l, c: x.c })));
  const volZ = zscore(vols, 60) ?? 0;
  const oiZ = zscore(ois, 60) ?? 0;

  // Normalize simple signals to [-1, +1]
  const trend = clamp(emaSpread * 10, -1, 1);      // 10x scale
  const mom = clamp((ret5d * 8) + (ret1d * 3), -1, 1);
  const participation = clamp((oiZ / 3) + (volZ / 3), -1, 1); // combine

  // Risk penalty: too high ATR relative to price -> reduce confidence
  const atrPct = (atr != null && last?.c) ? (atr / last.c) : 0;
  const riskPenalty = clamp((atrPct - 0.01) * 20, 0, 0.6); // starts penalizing after ~1%

  const score = clamp(0.5 * trend + 0.3 * mom + 0.2 * participation, -1, 1);

  const verdict = score > 0.25 ? "BUY" : score < -0.25 ? "SELL" : "HOLD";

  const aligned = [
    Math.sign(trend) === Math.sign(score) ? 1 : 0,
    Math.sign(mom) === Math.sign(score) ? 1 : 0,
    Math.sign(participation) === Math.sign(score) ? 1 : 0,
  ];
  const agreement = aligned.reduce((a,b)=>a+b,0) / aligned.length;

  const dataQuality = 1; // MVP: assume OK if we got numbers; extend later
  const rawConf = 0.55 * Math.abs(score) + 0.25 * agreement + 0.20 * dataQuality;
  const confidence = Math.round(100 * clamp(rawConf * (1 - riskPenalty), 0, 1));

  return {
    score,
    verdict,
    confidence,
    signals: {
      ret_1d: ret1d,
      ret_5d: ret5d,
      ema20,
      ema50,
      ema_spread: emaSpread,
      atr14: atr,
      atr_pct: atrPct,
      vol_z60: volZ,
      oi_z60: oiZ,
      trend,
      momentum: mom,
      participation
    }
  };
}

/**
 * Usage:
 *   node tools/update_mcx.js --file ./bhavcopy.csv --date 2026-02-18
 *   node tools/update_mcx.js --url  https://.../bhavcopy.csv --date 2026-02-18
 */
(async function main(){
  const args = process.argv.slice(2);
  const getArg = (k) => {
    const i = args.indexOf(k);
    if (i >= 0) return args[i+1];
    return null;
  };

  const file = getArg("--file");
  const url  = getArg("--url");
  const date = getArg("--date") || new Date().toISOString().slice(0,10);

  if (!file && !url) {
    console.log("Missing input. Provide --file <path> OR --url <download-url>");
    process.exit(1);
  }

  let inputPath = file;
  if (url) {
    inputPath = path.join(__dirname, "..", "data", "raw", `mcx_bhavcopy_${date}.csv`);
    await downloadToFile(url, inputPath);
  }

  const text = readTextFile(inputPath);
  const rows = parseCsvLoose(text);
  const goldRow = extractGoldRow(rows);
  if (!goldRow) {
    console.error("Could not find GOLD row. Paste the CSV header line and one GOLD row and I will map it.");
    process.exit(2);
  }

  const bar = mapRowToBar(goldRow);
  if (![bar.o,bar.h,bar.l,bar.c].every(n => typeof n === "number")) {
    console.error("Parsed GOLD row but OHLC not numeric. Likely header mapping mismatch.");
    console.error("Parsed:", bar);
    process.exit(3);
  }

  const history = loadHistory();
  // prevent duplicate date insert
  const exists = history.some(x => x.date === date);
  if (!exists) {
    const record = {
      date,
      exchange: "MCX",
      instrument: "GOLD",
      expiry: bar.expiry || null,
      o: bar.o, h: bar.h, l: bar.l, c: bar.c,
      prev_close: bar.pc,
      volume: bar.vol,
      value: bar.val,
      open_interest: bar.oi,
      updated: new Date().toISOString(),
      source: file ? "MCX_BHAVCOPY_FILE" : "MCX_BHAVCOPY_URL"
    };
    appendHistory(record);
    history.push(record);
  }

  // sort by date to ensure indicators correct
  history.sort((a,b)=> String(a.date).localeCompare(String(b.date)));

  const calc = computeScore(history);
  const latest = history[history.length - 1];

  const latestJson = {
    date: latest.date,
    exchange: latest.exchange,
    instrument: latest.instrument,
    expiry: latest.expiry,
    ohlc: { o: latest.o, h: latest.h, l: latest.l, c: latest.c },
    prev_close: latest.prev_close,
    volume: latest.volume,
    value: latest.value,
    open_interest: latest.open_interest,
    signals: calc.signals,
    score: calc.score,
    verdict: calc.verdict,
    confidence: calc.confidence,
    updated: new Date().toISOString(),
    source: "MCX_BHAVCOPY"
  };

  saveLatest(latestJson);
  console.log("Updated:", LATEST_PATH);
  console.log(latestJson);
})();
