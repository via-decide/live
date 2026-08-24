"""Production source adapter entrypoint.

Acquisition order:
1. Official MCX Bhav Copy.
2. Established independent mirrors (5paisa + ICICI Direct) for diagnostic evidence.
3. Governed historical mirrors (Upstox + Economic Times) for the completed EOD
   session and front-contract identity when official MCX is unavailable.
4. Cross-source reconciliation per exact commodity/expiry.

The completed-session historical pair is authoritative for mirror-only production
because it binds the exact trade date and the active full-size contract. A primary
5paisa/ICICI snapshot may not override it with a farther expiry. Missing or
conflicting commodities remain fail-closed.
"""
from __future__ import annotations
import csv, io, json, os, re, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .mcx_bhavcopy import acquire_latest
from .mirror_sources import acquire_mirrors
from .historical_mirrors import acquire_historical

ALIASES = {
    "commodity": ("commodity","symbol","name"), "instrument": ("instrument","contract","instrument_name"),
    "ltp": ("ltp","last","last_price","close","price"), "breakout_level": ("breakout_level","breakout","buy_trigger"),
    "breakdown_level": ("breakdown_level","breakdown","sell_trigger"), "buy_invalidation_below": ("buy_invalidation_below","buy_invalidation","buy_stop","stop_below"),
    "sell_invalidation_above": ("sell_invalidation_above","sell_invalidation","sell_stop","stop_above"), "atr": ("atr","atr14"),
    "trend_bias": ("trend_bias","trend","bias"), "source_timestamp": ("source_timestamp","timestamp","updated_at","as_of"),
    "verified": ("verified","verification_status","is_verified"),
}
TARGETS=("GOLD","SILVER","CRUDE","ZINC","COPPER")

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

def _expiry(instrument):
    m=re.search(r"\((\d{2}[A-Z]{3}\d{4})\)",str(instrument or "").upper())
    return m.group(1) if m else None

def _near(a,b):
    try:
        a=float(a); b=float(b); return abs(a-b)<=max(1e-9,max(abs(a),abs(b))*0.0015)
    except (TypeError,ValueError): return False

def _reconcile(mcx_records, mirror_records, mcx_diag, mirror_diag):
    mcx={r.get("commodity"):r for r in mcx_records}; mirrors={r.get("commodity"):r for r in mirror_records}; out=[]; per={}
    mirror_name="Upstox+EconomicTimes" if mirror_diag.get("adapter")=="mcx-historical-mirror-v1" else "5paisa+ICICI"
    for commodity in TARGETS:
        a=mcx.get(commodity); b=mirrors.get(commodity)
        if not b:
            per[commodity]={"status":"NOT_RECOMMEND","reason":"independent mirror verification unavailable"}; continue
        if a:
            same_expiry=_expiry(a.get("instrument"))==_expiry(b.get("instrument")); same_date=a.get("source_trade_date")==b.get("source_trade_date"); price_ok=_near(a.get("ltp"),b.get("ltp"))
            if not (same_expiry and same_date and price_ok):
                per[commodity]={"status":"NOT_RECOMMEND","reason":"MCX and mirrors disagree","same_expiry":same_expiry,"same_date":same_date,"price_ok":price_ok}; continue
            chosen=dict(a); chosen["verification_tier"]="MCX_PLUS_MIRRORS"; chosen["verified"]=True; out.append(chosen)
            per[commodity]={"status":"MCX_PLUS_MIRRORS","source":"MCX","expiry":_expiry(chosen.get("instrument")),"trade_date":chosen.get("source_trade_date")}
        else:
            chosen=dict(b); chosen["verification_tier"]="CROSS_SOURCE_VERIFIED"; chosen["verified"]=True; out.append(chosen)
            per[commodity]={"status":"CROSS_SOURCE_VERIFIED","source":mirror_name,"expiry":_expiry(chosen.get("instrument")),"trade_date":chosen.get("source_trade_date")}
    return out,{"adapter":"mcx-multi-source-v1","source_type":"mcx_then_independent_mirrors","source":f"MCX -> {mirror_name}","fetched_at":datetime.now(timezone.utc).isoformat(),"mcx":mcx_diag,"mirrors":mirror_diag,"commodities":per,"verified_count":len(out),"record_count":len(out),"errors":[]}

def _expiry_map(records):
    return {r.get("commodity"): _expiry(r.get("instrument")) for r in records}

def acquire(repo_root: Path) -> AdapterResult:
    url=os.getenv("COMMODITY_SOURCE_URL","").strip(); path=os.getenv("COMMODITY_SOURCE_PATH","").strip()
    if url or path: return _override(repo_root,url,path)
    lookback=int(os.getenv("MCX_LOOKBACK_DAYS","8"))
    try: mcx_records,mcx_diag=acquire_latest(lookback_days=lookback)
    except Exception as exc: mcx_records=[]; mcx_diag={"adapter":"mcx-official-bhavcopy-v1","errors":[f"{type(exc).__name__}: {exc}"]}

    try: primary_records,primary_diag=acquire_mirrors()
    except Exception as exc: primary_records=[]; primary_diag={"adapter":"mcx-multi-source-v1","errors":[f"{type(exc).__name__}: {exc}"],"verified_count":0}

    # When official MCX is unavailable, completed-session historical mirrors are
    # mandatory. This prevents a current snapshot provider from nominating a farther
    # contract even when the governance contract is the nearest liquid unexpired one.
    if not mcx_records:
        try:
            mirror_records,mirror_diag=acquire_historical()
            mirror_diag["primary_mirror_attempt"] = primary_diag
            mirror_diag["primary_expiries"] = _expiry_map(primary_records)
            mirror_diag["historical_front_expiries"] = _expiry_map(mirror_records)
            if len(mirror_records) != len(TARGETS):
                # Partial historical verification is not a reason to discard verified
                # commodities: _reconcile() already fail-closes any commodity missing
                # from mirror_records to NOT_RECOMMEND below. Voiding the whole batch
                # here previously turned a single missing commodity into a total
                # publication outage.
                mirror_diag.setdefault("errors",[]).append(
                    f"front-contract historical gate: {len(mirror_records)}/{len(TARGETS)} verified; missing commodities fail-closed individually"
                )
        except Exception as exc:
            mirror_records=[]; mirror_diag={"adapter":"mcx-historical-mirror-v1","errors":[f"{type(exc).__name__}: {exc}"],"verified_count":0,"primary_mirror_attempt":primary_diag}
    else:
        # Official MCX remains authority; mirrors are only an independent check.
        mirror_records,mirror_diag=primary_records,primary_diag
        if not mirror_records:
            try:
                mirror_records,mirror_diag=acquire_historical(); mirror_diag["primary_mirror_attempt"]=primary_diag
            except Exception as exc:
                mirror_records=[]; mirror_diag={"adapter":"mcx-historical-mirror-v1","errors":[f"{type(exc).__name__}: {exc}"],"verified_count":0,"primary_mirror_attempt":primary_diag}

    records,diagnostics=_reconcile(mcx_records,mirror_records,mcx_diag,mirror_diag)
    return AdapterResult(bool(records),records,diagnostics)
