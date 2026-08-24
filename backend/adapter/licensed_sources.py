"""Documented broker-API adapters for MCX futures market data.

Production data sources:
- Upstox Developer API (primary)
- DhanHQ v2 API (independent secondary)

No consumer HTML is parsed. Instrument identity comes from each broker's documented
instrument master; market data comes from authenticated JSON APIs. Missing credentials
or any source disagreement fail closed per commodity.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST=ZoneInfo("Asia/Kolkata")
TARGETS=("GOLD","SILVER","CRUDEOIL","ZINC","COPPER")
DISPLAY={"GOLD":"GOLD","SILVER":"SILVER","CRUDEOIL":"CRUDE","ZINC":"ZINC","COPPER":"COPPER"}
ABS_TOL={"GOLD":25.0,"SILVER":75.0,"CRUDEOIL":8.0,"ZINC":0.40,"COPPER":1.25}
REL_TOL=0.0015
UPSTOX_MASTER="https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
UPSTOX_QUOTE="https://api.upstox.com/v2/market-quote/quotes"
UPSTOX_HIST="https://api.upstox.com/v3/historical-candle/{instrument_key}/days/1/{to_date}/{from_date}"
DHAN_MASTER="https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_QUOTE="https://api.dhan.co/v2/marketfeed/ohlc"
DHAN_HIST="https://api.dhan.co/v2/charts/historical"


def _num(v):
    try:
        x=float(str(v).replace(",","").strip())
        return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None


def _agree(symbol,a,b):
    if a is None or b is None: return False,None
    delta=abs(float(a)-float(b)); tol=max(ABS_TOL[symbol],max(abs(float(a)),abs(float(b)))*REL_TOL)
    return delta<=tol,{"delta":round(delta,6),"tolerance":round(tol,6),"delta_percent":round(delta/max(abs(float(a)),1)*100,6)}


def _is_session_day(d): return d.weekday()<5

def _last_completed_session(local_now):
    d=local_now.date()-timedelta(days=1)
    while not _is_session_day(d): d-=timedelta(days=1)
    return d

def _session_timestamp(d): return datetime(d.year,d.month,d.day,23,30,tzinfo=IST)


def _request(url,headers=None,data=None,timeout=25):
    req=urllib.request.Request(url,data=data,headers=headers or {},method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req,timeout=timeout) as r:
        status=int(getattr(r,"status",200)); ctype=str(r.headers.get("Content-Type","")); raw=r.read(20_000_001)
    if status!=200: raise ValueError(f"HTTP {status}")
    if len(raw)>20_000_000: raise ValueError("payload exceeds 20 MB")
    return raw,status,ctype


def _json_request(url,headers=None,payload=None):
    h={"Accept":"application/json",**(headers or {})}; data=None
    if payload is not None:
        h["Content-Type"]="application/json"; data=json.dumps(payload,separators=(",",":")).encode()
    raw,status,ctype=_request(url,h,data)
    try: obj=json.loads(raw.decode("utf-8"))
    except Exception as exc: raise ValueError(f"non-JSON response: {exc}")
    return obj,{"url":url,"http_status":status,"content_type":ctype}


def _expiry_value(v):
    if v in (None,""): return None
    if isinstance(v,(int,float)):
        x=float(v); x=x/1000 if x>10_000_000_000 else x
        return datetime.fromtimestamp(x,tz=timezone.utc).astimezone(IST).date()
    s=str(v).strip()
    for fmt in ("%Y-%m-%d","%d-%b-%Y","%d %b %Y","%d%b%Y","%Y/%m/%d"):
        try: return datetime.strptime(s.upper(),fmt).date()
        except ValueError: pass
    return None


def _symbol_match(value,symbol):
    s=str(value or "").upper().replace(" ","").replace("-","")
    return s==symbol or s.startswith(symbol)


def fetch_upstox_master():
    raw,status,ctype=_request(UPSTOX_MASTER,{"Accept":"application/json,application/gzip"})
    try: rows=json.loads(gzip.decompress(raw).decode("utf-8"))
    except OSError: rows=json.loads(raw.decode("utf-8"))
    if not isinstance(rows,list): raise ValueError("Upstox instrument master is not a list")
    return rows,{"url":UPSTOX_MASTER,"http_status":status,"content_type":ctype,"record_count":len(rows)}


def _upstox_contracts(rows,symbol,on_date):
    out=[]
    for r in rows:
        if str(r.get("exchange","")).upper()!="MCX" and str(r.get("segment","")).upper()!="MCX_FO": continue
        typ=str(r.get("instrument_type","")).upper()
        if typ not in {"FUT","FUTCOM"}: continue
        if not any(_symbol_match(r.get(k),symbol) for k in ("name","short_name","underlying_symbol","trading_symbol")): continue
        exp=_expiry_value(r.get("expiry"))
        if exp is None or exp<on_date: continue
        out.append((exp,r))
    return sorted(out,key=lambda x:x[0])


def resolve_upstox(rows,symbol,expiry=None,on_date=None):
    on_date=on_date or datetime.now(IST).date(); candidates=_upstox_contracts(rows,symbol,on_date)
    if expiry is not None: candidates=[x for x in candidates if x[0]==expiry]
    if not candidates: raise ValueError(f"Upstox exact MCX FUTCOM contract unavailable for {symbol} {expiry or ''}".strip())
    exp,r=candidates[0]; key=str(r.get("instrument_key") or "")
    if not key: raise ValueError("Upstox instrument key missing")
    return {"symbol":symbol,"expiry":exp,"instrument_key":key,"trading_symbol":str(r.get("trading_symbol") or "")}


def fetch_dhan_master():
    raw,status,ctype=_request(DHAN_MASTER,{"Accept":"text/csv,*/*"})
    text=raw.decode("utf-8-sig","replace"); rows=list(csv.DictReader(io.StringIO(text)))
    if not rows: raise ValueError("Dhan instrument master empty")
    return rows,{"url":DHAN_MASTER,"http_status":status,"content_type":ctype,"record_count":len(rows)}


def _first(row,*keys):
    low={str(k).upper():v for k,v in row.items()}
    for k in keys:
        if k.upper() in low and low[k.upper()] not in (None,""): return low[k.upper()]
    return None


def resolve_dhan(rows,symbol,expiry=None,on_date=None):
    on_date=on_date or datetime.now(IST).date(); candidates=[]
    for r in rows:
        exch=str(_first(r,"EXCH_ID","SEM_EXM_EXCH_ID") or "").upper(); seg=str(_first(r,"SEGMENT","SEM_SEGMENT") or "").upper()
        inst=str(_first(r,"INSTRUMENT","SEM_INSTRUMENT_NAME","SEM_EXCH_INSTRUMENT_TYPE") or "").upper()
        if exch!="MCX" and seg not in {"M","MCX_COMM","MCX"}: continue
        if "FUTCOM" not in inst and inst not in {"FUT","FUTURE"}: continue
        name=_first(r,"SYMBOL_NAME","SM_SYMBOL_NAME","TRADING_SYMBOL","SEM_TRADING_SYMBOL","CUSTOM_SYMBOL","SEM_CUSTOM_SYMBOL")
        if not _symbol_match(name,symbol): continue
        exp=_expiry_value(_first(r,"EXPIRY_DATE","SEM_EXPIRY_DATE"))
        if exp is None or exp<on_date or (expiry is not None and exp!=expiry): continue
        sid=str(_first(r,"SECURITY_ID","SEM_SMST_SECURITY_ID") or "").strip()
        if sid: candidates.append((exp,sid,r))
    if not candidates: raise ValueError(f"Dhan exact MCX FUTCOM contract unavailable for {symbol} {expiry or ''}".strip())
    exp,sid,r=sorted(candidates,key=lambda x:x[0])[0]
    return {"symbol":symbol,"expiry":exp,"security_id":sid,"trading_symbol":str(_first(r,"TRADING_SYMBOL","SEM_TRADING_SYMBOL") or "")}


def _upstox_headers():
    token=os.getenv("UPSTOX_ACCESS_TOKEN","").strip()
    if not token: raise ValueError("missing UPSTOX_ACCESS_TOKEN")
    return {"Authorization":f"Bearer {token}"}


def _dhan_headers():
    token=os.getenv("DHAN_ACCESS_TOKEN","").strip(); client=os.getenv("DHAN_CLIENT_ID","").strip()
    if not token: raise ValueError("missing DHAN_ACCESS_TOKEN")
    if not client: raise ValueError("missing DHAN_CLIENT_ID")
    return {"access-token":token,"client-id":client}


def fetch_upstox_history(contract,from_date,to_date):
    key=urllib.parse.quote(contract["instrument_key"],safe=""); url=UPSTOX_HIST.format(instrument_key=key,to_date=to_date.isoformat(),from_date=from_date.isoformat())
    obj,diag=_json_request(url,_upstox_headers()); candles=((obj.get("data") or {}).get("candles") if isinstance(obj,dict) else None)
    if not isinstance(candles,list): raise ValueError("Upstox historical candles missing")
    return candles,diag


def fetch_dhan_history(contract,from_date,to_date):
    payload={"securityId":contract["security_id"],"exchangeSegment":"MCX_COMM","instrument":"FUTCOM","expiryCode":0,"oi":True,"fromDate":from_date.isoformat(),"toDate":to_date.isoformat()}
    obj,diag=_json_request(DHAN_HIST,_dhan_headers(),payload)
    if not isinstance(obj,dict) or not isinstance(obj.get("timestamp"),list): raise ValueError("Dhan historical candles missing")
    rows=[]
    for i,ts in enumerate(obj["timestamp"]):
        rows.append([datetime.fromtimestamp(float(ts),tz=timezone.utc).astimezone(IST).isoformat(),obj["open"][i],obj["high"][i],obj["low"][i],obj["close"][i],(obj.get("volume") or [None]*len(obj["timestamp"]))[i],(obj.get("open_interest") or [None]*len(obj["timestamp"]))[i]])
    return rows,diag


def _candle_for(candles,d):
    for c in candles:
        if not isinstance(c,(list,tuple)) or len(c)<5: continue
        try: cd=datetime.fromisoformat(str(c[0]).replace("Z","+00:00")).astimezone(IST).date()
        except ValueError: continue
        if cd==d:
            o,h,l,cl=map(_num,c[1:5]);
            if None not in (o,h,l,cl) and l<=min(o,cl)<=max(o,cl)<=h: return {"open":o,"high":h,"low":l,"close":cl}
    return None


def _normalize(symbol,contract,candle,previous_close,trade_date,provider):
    o,h,l,c=(candle[k] for k in ("open","high","low","close")); p=(h+l+c)/3.0; r1=2*p-l; s1=2*p-h
    bias="NEUTRAL" if previous_close is None or c==previous_close else ("BUY" if c>previous_close else "SELL")
    display=DISPLAY[symbol]
    return {"commodity":display,"source_symbol":symbol,"instrument":f"{display.title()} Futures ({contract['expiry'].strftime('%d%b%Y').upper()})","ltp":c,"breakout_level":r1,"breakdown_level":s1,"buy_invalidation_below":p,"sell_invalidation_above":p,"atr":None,"trend_bias":bias,"source_timestamp":_session_timestamp(trade_date).isoformat(),"source_trade_date":trade_date.isoformat(),"source_observed_at":_session_timestamp(trade_date).isoformat(),"verified":True,"derivation":f"{provider} documented API exact-contract daily OHLC; classic pivot P/R1/S1","verification_tier":"SOURCE_VERIFIED"}


def _acquire_provider(provider,now=None):
    now=now or datetime.now(timezone.utc); local=now.astimezone(IST); session=_last_completed_session(local); prior=session-timedelta(days=7); end=session+timedelta(days=1)
    diagnostics={"adapter":f"mcx-{provider.lower()}-api-v1","source_type":"documented_broker_api","provider":provider,"fetched_at":now.isoformat(),"latest_completed_session":session.isoformat(),"verified_count":0,"record_count":0,"commodities":{},"errors":[]}
    try:
        master,md=(fetch_upstox_master() if provider=="Upstox" else fetch_dhan_master()); diagnostics["instrument_master"]=md
    except Exception as exc:
        diagnostics["errors"].append(f"instrument master: {type(exc).__name__}: {exc}"); return [],diagnostics
    out=[]
    for symbol in TARGETS:
        d={"status":"NOT_RECOMMEND","trade_date":session.isoformat(),"checks":{}}
        try:
            contract=(resolve_upstox(master,symbol,on_date=session) if provider=="Upstox" else resolve_dhan(master,symbol,on_date=session))
            candles,hd=(fetch_upstox_history(contract,prior,end) if provider=="Upstox" else fetch_dhan_history(contract,prior,end)); d["history"]=hd
            candle=_candle_for(candles,session)
            if candle is None: raise ValueError(f"daily candle {session} unavailable")
            prev_dates=sorted([session-timedelta(days=i) for i in range(1,8)],reverse=True); prev=None
            for pd in prev_dates:
                pc=_candle_for(candles,pd)
                if pc is not None: prev=pc["close"]; break
            rec=_normalize(symbol,contract,candle,prev,session,provider); out.append(rec); diagnostics["verified_count"]+=1
            d.update(status="SOURCE_VERIFIED",expiry=contract["expiry"].isoformat(),instrument=rec["instrument"],ltp=rec["ltp"])
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{symbol}: {d['error']}")
        diagnostics["commodities"][symbol]=d
    diagnostics["record_count"]=len(out); return out,diagnostics


def acquire_latest(now=None,lookback_days=8,fetcher=None):
    return _acquire_provider("Upstox",now)


def acquire_mirrors(now=None):
    return _acquire_provider("Dhan",now)


def acquire_historical(now=None):
    now=now or datetime.now(timezone.utc); a,ad=acquire_latest(now); b,bd=acquire_mirrors(now); bm={r["commodity"]:r for r in b}; out=[]
    diagnostics={"adapter":"mcx-historical-mirror-v1","mode":"licensed_api_exact_date_reconciliation","providers":["Upstox","DhanHQ"],"fetched_at":now.isoformat(),"latest_completed_session":_last_completed_session(now.astimezone(IST)).isoformat(),"verified_count":0,"record_count":0,"commodities":{},"errors":[],"upstox":ad,"dhan":bd}
    for r in a:
        c=r["commodity"]; symbol="CRUDEOIL" if c=="CRUDE" else c; d={"status":"NOT_RECOMMEND","checks":{}}; other=bm.get(c)
        try:
            if other is None: raise ValueError("Dhan independent verification unavailable")
            if r["instrument"]!=other["instrument"]: raise ValueError("licensed sources disagree on exact expiry")
            if r["source_trade_date"]!=other["source_trade_date"]: raise ValueError("licensed sources disagree on trade date")
            ok,meta=_agree(symbol,r["ltp"],other["ltp"]); d["checks"]["ltp"]={"ok":ok,**(meta or {})}
            if not ok: raise ValueError("licensed source prices disagree beyond tolerance")
            rec=dict(r); rec["verified"]=True; rec["verification_tier"]="CROSS_SOURCE_VERIFIED"; out.append(rec); diagnostics["verified_count"]+=1; d.update(status="CROSS_SOURCE_VERIFIED",expiry=rec["instrument"].split("(")[-1].rstrip(")"),trade_date=rec["source_trade_date"])
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{c}: {d['error']}")
        diagnostics["commodities"][c]=d
    for c in DISPLAY.values(): diagnostics["commodities"].setdefault(c,{"status":"NOT_RECOMMEND","reason":"primary licensed source unavailable"})
    diagnostics["record_count"]=len(out); return out,diagnostics


def fetch_upstox_quote(contract):
    url=UPSTOX_QUOTE+"?"+urllib.parse.urlencode({"instrument_key":contract["instrument_key"]}); obj,diag=_json_request(url,_upstox_headers()); data=obj.get("data") if isinstance(obj,dict) else None
    if not isinstance(data,dict) or not data: raise ValueError("Upstox quote missing")
    q=next(iter(data.values())); price=_num(q.get("last_price")); ohlc=q.get("ohlc") or {}; ts=q.get("last_trade_time") or q.get("timestamp")
    if price is None: raise ValueError("Upstox LTP missing")
    return {"ltp":price,"previous_close":_num(ohlc.get("close")),"timestamp":ts},diag


def fetch_dhan_quote(contract):
    obj,diag=_json_request(DHAN_QUOTE,_dhan_headers(),{"MCX_COMM":[int(contract["security_id"])]}); data=((obj.get("data") or {}).get("MCX_COMM") if isinstance(obj,dict) else None)
    if not isinstance(data,dict): raise ValueError("Dhan quote missing")
    q=data.get(str(contract["security_id"])) or data.get(int(contract["security_id"]))
    if not isinstance(q,dict): raise ValueError("Dhan exact security quote missing")
    price=_num(q.get("last_price"));
    if price is None: raise ValueError("Dhan LTP missing")
    return {"ltp":price,"previous_close":_num((q.get("ohlc") or {}).get("close"))},diag


def acquire_current(eod_records,now=None,max_age_min=30):
    now=now or datetime.now(timezone.utc); local=now.astimezone(IST)
    diagnostics={"adapter":"mcx-current-session-mirrors-v1","mode":"licensed_api_exact_frozen_contract_reconciliation","providers":["Upstox","DhanHQ"],"nominated_price_source":"Upstox","fetched_at":now.isoformat(),"session_date":local.date().isoformat(),"verified_count":0,"commodities":{},"errors":[]}
    if local.weekday()>=5 or local.hour<9 or local.hour>23 or (local.hour==23 and local.minute>30): diagnostics["errors"].append("outside governed MCX current-session window"); return [],diagnostics
    try: um,ud=fetch_upstox_master(); dm,dd=fetch_dhan_master(); diagnostics["upstox_master"]=ud; diagnostics["dhan_master"]=dd
    except Exception as exc: diagnostics["errors"].append(f"instrument masters: {type(exc).__name__}: {exc}"); return [],diagnostics
    by={str(r.get("source_symbol") or r.get("commodity") or "").upper():r for r in eod_records}
    if "CRUDE" in by and "CRUDEOIL" not in by: by["CRUDEOIL"]=by["CRUDE"]
    out=[]
    for symbol in TARGETS:
        d={"status":"NOT_RECOMMEND","checks":{},"sources":{}}
        try:
            eod=by.get(symbol)
            if not eod or not eod.get("verified"): raise ValueError("verified frozen EOD contract unavailable")
            token=str(eod.get("instrument") or ""); inside=token[token.rfind("(")+1:token.rfind(")")]; expiry=datetime.strptime(inside,"%d%b%Y").date()
            uc=resolve_upstox(um,symbol,expiry=expiry,on_date=local.date()); dc=resolve_dhan(dm,symbol,expiry=expiry,on_date=local.date())
            uq,uqdiag=fetch_upstox_quote(uc); dq,dqdiag=fetch_dhan_quote(dc); ok,meta=_agree(symbol,uq["ltp"],dq["ltp"]); d["checks"]["price"]={"ok":ok,**(meta or {})}
            d["sources"]={"upstox":{"price":uq["ltp"],**uqdiag},"dhan":{"price":dq["ltp"],**dqdiag}}
            if not ok: raise ValueError("current licensed-source prices disagree beyond tolerance")
            ts=now.astimezone(IST); display=DISPLAY[symbol]; obs={"commodity":display,"instrument":str(eod["instrument"]),"price":uq["ltp"],"timestamp":ts.isoformat(),"verified":True,"verification_tier":"CROSS_SOURCE_CURRENT_VERIFIED","source_symbol":symbol,"nominated_source":"Upstox"}; out.append(obs); diagnostics["verified_count"]+=1; d.update(status="CROSS_SOURCE_CURRENT_VERIFIED",expiry=expiry.isoformat(),price=uq["ltp"],timestamp=ts.isoformat(),nominated_source="Upstox")
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{symbol}: {d['error']}")
        diagnostics["commodities"][symbol]=d
    diagnostics["record_count"]=len(out); return out,diagnostics
