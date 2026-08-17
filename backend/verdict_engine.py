"""Deterministic Commodity Verdict v1.0 engine."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

COMMODITIES=("GOLD","SILVER","CRUDE","ZINC","COPPER")
CSV_COLUMNS=("record_id","date","ltp","capital_inr","market","instrument","mode","verdict","confidence_score_percent","entry_zone","invalid_if_above","risk_per_trade_inr","daily_loss_cap_inr")

def num(v):
    try:
        d=Decimal(str(v).replace(",","").strip()); return float(d) if d.is_finite() else None
    except (InvalidOperation,ValueError,TypeError,AttributeError): return None

def parse_ts(v):
    if not v: return None
    try:
        x=datetime.fromisoformat(str(v).strip().replace("Z","+00:00"))
        if x.tzinfo is None: x=x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc)
    except ValueError: return None

def _fmt(v):
    f=float(v); return str(int(f)) if f.is_integer() else f"{f:.6f}".rstrip("0").rstrip(".")

def _row(c,today,ltp,instrument,verdict,conf,entry,invalid,reason):
    return {"record_id":f"MCX{c}-{today.replace('-','')}-V1","date":today,"ltp":"" if ltp in (None,"") else _fmt(ltp),
      "capital_inr":"35000","market":"MCX","instrument":instrument or f"{c.title()} Futures","mode":"Intraday","verdict":verdict,
      "confidence_score_percent":str(int(round(conf))),"entry_zone":entry,"invalid_if_above":"" if invalid in (None,"") else _fmt(invalid),
      "risk_per_trade_inr":"350","daily_loss_cap_inr":"700","_reason":reason}

def fail_row(c,today,reason,ltp="",instrument=""): return _row(c,today,ltp,instrument,"NOT_RECOMMEND",0,"","",reason)

def _hold_confidence(ltp,buy_trigger,sell_trigger,bias,atr):
    distances=[]
    if buy_trigger is not None: distances.append(("BUY",max(0.0,buy_trigger-ltp)))
    if sell_trigger is not None: distances.append(("SELL",max(0.0,ltp-sell_trigger)))
    if not distances: return 20
    nearest_dir,nearest_dist=min(distances,key=lambda x:x[1])
    if atr is not None and atr>0:
        proximity=max(0.0,1.0-min(1.0,nearest_dist/(2.0*atr)))
    elif buy_trigger is not None and sell_trigger is not None and buy_trigger>sell_trigger:
        width=buy_trigger-sell_trigger
        proximity=max(0.0,1.0-min(1.0,nearest_dist/max(width/2.0,1e-9)))
    else:
        level=buy_trigger if buy_trigger is not None else sell_trigger
        proximity=max(0.0,1.0-min(1.0,nearest_dist/max(abs(level)*0.01,1e-9)))
    conf=25.0+25.0*proximity
    if bias==nearest_dir: conf+=4.0
    elif bias in {"BUY","SELL"} and bias!=nearest_dir: conf-=4.0
    elif bias in {"NEUTRAL","HOLD",""}: conf-=1.0
    return max(20.0,min(54.0,conf))

def evaluate(c, rec, now, max_age_min=1440):
    today=now.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    if rec is None: return fail_row(c,today,"missing commodity")
    if str(rec.get("commodity","")).upper()!=c: return fail_row(c,today,"commodity identity mismatch")
    ltp=num(rec.get("ltp")); bo=num(rec.get("breakout_level")); bd=num(rec.get("breakdown_level")); bi=num(rec.get("buy_invalidation_below")); si=num(rec.get("sell_invalidation_above")); atr=num(rec.get("atr")); inst=str(rec.get("instrument") or "")
    ts=parse_ts(rec.get("source_timestamp"))
    if ts is None: return fail_row(c,today,"missing/malformed source timestamp",ltp,inst)
    age=(now-ts).total_seconds()/60
    if age < -10 or age > max_age_min: return fail_row(c,today,f"stale source ({age:.1f} min)",ltp,inst)
    if not rec.get("verified"): return fail_row(c,today,"unverified row",ltp,inst)
    if ltp is None or ltp<=0: return fail_row(c,today,"invalid LTP","",inst)
    if bo is None and bd is None: return fail_row(c,today,"missing entry level",ltp,inst)
    if bo is not None and bi is None: return fail_row(c,today,"missing BUY invalidation",ltp,inst)
    if bd is not None and si is None: return fail_row(c,today,"missing SELL invalidation",ltp,inst)
    if bo is not None and bi is not None and not bi < bo: return fail_row(c,today,"invalid BUY geometry",ltp,inst)
    if bd is not None and si is not None and not si > bd: return fail_row(c,today,"invalid SELL geometry",ltp,inst)
    if bo is not None and bd is not None and not bd < bo: return fail_row(c,today,"invalid range geometry",ltp,inst)
    bias=str(rec.get("trend_bias") or "").upper()
    if bias not in {"","BUY","SELL","NEUTRAL","HOLD"}: return fail_row(c,today,"invalid trend bias",ltp,inst)
    buffer=max((atr or 0)*0.10,ltp*0.0005); buy_trigger=bo+buffer if bo is not None else None; sell_trigger=bd-buffer if bd is not None else None
    if buy_trigger is not None and ltp >= buy_trigger:
        if bi is not None and ltp <= bi: return _row(c,today,ltp,inst,"HOLD",25,f"Above {_fmt(bo)} breakout",bi,"crossed BUY invalidation")
        if bias=="SELL": return fail_row(c,today,"signal/trend conflict",ltp,inst)
        conf=min(90,max(55,60+(8 if bias=="BUY" else 0)+min(20,(ltp-buy_trigger)/max(buffer,1)*5)))
        return _row(c,today,ltp,inst,"BUY",conf,f"Above {_fmt(bo)} breakout",bi,"BUY trigger")
    if sell_trigger is not None and ltp <= sell_trigger:
        if si is not None and ltp >= si: return _row(c,today,ltp,inst,"HOLD",25,f"Below {_fmt(bd)} breakdown",si,"crossed SELL invalidation")
        if bias=="BUY": return fail_row(c,today,"signal/trend conflict",ltp,inst)
        conf=min(90,max(55,60+(8 if bias=="SELL" else 0)+min(20,(sell_trigger-ltp)/max(buffer,1)*5)))
        return _row(c,today,ltp,inst,"SELL",conf,f"Below {_fmt(bd)} breakdown",si,"SELL trigger")
    entry=f"Above {_fmt(bo)} breakout / Below {_fmt(bd)} breakdown" if bo is not None and bd is not None else (f"Above {_fmt(bo)} breakout" if bo is not None else f"Below {_fmt(bd)} breakdown")
    hold_conf=_hold_confidence(ltp,buy_trigger,sell_trigger,bias,atr)
    return _row(c,today,ltp,inst,"HOLD",hold_conf,entry,"","waiting for valid trigger")

def run(records, now=None, max_age_min=None):
    now=now or datetime.now(timezone.utc); max_age_min=max_age_min or int(os.getenv("MAX_SOURCE_AGE_MINUTES","1440")); by={}; duplicates=set()
    for r in records:
        c=str(r.get("commodity","")).upper()
        if c in by: duplicates.add(c)
        elif c in COMMODITIES: by[c]=r
    rows=[]; audits=[]
    for c in COMMODITIES:
        row=fail_row(c,now.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat(),"duplicate commodity records") if c in duplicates else evaluate(c,by.get(c),now,max_age_min)
        audits.append({"commodity":c,"verdict":row["verdict"],"reason":row["_reason"]}); rows.append({k:row[k] for k in CSV_COLUMNS})
    return rows,audits
