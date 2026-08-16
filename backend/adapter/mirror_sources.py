"""Independent public-MCX mirror acquisition and reconciliation.

MCX remains the contract authority. 5paisa supplies an explicit timestamped MCX
contract snapshot; ICICI Direct independently verifies the exact symbol/expiry and
OHLC/previous-close values. No mini/micro substitution is permitted.

Mirror page timestamps are observation timestamps, not trading-session dates. When
this EOD adapter runs before 09:00 IST it anchors frozen/carry-forward quotes to the
last completed MCX session according to the 2026 exchange calendar.
"""
from __future__ import annotations

import html
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
TARGETS = ("GOLD", "SILVER", "CRUDEOIL", "ZINC", "COPPER")
DISPLAY = {"GOLD":"GOLD","SILVER":"SILVER","CRUDEOIL":"CRUDE","ZINC":"ZINC","COPPER":"COPPER"}
FIVEPAISA = {
    "GOLD": ("Gold", "https://www.5paisa.com/commodity-trading/mcx-gold-price"),
    "SILVER": ("Silver", "https://www.5paisa.com/commodity-trading/mcx-silver-price"),
    "CRUDEOIL": ("Crude Oil", "https://www.5paisa.com/commodity-trading/mcx-crudeoil-price"),
    "ZINC": ("Zinc", "https://www.5paisa.com/commodity-trading/mcx-zinc-price"),
    "COPPER": ("Copper", "https://www.5paisa.com/commodity-trading/mcx-copper-price"),
}
ICICI_ACTIVE = "https://www.icicidirect.com/commodities-market/active-by-value/mcx/futcom"
ICICI_QUOTE = "https://www.icicidirect.com/commodities-market/pricequote/mcx/futcom/{slug}/{expiry}"
ABS_TOL = {"GOLD":25.0,"SILVER":75.0,"CRUDEOIL":8.0,"ZINC":0.40,"COPPER":1.25}
REL_TOL = 0.0015
# Official MCX 2026 calendar dates on which both morning and evening sessions are closed.
# Partial holidays with an evening session remain trading-session days for these non-agri contracts.
MCX_FULLY_CLOSED_2026 = {
    date(2026,1,26),  # Republic Day
    date(2026,4,3),   # Good Friday
    date(2026,10,2),  # Mahatma Gandhi Jayanti
    date(2026,12,25), # Christmas
}

@dataclass
class MirrorQuote:
    provider: str
    symbol: str
    expiry: date
    trade_time: datetime | None  # provider page observation timestamp
    ltp: float
    open: float | None
    high: float | None
    low: float | None
    previous_close: float | None
    url: str

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None
    def handle_starttag(self, tag, attrs):
        if tag.lower()=="tr": self.row=[]
        elif tag.lower() in {"td","th"} and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        t=tag.lower()
        if t in {"td","th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split())); self.cell=None
        elif t=="tr" and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None; self.cell=None

def _num(v):
    try:
        x=float(re.sub(r"[^0-9.+-]","",str(v).replace(",","")))
        return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def _fetch(url, timeout=25):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml","Accept-Language":"en-US,en;q=0.9"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        status=int(getattr(r,"status",200)); ctype=str(r.headers.get("Content-Type","")); raw=r.read(4_000_001)
    if status!=200: raise ValueError(f"HTTP {status}")
    if len(raw)>4_000_000: raise ValueError("HTML exceeds 4 MB")
    if "html" not in ctype.lower(): raise ValueError(f"unexpected content type {ctype!r}")
    return raw.decode("utf-8","replace"), status, ctype

def _text(raw):
    s=re.sub(r"(?is)<script.*?</script>|<style.*?</style>"," ",raw)
    s=re.sub(r"(?s)<[^>]+>"," ",s)
    return " ".join(html.unescape(s).split())

def _date_any(s):
    s=" ".join(str(s).replace(","," ").split())
    for fmt in ("%d %B %Y","%d %b %Y","%b %d %Y","%B %d %Y","%d-%b-%Y"):
        try: return datetime.strptime(s.title(),fmt).date()
        except ValueError: pass
    return None

def _is_session_day(d):
    if d.weekday() >= 5: return False
    if d.year == 2026 and d in MCX_FULLY_CLOSED_2026: return False
    return True

def _last_completed_session(local_now):
    d=local_now.date()-timedelta(days=1)
    while not _is_session_day(d): d-=timedelta(days=1)
    return d

def _session_timestamp(d):
    # 23:30 IST is the normal DST-season close for internationally referenceable non-agri contracts.
    # This timestamp is an EOD freshness anchor; provider observation time is audited separately.
    return datetime(d.year,d.month,d.day,23,30,tzinfo=IST)

def _expiry_token(d): return d.strftime("%d-%b-%Y").lower()
def _contract_token(symbol,d): return f"{symbol}-{d.strftime('%d-%b-%y').upper()}"

def fetch_5paisa(symbol):
    title,url=FIVEPAISA[symbol]; raw,status,ctype=_fetch(url); txt=_text(raw)
    m=re.search(rf"{re.escape(title)} Price Today\s+(?:Bullion|Metals|Energy)\s+([A-Za-z]+\s+\d{{1,2}}\s+\d{{4}})",txt,re.I)
    if not m: raise ValueError("active expiry not found")
    expiry=_date_any(m.group(1))
    if not expiry: raise ValueError("active expiry malformed")
    tm=re.search(r"As on\s+(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})\s*\|\s*(\d{1,2}:\d{2})",txt,re.I)
    if not tm: raise ValueError("explicit As on timestamp not found")
    td=_date_any(tm.group(1)); hh,mm=map(int,tm.group(2).split(":")); trade_time=datetime(td.year,td.month,td.day,hh,mm,tzinfo=IST)
    tail=txt[m.end():]
    pm=re.search(r"₹\s*([0-9][0-9,]*(?:\.\d+)?)",tail)
    if not pm: raise ValueError("LTP not found")
    ltp=_num(pm.group(1))
    def val(pattern):
        x=re.search(pattern,txt,re.I); return _num(x.group(1)) if x else None
    low=val(r"Today's Low\s+([0-9][0-9,]*(?:\.\d+)?)")
    high=val(r"Today's High\s+([0-9][0-9,]*(?:\.\d+)?)")
    op=val(r"Open Price\s*\|?\s*([0-9][0-9,]*(?:\.\d+)?)")
    pc=val(r"Previous Close\s*\|?\s*([0-9][0-9,]*(?:\.\d+)?)")
    if None in (ltp,low,high,op,pc): raise ValueError("incomplete 5paisa OHLC/previous-close snapshot")
    if not (low<=min(op,ltp)<=max(op,ltp)<=high): raise ValueError("invalid 5paisa OHLC geometry")
    return MirrorQuote("5paisa",symbol,expiry,trade_time,ltp,op,high,low,pc,url), {"http_status":status,"content_type":ctype}

def fetch_icici_active():
    raw,status,ctype=_fetch(ICICI_ACTIVE); p=TableParser(); p.feed(raw)
    rows={}
    for row in p.rows:
        if not row: continue
        tok=row[0].strip().upper()
        if re.match(r"^[A-Z0-9]+-\d{2}-[A-Z]{3}-\d{2}$",tok) and len(row)>=5:
            ltp=_num(row[3]); chg=_num(row[4])
            if ltp is not None: rows[tok]={"ltp":ltp,"change":chg,"row":row}
    if not rows:
        txt=_text(raw)
        for tok,ltp,chg in re.findall(r"([A-Z0-9]+-\d{2}-[A-Z]{3}-\d{2})\s+0\s+[A-Za-z]{3}\s+\d{1,2},?\s*\d{4}\s+([0-9,]+(?:\.\d+)?)\s+([+-]?[0-9,]+(?:\.\d+)?)",txt):
            rows[tok]={"ltp":_num(ltp),"change":_num(chg),"row":[]}
    return rows,{"http_status":status,"content_type":ctype,"url":ICICI_ACTIVE}

def fetch_icici_quote(symbol,expiry):
    slug={"CRUDEOIL":"crudeoil"}.get(symbol,symbol.lower()); url=ICICI_QUOTE.format(slug=slug,expiry=_expiry_token(expiry)); raw,status,ctype=_fetch(url); txt=_text(raw)
    def val(label):
        m=re.search(rf"\b{label}\b\s+([0-9][0-9,]*(?:\.\d+)?)",txt,re.I); return _num(m.group(1)) if m else None
    low,high,op,pc=(val(x) for x in ("Low","High","Open","Close"))
    if None in (low,high,op,pc): raise ValueError("incomplete ICICI quote OHLC/close snapshot")
    return {"low":low,"high":high,"open":op,"previous_close":pc,"url":url,"http_status":status,"content_type":ctype}

def _agree(symbol,a,b):
    if a is None or b is None: return False,None
    delta=abs(float(a)-float(b)); tol=max(ABS_TOL[symbol],max(abs(float(a)),abs(float(b)))*REL_TOL)
    return delta<=tol,{"delta":round(delta,6),"tolerance":round(tol,6),"delta_percent":round(delta/max(abs(float(a)),1)*100,6)}

def _normalized(symbol,q,session_date):
    p=(q.high+q.low+q.ltp)/3.0; r1=2*p-q.low; s1=2*p-q.high
    bias="NEUTRAL" if q.previous_close is None or q.ltp==q.previous_close else ("BUY" if q.ltp>q.previous_close else "SELL")
    display=DISPLAY[symbol]; ts=_session_timestamp(session_date)
    return {"commodity":display,"source_symbol":symbol,"instrument":f"{display.title()} Futures ({q.expiry.strftime('%d%b%Y').upper()})","ltp":q.ltp,"breakout_level":r1,"breakdown_level":s1,"buy_invalidation_below":p,"sell_invalidation_above":p,"atr":None,"trend_bias":bias,"source_timestamp":ts.isoformat(),"source_trade_date":session_date.isoformat(),"source_observed_at":q.trade_time.isoformat(),"verified":True,"derivation":"cross-source verified 5paisa OHLC + ICICI exact-contract verification; classic pivot P/R1/S1","verification_tier":"CROSS_SOURCE_VERIFIED"}

def acquire_mirrors(now=None):
    now=now or datetime.now(timezone.utc); local=now.astimezone(IST); session_date=_last_completed_session(local)
    diagnostics={"adapter":"mcx-multi-source-v1","mode":"mirror_reconciliation","fetched_at":now.isoformat(),"providers":["5paisa","ICICI Direct"],"verified_count":0,"latest_completed_session":session_date.isoformat(),"session_calendar":"MCX 2026: weekends + full-day closures","commodities":{},"errors":[]}
    if local.hour>=9:
        diagnostics["errors"].append("mirror EOD verification is only permitted before 09:00 IST")
        return [],diagnostics
    try: active,active_diag=fetch_icici_active(); diagnostics["icici_active"]=active_diag
    except Exception as exc:
        diagnostics["errors"].append(f"ICICI active table: {type(exc).__name__}: {exc}"); active={}
    out=[]
    for symbol in TARGETS:
        d={"status":"NOT_RECOMMEND","sources":{},"checks":{},"trade_date":session_date.isoformat()}
        try:
            q,qdiag=fetch_5paisa(symbol)
            d["sources"]["5paisa"]={"url":q.url,"expiry":q.expiry.isoformat(),"observed_at":q.trade_time.isoformat(),**qdiag}
            if q.trade_time.date() < session_date or q.trade_time.date() > local.date():
                raise ValueError(f"5paisa observation date {q.trade_time.date()} cannot verify session {session_date}")
            age=(now-q.trade_time.astimezone(timezone.utc)).total_seconds()/60
            if age < -10 or age > 10080: raise ValueError(f"5paisa timestamp stale/future: {age:.1f} min")
            token=_contract_token(symbol,q.expiry); ir=active.get(token)
            if not ir: raise ValueError(f"ICICI exact contract {token} not present")
            iq=fetch_icici_quote(symbol,q.expiry); d["sources"]["icici"]={"active_url":ICICI_ACTIVE,"quote_url":iq["url"],"contract":token}
            checks=[]
            for key,av,bv in (("ltp",q.ltp,ir["ltp"]),("open",q.open,iq["open"]),("high",q.high,iq["high"]),("low",q.low,iq["low"]),("previous_close",q.previous_close,iq["previous_close"])):
                ok,meta=_agree(symbol,av,bv); d["checks"][key]={"ok":ok,**(meta or {})}; checks.append(ok)
            if not all(checks): raise ValueError("cross-source values disagree beyond tolerance")
            d["status"]="CROSS_SOURCE_VERIFIED"; d["contract"]=token; diagnostics["verified_count"]+=1; out.append(_normalized(symbol,q,session_date))
        except Exception as exc:
            d["error"]=f"{type(exc).__name__}: {exc}"; diagnostics["errors"].append(f"{symbol}: {d['error']}")
        diagnostics["commodities"][symbol]=d
    diagnostics["record_count"]=len(out); return out,diagnostics
