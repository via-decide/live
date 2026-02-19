const express = require("express");
const cors = require("cors");
const path = require("path");
const fs = require("fs");
const multer = require("multer");
const { execFile } = require("child_process");

const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb" }));

const PORT = process.env.PORT || 3000;

// --- Paths ---
const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "public");
const DATA_DIR = path.join(ROOT, "data");
const RAW_DIR = path.join(DATA_DIR, "raw");
const TOOLS_DIR = path.join(ROOT, "tools");

const UPDATE_TOOL = path.join(TOOLS_DIR, "update_mcx.js");
const LATEST_JSON = path.join(DATA_DIR, "mcx_gold_latest.json");

// Ensure dirs exist
fs.mkdirSync(PUBLIC_DIR, { recursive: true });
fs.mkdirSync(DATA_DIR, { recursive: true });
fs.mkdirSync(RAW_DIR, { recursive: true });

// --- Multer upload config ---
const upload = multer({
  dest: RAW_DIR,
  limits: {
    fileSize: 20 * 1024 * 1024, // 20MB
  },
  fileFilter: (req, file, cb) => {
    const ok =
      file.mimetype === "text/csv" ||
      file.mimetype === "application/vnd.ms-excel" ||
      file.originalname.toLowerCase().endsWith(".csv");
    cb(ok ? null : new Error("Only CSV files allowed"), ok);
  }
});

// --- Serve frontend ---
app.use("/", express.static(PUBLIC_DIR));

// Helper: read latest JSON safely
function readLatestJson() {
  try {
    if (!fs.existsSync(LATEST_JSON)) return null;
    const raw = fs.readFileSync(LATEST_JSON, "utf8");
    return JSON.parse(raw);
  } catch (e) {
    return { _parseError: String(e?.message || e) };
  }
}

// --- Dashboard API ---
app.get("/prices", (req, res) => {
  const latest = readLatestJson();

  const mcxGold = latest && !latest._parseError ? {
    price: latest?.ohlc?.c ?? null,
    unit: "INR (MCX close)",
    source: "MCX Bhavcopy",
    updated: latest?.updated ?? null,
    score: latest?.score ?? null,
    verdict: latest?.verdict ?? null,
    confidence: latest?.confidence ?? null
  } : {
    price: null,
    unit: "INR (MCX close)",
    source: "MCX Bhavcopy",
    updated: null,
    score: null,
    verdict: null,
    confidence: null
  };

  res.json({
    ok: !!(latest && !latest._parseError),
    providerRefreshMs: 86400000, // daily EOD
    err: !latest
      ? "No data yet. Upload a CSV in /admin.html"
      : (latest._parseError ? `Latest JSON parse error: ${latest._parseError}` : null),
    data: {
      instruments: {
        // placeholders (optional)
        gold:   { price: null, unit: "—", source: "—", updated: null },
        silver: { price: null, unit: "—", source: "—", updated: null },
        copper: { price: null, unit: "—", source: "—", updated: null },
        zinc:   { price: null, unit: "—", source: "—", updated: null },
        crude:  { price: null, unit: "—", source: "—", updated: null },

        // ✅ MCX GOLD output
        mcx_gold: mcxGold
      }
    }
  });
});

// --- Admin upload route ---
app.post("/admin/upload", upload.single("file"), (req, res) => {
  try {
    if (!req.file) return res.status(400).send("No file uploaded (field name must be 'file').");

    // If admin posts a date, accept it; else today.
    // Note: if file is for earlier date, pass it explicitly from admin UI later.
    const date = (req.body && req.body.date) ? String(req.body.date) : new Date().toISOString().slice(0, 10);
    const filePath = req.file.path;

    // Run updater tool
    const args = [UPDATE_TOOL, "--file", filePath, "--date", date];

    execFile("node", args, { timeout: 120000 }, (err, stdout, stderr) => {
      if (err) {
        const msg = `UPDATE FAILED\n\n${stderr || err.message || err}\n\nSTDOUT:\n${stdout || ""}`;
        return res.status(500).type("text/plain").send(msg);
      }
      const msg = `UPDATE OK\n\n${stdout || ""}`;
      return res.type("text/plain").send(msg);
    });
  } catch (e) {
    return res.status(500).type("text/plain").send(String(e?.message || e));
  }
});

// Optional: quick health check
app.get("/health", (req, res) => {
  res.json({ ok: true, time: new Date().toISOString() });
});

app.listen(PORT, () => {
  console.log(`Server running: http://127.0.0.1:${PORT}`);
  console.log(`Dashboard:      http://127.0.0.1:${PORT}/`);
  console.log(`Admin:          http://127.0.0.1:${PORT}/admin.html`);
  console.log(`API:            http://127.0.0.1:${PORT}/prices`);
});


app.use(cors({
  origin: [
    "https://via-decide.github.io"
  ],
  methods: ["GET", "POST", "OPTIONS"],
  allowedHeaders: ["Content-Type"]
}));
