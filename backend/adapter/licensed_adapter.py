"""Production reconciliation for documented Upstox + DhanHQ MCX APIs."""
from __future__ import annotations
import csv, io, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from . import licensed_sources as _licensed_sources
_licensed_sources.DHAN_MASTER="https://images.dhan.co/api-data/api-scrip-master.csv"

def _fetch_dhan_master_bounded():
    req=urllib.request.Request(_licensed_sources.DHAN_MASTER,headers={"Accept":"text/csv,*/*"})
    with urllib.request.urlopen(req,timeout=25) as r:
        status=int(getattr(r,"status",200)); ctype=str(r.headers.get("Content-Type","")); raw=r.read(50_000_001)
    if status!=200: raise ValueError(f"HTTP {status}")
    if len(raw)>50_000_000: raise ValueError("Dhan instrument master exceeds 50 MB")
    rows=list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig","replace"))))
    if not rows: raise ValueError("Dhan instrument master empty")
    return rows,{"url":_licensed_sources.DHAN_MASTER,"http_status":status,"content_type":ctype,"record_count":len(rows)}

_licensed_sources.fetch_dhan_master=_fetch_dhan_master_bounded
from .licensed_sources import acquire_latest, acquire_mirrors, acquire_historical

TARGETS=("GOLD","SILVER","CRUDE","ZINC","COPPER")

@dataclass
class AdapterResult:
    ok: bool
    records: list[dict]
    diagnostics: dict


def _expiry(instrument):
    s=str(instrument or "").upper(); a=s.rfind("("); b=s.rfind(")")
    return s[a+1:b] if a>=0 and b>a else None


def _near(a,b):
    try:
        a=float(a); b=float(b); return abs(a-b)<=max(1e-9,max(abs(a),abs(b))*0.0015)
    except (TypeError,ValueError): return False


def _reconcile(primary_records,secondary_records,primary_diag,secondary_diag):
    primary={r.get("commodity"):r for r in primary_records}; secondary={r.get("commodity"):r for r in secondary_records}; out=[]; per={}
    for commodity in TARGETS:
        a=primary.get(commodity); b=secondary.get(commodity)
        if not a or not b:
            per[commodity]={"status":"NOT_RECOMMEND","reason":"both licensed sources required","upstox":bool(a),"dhan":bool(b)}; continue
        same_expiry=_expiry(a.get("instrument"))==_expiry(b.get("instrument")); same_date=a.get("source_trade_date")==b.get("source_trade_date"); price_ok=_near(a.get("ltp"),b.get("ltp"))
        if not (same_expiry and same_date and price_ok):
            per[commodity]={"status":"NOT_RECOMMEND","reason":"licensed sources disagree","same_expiry":same_expiry,"same_date":same_date,"price_ok":price_ok}; continue
        chosen=dict(a); chosen["verified"]=True; chosen["verification_tier"]="CROSS_SOURCE_VERIFIED"; out.append(chosen)
        per[commodity]={"status":"CROSS_SOURCE_VERIFIED","source":"Upstox+DhanHQ","expiry":_expiry(chosen.get("instrument")),"trade_date":chosen.get("source_trade_date")}
    diagnostics={"adapter":"mcx-multi-source-v1","source_type":"licensed_broker_api_cross_verification","source":"Upstox -> DhanHQ","providers":["Upstox","DhanHQ"],"fetched_at":datetime.now(timezone.utc).isoformat(),"mcx":primary_diag,"mirrors":secondary_diag,"commodities":per,"verified_count":len(out),"record_count":len(out),"errors":[]}
    return out,diagnostics


def acquire(repo_root=None):
    try: primary,pdiag=acquire_latest()
    except Exception as exc: primary=[]; pdiag={"adapter":"mcx-upstox-api-v1","errors":[f"{type(exc).__name__}: {exc}"]}
    try: secondary,sdiag=acquire_mirrors()
    except Exception as exc: secondary=[]; sdiag={"adapter":"mcx-dhan-api-v1","errors":[f"{type(exc).__name__}: {exc}"]}
    records,diagnostics=_reconcile(primary,secondary,pdiag,sdiag)
    return AdapterResult(bool(records),records,diagnostics)
