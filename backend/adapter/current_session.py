"""Governed current-session MCX price acquisition.

This adapter does not calculate verdicts. It verifies a current-session price for the
exact contract already frozen by the EOD adapter. Economic Times is the nominated
raw price/timestamp source; ICICI Direct independently verifies the exact MCX FUTCOM
symbol/expiry and LTP from its active-contract table.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .mirror_sources import IST, TARGETS, _agree, _fetch, _text, _date_any, _num, fetch_icici_active, _contract_token

ET = "https://economictimes.indiatimes.com/commoditysummary/symbol-{symbol}.cms?expiry={expiry}"
DISPLAY = {"GOLD":"GOLD","SILVER":"SILVER","CRUDEOIL":"CRUDE","ZINC":"ZINC","COPPER":"COPPER"}


def _date(v):
    parsed=_date_any(v)
    if parsed: return parsed
    s=" ".join(str(v).replace(","," ").split())
    for fmt in ("%d %b %y","%d %B %y","%d %b %Y","%d %B %Y","%Y-%m-%d"):
        try: return datetime.strptime(s.title(),fmt).date()
        except ValueError: pass
    return None


def _expiry_from_instrument(instrument):
    m=re.search(r"\((\d{2}[A-Z]{3}\d{4})\)",str(instrument or "").upper())
    if not m: return None
    try: return datetime.strptime(m.group(1),"%d%b%Y").date()
    except ValueError: return None


def _fresh_current(ts, now, max_age_min):
    local=ts.astimezone(IST); now_local=now.astimezone(IST)
    if local.date()!=now_local.date(): raise ValueError(f"observation date {local.date()} is not current IST session {now_local.date()}")
    age=(now-ts.astimezone(timezone.utc)).total_seconds()/60
    if age < -10 or age > max_age_min: raise ValueError(f"observation stale/future: {age:.1f} min")
    return round(age,2)


def fetch_et_current(symbol, expected_expiry, now, max_age_min=30):
    url=ET.format(symbol=symbol,expiry=expected_expiry.isoformat()); raw,status,ctype=_fetch(url); txt=_text(raw)
    iso=re.escape(expected_expiry.isoformat())
    # Exact contract identity is validated independently of cosmetic page layout.
    identity=(re.search(rf"\b{re.escape(symbol)}\s+Contract Details\s*\({iso}\)\s*Exchange:\s*MCX\b",txt,re.I)
              or (symbol in txt.upper() and expected_expiry.isoformat() in txt and re.search(r"\bExchange:\s*MCX\b",txt,re.I)))
    if not identity: raise ValueError("ET exact MCX contract identity not found")
    human=expected_expiry.strftime("%d-%b-%Y")
    if not re.search(rf"Expiry:\s*{re.escape(human)}\s*\|\s*Exchange:\s*MCX",txt,re.I):
        raise ValueError("ET exact expiry header not found")
    tm=re.search(r"\b(\d{1,2})[\.:](\d{2})(AM|PM)\s+IST\s*\|\s*(\d{1,2}\s+[A-Za-z]+,?\s+\d{2,4})",txt,re.I)
    if not tm: raise ValueError("ET current timestamp not found")
    hh=int(tm.group(1)); mm=int(tm.group(2)); ap=tm.group(3).upper(); hh=(hh%12)+(12 if ap=="PM" else 0)
    d=_date(tm.group(4));
    if d is None: raise ValueError("ET current date malformed")
    ts=datetime(d.year,d.month,d.day,hh,mm,tzinfo=IST)
    tail=txt[tm.end():]
    pm=re.search(r"([0-9][0-9,]*(?:\.\d+)?)\s+Per\b",tail,re.I)
    if not pm: raise ValueError("ET current price not found")
    price=_num(pm.group(1)); age=_fresh_current(ts,now,max_age_min)
    return price,ts,{"url":url,"http_status":status,"content_type":ctype,"expiry":expected_expiry.isoformat(),"age_minutes":age}


def acquire_current(eod_records, now=None, max_age_min=30):
    now=now or datetime.now(timezone.utc); local=now.astimezone(IST)
    diagnostics={"adapter":"mcx-current-session-mirrors-v1","mode":"exact_frozen_contract_current_price_reconciliation","providers":["Economic Times","ICICI Direct"],"nominated_price_source":"Economic Times","fetched_at":now.isoformat(),"session_date":local.date().isoformat(),"verified_count":0,"commodities":{},"errors":[]}
    if local.weekday()>=5 or local.hour<9 or (local.hour==23 and local.minute>30) or local.hour>23:
        diagnostics["errors"].append("outside governed MCX current-session window")
        return [],diagnostics
    try:
        active,active_diag=fetch_icici_active(); diagnostics["icici_active"]=active_diag
    except Exception as exc:
        active={}; diagnostics["errors"].append(f"ICICI active table: {type(exc).__name__}: {exc}")
    by_symbol={}
    for r in eod_records:
        source_symbol=str(r.get("source_symbol") or r.get("commodity") or "").upper()
        if source_symbol=="CRUDE": source_symbol="CRUDEOIL"
        if source_symbol in TARGETS: by_symbol[source_symbol]=r
    out=[]
    for symbol in TARGETS:
        d={"status":"NOT_RECOMMEND","checks":{},"sources":{}}
        try:
            eod=by_symbol.get(symbol)
            if not eod or not eod.get("verified"): raise ValueError("verified frozen EOD contract unavailable")
            expiry=_expiry_from_instrument(eod.get("instrument"))
            if expiry is None: raise ValueError("frozen EOD instrument missing exact expiry")
            token=_contract_token(symbol,expiry); ir=active.get(token)
            if not ir or ir.get("ltp") is None: raise ValueError(f"ICICI exact contract {token} not present")
            et,ets,ed=fetch_et_current(symbol,expiry,now,max_age_min)
            icici=float(ir["ltp"]); ok,meta=_agree(symbol,et,icici); d["checks"]["price"]={"ok":ok,**(meta or {})}
            d["sources"]={"economic_times":{"price":et,"timestamp":ets.isoformat(),**ed},"icici":{"price":icici,"contract":token,"active_url":diagnostics.get("icici_active",{}).get("url")}}
            if not ok: raise ValueError("current prices disagree beyond tolerance")
            display=DISPLAY[symbol]
            obs={"commodity":display,"instrument":str(eod.get("instrument")),"price":et,"timestamp":ets.isoformat(),"verified":True,"verification_tier":"CROSS_SOURCE_CURRENT_VERIFIED","source_symbol":symbol,"nominated_source":"Economic Times"}
            out.append(obs); d.update(status="CROSS_SOURCE_CURRENT_VERIFIED",expiry=expiry.isoformat(),price=et,timestamp=ets.isoformat(),nominated_source="Economic Times"); diagnostics["verified_count"]+=1
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{symbol}: {d['error']}")
        diagnostics["commodities"][symbol]=d
    diagnostics["record_count"]=len(out)
    return out,diagnostics
