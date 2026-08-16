"""Production source adapter entrypoint.

Default source is the official MCX Bhav Copy endpoint. A repository file/URL override
remains available only for controlled testing or a future licensed feed.
"""
from __future__ import annotations
import csv, io, json, os, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .mcx_bhavcopy import acquire_latest

ALIASES = {
    "commodity": ("commodity","symbol","name"), "instrument": ("instrument","contract","instrument_name"),
    "ltp": ("ltp","last","last_price","close","price"), "breakout_level": ("breakout_level","breakout","buy_trigger"),
    "breakdown_level": ("breakdown_level","breakdown","sell_trigger"), "buy_invalidation_below": ("buy_invalidation_below","buy_invalidation","buy_stop","stop_below"),
    "sell_invalidation_above": ("sell_invalidation_above","sell_invalidation","sell_stop","stop_above"), "atr": ("atr","atr14"),
    "trend_bias": ("trend_bias","trend","bias"), "source_timestamp": ("source_timestamp","timestamp","updated_at","as_of"),
    "verified": ("verified","verification_status","is_verified"),
}
@dataclass
class AdapterResult:
    ok: bool
    records: list[dict]
    diagnostics: dict

def _first(row, keys):
    low={str(k).strip().lower(): v for k,v in row.items()}
    for k in keys:
        if k in low and low[k] not in (None,""): return low[k]
    return None

def _verified(v):
    if isinstance(v,bool): return v
    return str(v or "").strip().lower() in {"1","true","yes","verified","ok","pass"}

def normalize(row):
    out={k:_first(row,v) for k,v in ALIASES.items()}; out["commodity"]=str(out["commodity"] or "").strip().upper(); out["instrument"]=str(out["instrument"] or "").strip(); out["trend_bias"]=str(out["trend_bias"] or "").strip().upper(); out["verified"]=_verified(out["verified"]); return out

def _parse_payload(text, content_type, source_name):
    c=(content_type or "").lower()
    if "json" in c or source_name.lower().endswith(".json"):
        obj=json.loads(text); rows=(obj.get("records") or obj.get("data") or obj.get("commodities")) if isinstance(obj,dict) else obj
        if not isinstance(rows,list): raise ValueError("JSON payload must contain a list of records")
        return [normalize(x) for x in rows if isinstance(x,dict)]
    if "csv" in c or source_name.lower().endswith(".csv") or "text/plain" in c: return [normalize(r) for r in csv.DictReader(io.StringIO(text))]
    raise ValueError(f"unsupported content type: {content_type or 'unknown'}")

def _override(repo_root: Path, url: str, path: str) -> AdapterResult:
    d={"adapter":"bounded-override-v1","source_type":None,"source":None,"http_status":None,"content_type":None,"fetched_at":datetime.now(timezone.utc).isoformat(),"errors":[]}
    try:
        if url:
            d.update(source_type="url",source=url); req=urllib.request.Request(url,headers={"User-Agent":"CommodityVerdict/1.0","Accept":"application/json,text/csv,text/plain"})
            with urllib.request.urlopen(req,timeout=20) as r: d["http_status"]=getattr(r,"status",200); d["content_type"]=r.headers.get_content_type(); text=r.read(2_000_001).decode("utf-8")
            if len(text)>2_000_000: raise ValueError("payload exceeds 2 MB")
            records=_parse_payload(text,d["content_type"],url)
        else:
            p=(repo_root/path).resolve() if not Path(path).is_absolute() else Path(path); d.update(source_type="file",source=str(p)); text=p.read_text("utf-8"); c="application/json" if p.suffix.lower()==".json" else "text/csv"; d["content_type"]=c; records=_parse_payload(text,c,p.name)
        d["record_count"]=len(records); return AdapterResult(True,records,d)
    except Exception as e: d["errors"].append(f"{type(e).__name__}: {e}"); return AdapterResult(False,[],d)

def acquire(repo_root: Path) -> AdapterResult:
    url=os.getenv("COMMODITY_SOURCE_URL","").strip(); path=os.getenv("COMMODITY_SOURCE_PATH","").strip()
    if url or path: return _override(repo_root,url,path)
    records, diagnostics = acquire_latest(lookback_days=int(os.getenv("MCX_LOOKBACK_DAYS","8")))
    return AdapterResult(bool(records), records, diagnostics)
