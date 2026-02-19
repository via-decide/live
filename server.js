const express = require("express");
const cors = require("cors");
const path = require("path");
const fs = require("fs");
const { execFile } = require("child_process");

const app = express();
app.use(cors());
app.use(express.json({ limit: "1mb" }));

const PORT = process.env.PORT || 3000;

const DATA_DIR = path.join(__dirname, "data");
const LATEST_PATH = path.join(DATA_DIR, "mcx_gold_latest.json");

// Serve frontend
app.use("/", express.static(path.join(__dirname, "public")));

// Helper to read latest JSON safely
function readLatest() {
  try {
    if (!fs.existsSync(LATEST_PATH)) return null;
    return JSON.parse(fs.readFileSync(LATEST_PATH, "utf8"));
  } catch {
    return null;
  }
}

// API for dashboard
app.get("/prices", (req, res) => {
  const latest = readLatest();

  // Existing commodities can stay here if you want; MVP = MCX GOLD only.
  const instruments = {
    gold:   { price: null, unit: "—", source: "—", updated: null },
    silver: { price: null, unit: "—", source: "—", updated: null },
    copper: { price: null, unit: "—", source: "—", updated: null },
    zinc:   { price: null, unit: "—", source: "—", updated: null },
    crude:  { price: null, unit: "—", source: "—", updated: null },

    // ✅ MCX GOLD EOD instrument
    mcx_gold: latest ? {
      price: latest.ohlc?.c ?? null,
      unit: "INR (MCX close)",
      source: "MCX Bhavcopy",
      updated: latest.updated || null,

      // extra fields (frontend can display later)
      score: latest.score,
      verdict: latest.verdict,
      confidence: latest.confidence
    } : {
      price: null,
      unit: "INR (MCX close)",
      source: "MCX Bhavcopy",
      updated: null,
      score: null,
      verdict: null,
      confidence: null
    }
  };

  res.json({
    ok: true,
    providerRefreshMs: 86400000, // daily
    err: latest ? null : "No mcx_gold_latest.json yet. Run: node tools/update_mcx.js --file <bhavcopy.csv> --date YYYY-MM-DD",
    data: { instruments }
  });
});

// Manual trigger update (server runs the tool)
app.post("/update/mcx", (req, res) => {
  const { file, url, date } = req.body || {};
  if (!file && !url) {
    return res.status(400).json({ ok: false, err: "Provide {file} or {url}" });
  }

  const args = [];
  if (file) args.push("--file", file);
  if (url)  args.push("--url", url);
  if (date) args.push("--date", date);

  execFile("node", [path.join(__dirname, "tools", "update_mcx.js"), ...args], { timeout: 60000 }, (err, stdout, stderr) => {
    if (err) {
      return res.status(500).json({ ok: false, err: String(stderr || err.message || err), stdout });
    }
    return res.json({ ok: true, stdout, latest: readLatest() });
  });
});

app.listen(PORT, () => {
  console.log(`Server: http://127.0.0.1:${PORT}`);
  console.log(`Prices: http://127.0.0.1:${PORT}/prices`);
});
