"""Official MCX Bhav Copy adapter used by the scheduled production pipeline."""
from __future__ import annotations

import http.cookiejar
import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

MCX_ENDPOINT = "https://www.mcxindia.com/backpage.aspx/GetDateWiseBhavCopy"
MCX_BHAVCOPY_PAGE = "https://www.mcxindia.com/market-data/bhavcopy"
TARGETS = ("GOLD", "SILVER", "CRUDEOIL", "ZINC", "COPPER")
DISPLAY = {"CRUDEOIL": "CRUDE", "GOLD": "GOLD", "SILVER": "SILVER", "ZINC": "ZINC", "COPPER": "COPPER"}
IST = ZoneInfo("Asia/Kolkata")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"

ALIASES = {
    "instrument": ("InstrumentName", "Instrument", "InstrumentType", "Instrument_Type"),
    "symbol": ("Symbol", "Commodity", "CommodityName", "Product"),
    "expiry": ("ExpiryDate", "Expiry", "Expiry_Date", "Expiry Date"),
    "open": ("Open", "OpenPrice", "OPEN", "OPEN_PRICE"),
    "high": ("High", "HighPrice", "HIGH", "HIGH_PRICE"),
    "low": ("Low", "LowPrice", "LOW", "LOW_PRICE"),
    "close": ("Close", "ClosingPrice", "ClosePrice", "SettlementPrice", "SettPrice"),
    "pcp": ("PCP", "PrevClose", "PreviousClose", "PreviousClosePrice", "PREVCLOSE"),
    "volume": ("Volume", "Vol", "VolumeLots", "TotalQuantityTraded", "CumTrdVol"),
    "oi": ("OI", "OpenInterest", "OiQty", "OpenInterestLots"),
}

@dataclass
class FetchResult:
    trade_date: date
    records: list[dict[str, Any]]
    http_status: int
    content_type: str
    response_fields: list[str]

def _first(row, names):
    lower={str(k).strip().lower():v for k,v in row.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()] not in (None,""): return lower[name.lower()]
    return None

def _num(v):
    if v is None or isinstance(v,bool): return None
    try: x=float(str(v).replace(",","").strip())
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None

def _expiry(v):
    if not v: return None
    s=str(v).strip()
    for fmt in ("%d%b%Y","%d-%b-%Y","%Y-%m-%d","%d/%m/%Y","%d %b %Y"):
        try: return datetime.strptime(s.upper(),fmt).date()
        except ValueError: pass
    if s.startswith("/Date("):
        try:
            ms=int(s.split("(",1)[1].split(")",1)[0].split("+",1)[0]); return datetime.fromtimestamp(ms/1000,tz=timezone.utc).astimezone(IST).date()
        except (ValueError,IndexError): return None
    return None

def _browser_headers(accept="application/json, text/javascript, */*; q=0.01"):
    return {"Accept":accept,"Accept-Language":"en-US,en;q=0.9","User-Agent":BROWSER_UA,"Sec-Fetch-Site":"same-origin","Sec-Fetch-Mode":"cors","Sec-Fetch-Dest":"empty","X-Requested-With":"XMLHttpRequest"}

def _open_session(timeout):
    jar=http.cookiejar.CookieJar(); opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req=urllib.request.Request(MCX_BHAVCOPY_PAGE,headers={"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"en-US,en;q=0.9","User-Agent":BROWSER_UA})
    with opener.open(req,timeout=timeout) as resp: resp.read(4096)
    return opener

def fetch_date(trade_date: date, timeout: int = 25) -> FetchResult:
    opener=_open_session(timeout)
    payload=json.dumps({"Date":trade_date.strftime("%Y%m%d"),"InstrumentName":"FUTCOM"},separators=(",",":")).encode("utf-8")
    headers=_browser_headers(); headers.update({"Content-Type":"application/json; charset=UTF-8","Origin":"https://www.mcxindia.com","Referer":MCX_BHAVCOPY_PAGE})
    req=urllib.request.Request(MCX_ENDPOINT,data=payload,headers=headers,method="POST")
    try:
        with opener.open(req,timeout=timeout) as resp:
            status=int(getattr(resp,"status",200)); ctype=str(resp.headers.get("Content-Type","")); raw=resp.read(10_000_001)
    except urllib.error.HTTPError as exc: raise ValueError(f"MCX HTTP {exc.code}") from exc
    except urllib.error.URLError as exc: raise ValueError(f"MCX network error: {exc.reason}") from exc
    if status!=200: raise ValueError(f"MCX HTTP {status}")
    if len(raw)>10_000_000: raise ValueError("MCX payload exceeds 10 MB bound")
    if "json" not in ctype.lower():
        excerpt=" ".join(raw[:300].decode("utf-8","replace").split())
        raise ValueError(f"MCX unexpected content type {ctype!r}: {excerpt}")
    obj=json.loads(raw.decode("utf-8")); d=obj.get("d") if isinstance(obj,dict) else None
    if isinstance(d,str): d=json.loads(d)
    rows=d.get("Data") if isinstance(d,dict) else None
    if rows is None: raise ValueError("MCX response missing d.Data")
    if not isinstance(rows,list): raise ValueError("MCX d.Data is not a list")
    records=[r for r in rows if isinstance(r,dict)]; fields=sorted({str(k) for r in records for k in r.keys()})
    return FetchResult(trade_date,records,status,ctype,fields)

def _select_contract(rows,symbol,trade_date):
    candidates=[]
    for raw in rows:
        inst=str(_first(raw,ALIASES["instrument"]) or "").strip().upper(); sym=str(_first(raw,ALIASES["symbol"]) or "").strip().upper()
        if inst and inst!="FUTCOM": continue
        if sym!=symbol: continue
        o,h,l,c=(_num(_first(raw,ALIASES[k])) for k in ("open","high","low","close"))
        if None in (o,h,l,c) or not (l<=min(o,c)<=max(o,c)<=h): continue
        exp=_expiry(_first(raw,ALIASES["expiry"]));
        if exp is not None and exp<trade_date: continue
        vol=_num(_first(raw,ALIASES["volume"])) or 0.0; candidates.append((0 if vol>0 else 1,exp or date.max,-vol,raw))
    if not candidates: return None
    candidates.sort(key=lambda x:(x[0],x[1],x[2])); return candidates[0][3]

def _normalize(raw,symbol,trade_date):
    o=_num(_first(raw,ALIASES["open"])); h=_num(_first(raw,ALIASES["high"])); l=_num(_first(raw,ALIASES["low"])); c=_num(_first(raw,ALIASES["close"])); pcp=_num(_first(raw,ALIASES["pcp"]))
    if None in (o,h,l,c) or h<=l: return None
    pivot=(h+l+c)/3.0; breakout=2*pivot-l; breakdown=2*pivot-h
    if not breakdown<pivot<breakout: return None
    bias="NEUTRAL" if pcp is None or c==pcp else ("BUY" if c>pcp else "SELL")
    exp=_expiry(_first(raw,ALIASES["expiry"])); display=DISPLAY[symbol]
    return {"commodity":display,"source_symbol":symbol,"instrument":f"{display.title()} Futures"+(f" ({exp.strftime('%d%b%Y').upper()})" if exp else ""),"ltp":c,"breakout_level":breakout,"breakdown_level":breakdown,"buy_invalidation_below":pivot,"sell_invalidation_above":pivot,"atr":None,"trend_bias":bias,"source_timestamp":f"{trade_date.isoformat()}T00:00:00+05:30","source_trade_date":trade_date.isoformat(),"verified":True,"derivation":"classic-pivot: P=(H+L+C)/3; breakout=R1=2P-L; breakdown=S1=2P-H; invalidation=P","source_ohlc":{"open":o,"high":h,"low":l,"close":c,"pcp":pcp},"source_volume":_num(_first(raw,ALIASES["volume"])),"source_oi":_num(_first(raw,ALIASES["oi"]))}

def acquire_latest(now=None,lookback_days=8,fetcher=fetch_date):
    now=now or datetime.now(timezone.utc); today=now.astimezone(IST).date(); errors=[]; attempts=[]; chosen=None
    for offset in range(1,lookback_days+1):
        d=today-timedelta(days=offset)
        try:
            res=fetcher(d); attempts.append({"date":d.isoformat(),"http_status":res.http_status,"rows":len(res.records)})
            if res.records: chosen=res; break
        except Exception as exc:
            attempts.append({"date":d.isoformat(),"error":f"{type(exc).__name__}: {exc}"}); errors.append(f"{d.isoformat()}: {type(exc).__name__}: {exc}")
    diagnostics={"adapter":"mcx-official-bhavcopy-v1","source_type":"official_mcx_json","source":MCX_ENDPOINT,"public_page":MCX_BHAVCOPY_PAGE,"request_method":"POST","request_instrument":"FUTCOM","lookback_days":lookback_days,"attempts":attempts,"errors":errors,"fetched_at":now.isoformat(),"selected_trade_date":None,"response_fields":[],"selected_contracts":{},"field_definitions":{"commodity":"Exact MCX Symbol mapped to GOLD/SILVER/CRUDE/ZINC/COPPER; no cross-symbol fallback","instrument":"Selected FUTCOM contract, nearest unexpired contract with positive volume and valid OHLC","ltp":"MCX Bhav Copy Close for the selected contract (EOD reference price)","breakout_level":"Classic pivot R1 = 2*P - Low","breakdown_level":"Classic pivot S1 = 2*P - High","buy_invalidation_below":"Classic pivot P = (High+Low+Close)/3","sell_invalidation_above":"Classic pivot P = (High+Low+Close)/3","atr":"Not asserted from one-day Bhav Copy; null","trend_bias":"BUY if Close>PCP, SELL if Close<PCP, otherwise NEUTRAL","source_timestamp":"Trade date anchored at 00:00 IST; exact MCX publication time is not asserted","verification_status":"true only after HTTP/content/schema/exact-symbol/OHLC/geometry validation"}}
    if chosen is None: return [],diagnostics
    diagnostics["selected_trade_date"]=chosen.trade_date.isoformat(); diagnostics["http_status"]=chosen.http_status; diagnostics["content_type"]=chosen.content_type; diagnostics["response_fields"]=chosen.response_fields; out=[]
    for symbol in TARGETS:
        raw=_select_contract(chosen.records,symbol,chosen.trade_date)
        if raw is None: diagnostics["selected_contracts"][symbol]={"status":"missing"}; continue
        record=_normalize(raw,symbol,chosen.trade_date)
        if record is None: diagnostics["selected_contracts"][symbol]={"status":"invalid"}; continue
        diagnostics["selected_contracts"][symbol]={"status":"verified","instrument":record["instrument"],"source_symbol":symbol,"ohlc":record["source_ohlc"],"volume":record["source_volume"],"oi":record["source_oi"]}; out.append(record)
    diagnostics["record_count"]=len(out); return out,diagnostics
