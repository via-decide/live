"""Bounded source adapter for Commodity Verdict."""
from __future__ import annotations
import csv, io, json, os, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ALIASES = {
    "commodity": ("commodity","symbol","name"),
    "instrument": ("instrument","contract","instrument_name"),
    "ltp": ("ltp","last","last_price","close","price"),
    "breakout_level": ("breakout_level","breakout","buy_trigger"),
    "breakdown_level": ("breakdown_level","breakdown","sell_trigger"),
    "buy_invalidation_below": ("buy_invalidation_below","buy_invalidation","buy_stop","stop_below"),
    "sell_invalidation_above": ("sell_invalidation_above","sell_invalidation","sell_stop","stop_above"),
    "atr": ("atr","atr14"), "trend_bias": ("trend_bias","trend","bias"),
    "source_timestamp": ("source_timestamp","timestamp","updated_at","as_of"),
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
    out={k:_first(row,v) for k,v in ALIASES.items()}
    out["commodity"]=str(out["commodity"] or "").strip().upper()
    out["instrument"]=str(out["instrument"] or "").strip()
    out["trend_bias"]=str(out["trend_bias"] or "").strip().upper()
    out["verified"]=_verified(out["verified"])
    return out

def _parse_payload(text, content_type, source_name):
    c=(content_type or "").lower()
    if "json" in c or source_name.lower().endswith(".json"):
        obj=json.loads(text)
        if isinstance(obj,dict):
            rows=obj.get("records") or obj.get("data") or obj.get("commodities")
            if rows is None and all(isinstance(v,dict) for v in obj.values()): rows=[dict(v, commodity=k) for k,v in obj.items()]
        else: rows=obj
        if not isinstance(rows,list): raise ValueError("JSON payload must contain a list of records")
        return [normalize(x) for x in rows if isinstance(x,dict)]
    if "csv" in c or source_name.lower().endswith(".csv") or "text/plain" in c:
        return [normalize(r) for r in csv.DictReader(io.StringIO(text))]
    raise ValueError(f"unsupported content type: {content_type or 'unknown'}")

def acquire(repo_root: Path) -> AdapterResult:
    url=os.getenv("COMMODITY_SOURCE_URL","").strip(); path=os.getenv("COMMODITY_SOURCE_PATH","").strip()
    diagnostics={"adapter":"bounded-v1","source_type":None,"source":None,"http_status":None,"content_type":None,
                 "fetched_at":datetime.now(timezone.utc).isoformat(),"errors":[]}
    try:
        if url:
            diagnostics.update(source_type="url",source=url)
            headers={"User-Agent":"CommodityVerdict/1.0","Accept":"application/json,text/csv,text/plain"}
            auth=os.getenv("COMMODITY_SOURCE_AUTH","").strip()
            if auth: headers["Authorization"]=auth
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=20) as r:
                diagnostics["http_status"]=getattr(r,"status",200); diagnostics["content_type"]=r.headers.get_content_type()
                if diagnostics["http_status"] != 200: raise ValueError(f"HTTP {diagnostics['http_status']}")
                text=r.read(2_000_001).decode("utf-8")
                if len(text)>2_000_000: raise ValueError("payload exceeds 2 MB")
                records=_parse_payload(text,diagnostics["content_type"],url)
        elif path:
            p=(repo_root/path).resolve() if not Path(path).is_absolute() else Path(path)
            diagnostics.update(source_type="file",source=str(p))
            if not p.exists(): raise FileNotFoundError(str(p))
            text=p.read_text("utf-8"); c="application/json" if p.suffix.lower()==".json" else "text/csv"
            diagnostics["content_type"]=c; records=_parse_payload(text,c,p.name)
        else: raise ValueError("no COMMODITY_SOURCE_URL or COMMODITY_SOURCE_PATH configured")
        diagnostics["record_count"]=len(records)
        required=("commodity","instrument","ltp","source_timestamp","verified"); bad=[]
        for i,r in enumerate(records):
            missing=[k for k in required if r.get(k) in (None,"")]
            if missing: bad.append({"index":i,"missing":missing})
        diagnostics["required_field_issues"]=bad
        return AdapterResult(True,records,diagnostics)
    except Exception as e:
        diagnostics["errors"].append(f"{type(e).__name__}: {e}")
        return AdapterResult(False,[],diagnostics)
