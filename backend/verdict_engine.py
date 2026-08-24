"""Deterministic Commodity Verdict v1.2 two-state engine.

Freeze verified completed-session levels, then evaluate only a separately verified
current-session observation for the exact frozen contract. Directional prior-session
bias may confirm at the classic pivot; neutral bias still requires R1/S1 breakout.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

IST=ZoneInfo("Asia/Kolkata")
COMMODITIES=("GOLD","SILVER","CRUDE","ZINC","COPPER")
CSV_COLUMNS=("record_id","date","ltp","capital_inr","market","instrument","mode","verdict","confidence_score_percent","entry_zone","invalid_if_above","risk_per_trade_inr","daily_loss_cap_inr")

def num(v):
    try:
        d=Decimal(str(v).replace(",","").strip()); return float(d) if d.is_finite() else None
    except (InvalidOperation,ValueError,TypeError,AttributeError): return None

def parse_ts(v):
    if not v: return None
    try:
        x=datetime.fromisoformat(str(v).strip().replace("Z","+00:00")); x=x if x.tzinfo else x.replace(tzinfo=timezone.utc); return x.astimezone(timezone.utc)
    except ValueError: return None

def _fmt(v):
    f=float(v); return str(int(f)) if f.is_integer() else f"{f:.6f}".rstrip("0").rstrip(".")

def _row(c,today,ltp,instrument,verdict,conf,entry,invalid,reason):
    return {"record_id":f"MCX{c}-{today.replace('-','')}-V1","date":today,"ltp":"" if ltp in (None,"") else _fmt(ltp),"capital_inr":"35000","market":"MCX","instrument":instrument or f"{c.title()} Futures","mode":"Intraday","verdict":verdict,"confidence_score_percent":str(int(round(conf))),"entry_zone":entry,"invalid_if_above":"" if invalid in (None,"") else _fmt(invalid),"risk_per_trade_inr":"350","daily_loss_cap_inr":"700","_reason":reason}

def fail_row(c,today,reason,ltp="",instrument=""): return _row(c,today,ltp,instrument,"NOT_RECOMMEND",0,"","",reason)

def _hold_confidence(price,buy_trigger,sell_trigger,bias,atr):
    distances=[]
    if buy_trigger is not None: distances.append(("BUY",max(0.0,buy_trigger-price)))
    if sell_trigger is not None: distances.append(("SELL",max(0.0,price-sell_trigger)))
    if not distances: return 20
    nearest_dir,nearest_dist=min(distances,key=lambda x:x[1])
    if atr is not None and atr>0: proximity=max(0.0,1.0-min(1.0,nearest_dist/(2.0*atr)))
    elif buy_trigger is not None and sell_trigger is not None and buy_trigger>sell_trigger: proximity=max(0.0,1.0-min(1.0,nearest_dist/max((buy_trigger-sell_trigger)/2.0,1e-9)))
    else:
        level=buy_trigger if buy_trigger is not None else sell_trigger; proximity=max(0.0,1.0-min(1.0,nearest_dist/max(abs(level)*0.01,1e-9)))
    conf=25.0+25.0*proximity
    if bias==nearest_dir: conf+=4.0
    elif bias in {"BUY","SELL"} and bias!=nearest_dir: conf-=4.0
    elif bias in {"NEUTRAL","HOLD",""}: conf-=1.0
    return max(20.0,min(54.0,conf))

def freeze_levels(c,rec,now,max_age_min=1440):
    today=now.astimezone(IST).date().isoformat()
    if rec is None: return None,fail_row(c,today,"missing commodity")
    if str(rec.get("commodity","")).upper()!=c: return None,fail_row(c,today,"commodity identity mismatch")
    ref=num(rec.get("ltp")); bo=num(rec.get("breakout_level")); bd=num(rec.get("breakdown_level")); bi=num(rec.get("buy_invalidation_below")); si=num(rec.get("sell_invalidation_above")); atr=num(rec.get("atr")); inst=str(rec.get("instrument") or ""); ts=parse_ts(rec.get("source_timestamp"))
    if ts is None: return None,fail_row(c,today,"missing/malformed source timestamp",ref,inst)
    age=(now-ts).total_seconds()/60
    if age < -10 or age > max_age_min: return None,fail_row(c,today,f"stale source ({age:.1f} min)",ref,inst)
    if not rec.get("verified"): return None,fail_row(c,today,"unverified row",ref,inst)
    if ref is None or ref<=0: return None,fail_row(c,today,"invalid EOD reference","",inst)
    if not inst: return None,fail_row(c,today,"missing frozen contract identity",ref,inst)
    if bo is None and bd is None: return None,fail_row(c,today,"missing entry level",ref,inst)
    if bo is not None and bi is None: return None,fail_row(c,today,"missing BUY invalidation",ref,inst)
    if bd is not None and si is None: return None,fail_row(c,today,"missing SELL invalidation",ref,inst)
    if bo is not None and bi is not None and not bi < bo: return None,fail_row(c,today,"invalid BUY geometry",ref,inst)
    if bd is not None and si is not None and not si > bd: return None,fail_row(c,today,"invalid SELL geometry",ref,inst)
    if bo is not None and bd is not None and not bd < bo: return None,fail_row(c,today,"invalid range geometry",ref,inst)
    bias=str(rec.get("trend_bias") or "").upper()
    if bias not in {"","BUY","SELL","NEUTRAL","HOLD"}: return None,fail_row(c,today,"invalid trend bias",ref,inst)
    buffer=max((atr or 0)*0.10,ref*0.0005)
    return {"commodity":c,"reference_price":ref,"instrument":inst,"bias":bias,"atr":atr,"breakout_level":bo,"breakdown_level":bd,"buy_invalidation":bi,"sell_invalidation":si,"buy_trigger":bo+buffer if bo is not None else None,"sell_trigger":bd-buffer if bd is not None else None,"buffer":buffer,"source_timestamp":ts,"source_trade_date":str(rec.get("source_trade_date") or "")},None

def _observation(c,obs,now,expected_instrument,max_age_min=30):
    if obs is None: return None,"no current-session observation"
    if str(obs.get("commodity","")).upper()!=c: return None,"current observation commodity mismatch"
    if not obs.get("verified"): return None,"unverified current-session observation"
    inst=str(obs.get("instrument") or "")
    if not inst: return None,"current observation missing exact contract identity"
    if inst.strip().upper()!=str(expected_instrument).strip().upper(): return None,"current observation contract mismatch"
    price=num(obs.get("price",obs.get("ltp"))); ts=parse_ts(obs.get("timestamp",obs.get("source_timestamp")))
    if price is None or price<=0: return None,"invalid current-session price"
    if ts is None: return None,"missing/malformed current-session timestamp"
    age=(now-ts).total_seconds()/60
    if age < -10 or age > max_age_min: return None,f"stale current-session observation ({age:.1f} min)"
    if ts.astimezone(IST).date()!=now.astimezone(IST).date(): return None,"current observation is not from current IST session date"
    return {"price":price,"timestamp":ts},None

def evaluate_frozen(c,frozen,obs,now,current_max_age_min=30,require_current=False):
    today=now.astimezone(IST).date().isoformat(); ref=frozen["reference_price"]; inst=frozen["instrument"]; bo=frozen["breakout_level"]; bd=frozen["breakdown_level"]; bi=frozen["buy_invalidation"]; si=frozen["sell_invalidation"]; bt=frozen["buy_trigger"]; st=frozen["sell_trigger"]; bias=frozen["bias"]; atr=frozen["atr"]; buf=frozen["buffer"]
    entry=f"Above {_fmt(bo)} breakout / Below {_fmt(bd)} breakdown" if bo is not None and bd is not None else (f"Above {_fmt(bo)} breakout" if bo is not None else f"Below {_fmt(bd)} breakdown")
    current,err=_observation(c,obs,now,inst,current_max_age_min)
    if err=="no current-session observation":
        if require_current: return fail_row(c,today,"required current-session observation unavailable",ref,inst)
        return _row(c,today,ref,inst,"HOLD",_hold_confidence(ref,bt,st,bias,atr),entry,"","levels frozen; awaiting current-session price")
    if err: return fail_row(c,today,err,ref,inst)
    price=current["price"]
    # Strong R1/S1 breakouts remain valid, but never override an opposite verified bias.
    if bt is not None and price>=bt:
        if bias=="SELL": return fail_row(c,today,"signal/trend conflict",price,inst)
        conf=min(90,max(55,60+(8 if bias=="BUY" else 0)+min(20,(price-bt)/max(buf,1e-9)*5)))
        return _row(c,today,price,inst,"BUY",conf,f"Above {_fmt(bo)} breakout",bi,"BUY R1 breakout on current-session observation")
    if st is not None and price<=st:
        if bias=="BUY": return fail_row(c,today,"signal/trend conflict",price,inst)
        conf=min(90,max(55,60+(8 if bias=="SELL" else 0)+min(20,(st-price)/max(buf,1e-9)*5)))
        return _row(c,today,price,inst,"SELL",conf,f"Below {_fmt(bd)} breakdown",si,"SELL S1 breakdown on current-session observation")
    # v1.2: classic pivot is a continuation trigger when the completed session already
    # established direction. This removes the structural R1/S1-only HOLD bias without
    # manufacturing signals: price, prior bias, exact contract and freshness must agree.
    if bias=="BUY" and bi is not None:
        trigger=bi+buf
        if price>=trigger:
            conf=min(82,max(55,58+min(16,(price-trigger)/max(buf,1e-9)*4)))
            return _row(c,today,price,inst,"BUY",conf,f"Above {_fmt(trigger)} breakout",bi,"BUY pivot continuation confirmed by current-session observation")
    if bias=="SELL" and si is not None:
        trigger=si-buf
        if price<=trigger:
            conf=min(82,max(55,58+min(16,(trigger-price)/max(buf,1e-9)*4)))
            return _row(c,today,price,inst,"SELL",conf,f"Below {_fmt(trigger)} breakdown",si,"SELL pivot continuation confirmed by current-session observation")
    return _row(c,today,price,inst,"HOLD",_hold_confidence(price,bt,st,bias,atr),entry,"","current-session price has no directionally confirmed trigger")

def evaluate(c,rec,now,max_age_min=1440,current_observation=None,current_max_age_min=30,require_current=False):
    frozen,failure=freeze_levels(c,rec,now,max_age_min)
    return failure if failure is not None else evaluate_frozen(c,frozen,current_observation,now,current_max_age_min,require_current)

def run(records,now=None,max_age_min=None,current_observations=None,current_max_age_min=None,require_current=False):
    now=now or datetime.now(timezone.utc); max_age_min=max_age_min or int(os.getenv("MAX_SOURCE_AGE_MINUTES","1440")); current_max_age_min=current_max_age_min or int(os.getenv("MAX_CURRENT_PRICE_AGE_MINUTES","30")); by={}; duplicates=set(); current_by={}; current_dups=set()
    for r in records:
        c=str(r.get("commodity","")).upper()
        if c in by: duplicates.add(c)
        elif c in COMMODITIES: by[c]=r
    for r in current_observations or []:
        c=str(r.get("commodity","")).upper()
        if c in current_by: current_dups.add(c)
        elif c in COMMODITIES: current_by[c]=r
    rows=[]; audits=[]; today=now.astimezone(IST).date().isoformat()
    for c in COMMODITIES:
        if c in duplicates: row=fail_row(c,today,"duplicate commodity records")
        elif c in current_dups: row=fail_row(c,today,"duplicate current-session observations")
        else: row=evaluate(c,by.get(c),now,max_age_min,current_by.get(c),current_max_age_min,require_current)
        audits.append({"commodity":c,"verdict":row["verdict"],"reason":row["_reason"]}); rows.append({k:row[k] for k in CSV_COLUMNS})
    return rows,audits
