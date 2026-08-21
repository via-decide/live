"""Governed current-session MCX price acquisition.

This adapter does not calculate verdicts. It verifies a current-session price for the
exact contract already frozen by the EOD adapter. Upstox and Economic Times must
independently identify the same MCX FUTCOM expiry, expose a current-IST-session
observation timestamp, and agree on price within the existing mirror tolerances.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .mirror_sources import IST, TARGETS, _agree, _fetch, _text, _date_any, _num

UPSTOX = {
    "GOLD": "https://upstox.com/commodity-market-trading/mcx-gold-price/",
    "SILVER": "https://upstox.com/commodity-market-trading/mcx-silver-price/",
    "CRUDEOIL": "https://upstox.com/commodity-market-trading/mcx-crudeoil-price/",
    "ZINC": "https://upstox.com/commodity-market-trading/mcx-zinc-price/",
    "COPPER": "https://upstox.com/commodity-market-trading/mcx-copper-price/",
}
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


def fetch_upstox_current(symbol, expected_expiry, now, max_age_min=30):
    url=UPSTOX[symbol]; raw,status,ctype=_fetch(url); txt=_text(raw)
    em=re.search(r"\bExpiry:\s*(\d{1,2}\s+[A-Za-z]+,?\s+\d{2,4})",txt,re.I)
    if not em: raise ValueError("Upstox active expiry not found")
    expiry=_date(em.group(1))
    if expiry!=expected_expiry: raise ValueError(f"Upstox active expiry {expiry} != frozen {expected_expiry}")
    pm=re.search(rf"\b{re.escape(symbol)}\b\s+Expiry:.*?₹\s*([0-9][0-9,]*(?:\.\d+)?)",txt,re.I)
    if not pm: raise ValueError("Upstox current price not found")
    price=_num(pm.group(1))
    tm=re.search(r"Last updated on\s+(\d{1,2}\s+[A-Za-z]+,?\s+\d{2,4})\s*\|\s*(\d{1,2}:\d{2})\s*IST",txt,re.I)
    if not tm: raise ValueError("Upstox current timestamp not found")
    d=_date(tm.group(1)); hh,mm=map(int,tm.group(2).split(":")); ts=datetime(d.year,d.month,d.day,hh,mm,tzinfo=IST)
    age=_fresh_current(ts,now,max_age_min)
    return price,ts,{"url":url,"http_status":status,"content_type":ctype,"expiry":expiry.isoformat(),"age_minutes":age}


def fetch_et_current(symbol, expected_expiry, now, max_age_min=30):
    url=ET.format(symbol=symbol,expiry=expected_expiry.isoformat()); raw,status,ctype=_fetch(url); txt=_text(raw)
    iso=re.escape(expected_expiry.isoformat())
    if not re.search(rf"\b{re.escape(symbol)}\s+Contract Details\s*\({iso}\)\s*Exchange:\s*MCX\b",txt,re.I):
        raise ValueError("ET exact MCX contract identity not found")
    human=expected_expiry.strftime("%d-%b-%Y")
    if not re.search(rf"Expiry:\s*{re.escape(human)}\s*\|\s*Exchange:\s*MCX",txt,re.I):
        raise ValueError("ET exact expiry header not found")
    tm=re.search(r"\b(\d{1,2})[\.:](\d{2})(AM|PM)\s+IST\s*\|\s*(\d{1,2}\s+[A-Za-z]+,?\s+\d{2,4})",txt,re.I)
    if not tm: raise ValueError("ET current timestamp not found")
    hh=int(tm.group(1)); mm=int(tm.group(2)); ap=tm.group(3).upper(); hh=(hh%12)+(12 if ap=="PM" else 0)
    d=_date(tm.group(4)); ts=datetime(d.year,d.month,d.day,hh,mm,tzinfo=IST)
    tail=txt[tm.end():]
    pm=re.search(r"([0-9][0-9,]*(?:\.\d+)?)\s+Per\b",tail,re.I)
    if not pm: raise ValueError("ET current price not found")
    price=_num(pm.group(1)); age=_fresh_current(ts,now,max_age_min)
    return price,ts,{"url":url,"http_status":status,"content_type":ctype,"expiry":expected_expiry.isoformat(),"age_minutes":age}


def acquire_current(eod_records, now=None, max_age_min=30):
    now=now or datetime.now(timezone.utc); local=now.astimezone(IST)
    diagnostics={"adapter":"mcx-current-session-mirrors-v1","mode":"exact_frozen_contract_current_price_reconciliation","providers":["Upstox","Economic Times"],"fetched_at":now.isoformat(),"session_date":local.date().isoformat(),"verified_count":0,"commodities":{},"errors":[]}
    if local.weekday()>=5 or local.hour<9 or (local.hour==23 and local.minute>30) or local.hour>23:
        diagnostics["errors"].append("outside governed MCX current-session window")
        return [],diagnostics
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
            up,uts,ud=fetch_upstox_current(symbol,expiry,now,max_age_min)
            et,ets,ed=fetch_et_current(symbol,expiry,now,max_age_min)
            ok,meta=_agree(symbol,up,et); d["checks"]["price"]={"ok":ok,**(meta or {})}
            d["sources"]={"upstox":{"price":up,"timestamp":uts.isoformat(),**ud},"economic_times":{"price":et,"timestamp":ets.isoformat(),**ed}}
            if not ok: raise ValueError("current prices disagree beyond tolerance")
            observed_at=max(uts,ets); price=(up+et)/2.0
            display=DISPLAY[symbol]
            obs={"commodity":display,"instrument":str(eod.get("instrument")),"price":price,"timestamp":observed_at.isoformat(),"verified":True,"verification_tier":"CROSS_SOURCE_CURRENT_VERIFIED","source_symbol":symbol}
            out.append(obs); d.update(status="CROSS_SOURCE_CURRENT_VERIFIED",expiry=expiry.isoformat(),price=price,timestamp=observed_at.isoformat()); diagnostics["verified_count"]+=1
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{symbol}: {d['error']}")
        diagnostics["commodities"][symbol]=d
    diagnostics["record_count"]=len(out)
    return out,diagnostics
