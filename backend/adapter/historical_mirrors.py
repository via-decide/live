"""Governed date-specific fallback for completed MCX EOD sessions.

This module is used only when the established 5paisa + ICICI Direct mirror path
cannot produce verified records. It never treats a current-session quote as prior
session EOD data. Upstox and The Economic Times must independently expose the same
completed-session row, full-size symbol, exact expiry, and matching OHLC/close.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .mirror_sources import (
    IST, TARGETS, MirrorQuote, TableParser, _agree, _date_any, _fetch,
    _last_completed_session, _normalized, _session_timestamp, _text,
)

UPSTOX = {
    "GOLD": "https://upstox.com/commodity-market-trading/mcx-gold-price/",
    "SILVER": "https://upstox.com/commodity-market-trading/mcx-silver-price/",
    "CRUDEOIL": "https://upstox.com/commodity-market-trading/mcx-crudeoil-price/",
    "ZINC": "https://upstox.com/commodity-market-trading/mcx-zinc-price/",
    "COPPER": "https://upstox.com/commodity-market-trading/mcx-copper-price/",
}
ET = "https://economictimes.indiatimes.com/commoditysummary/symbol-{symbol}.cms?expiry={expiry}"
DISPLAY_NAME = {"GOLD":"Gold","SILVER":"Silver","CRUDEOIL":"Crude Oil","ZINC":"Zinc","COPPER":"Copper"}
NUM = r"[+-]?[₹]?\s*[0-9][0-9,]*(?:\.\d+)?"


def _num(v):
    from .mirror_sources import _num as parse_num
    return parse_num(v)


def _date(v):
    parsed = _date_any(v)
    if parsed:
        return parsed
    s = " ".join(str(v).replace(",", " ").split())
    for fmt in ("%d %b %y", "%d %B %y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(s.title(), fmt).date()
        except ValueError:
            pass
    return None


def _quote(symbol, expiry, session_date, close, op, high, low, previous_close, provider, url):
    vals = (close, op, high, low)
    if any(v is None for v in vals):
        raise ValueError(f"incomplete {provider} EOD row")
    if not (low <= op <= high and low <= close <= high):
        raise ValueError(f"invalid {provider} EOD geometry")
    return MirrorQuote(provider, symbol, expiry, _session_timestamp(session_date), close, op, high, low, previous_close, url)


def fetch_upstox_eod(symbol, session_date):
    url = UPSTOX[symbol]
    raw, status, ctype = _fetch(url)
    txt = _text(raw)
    if f"{symbol} HISTORICAL PRICE" not in txt.upper():
        raise ValueError("Upstox historical section not found")
    parser = TableParser(); parser.feed(raw)
    for row in parser.rows:
        if len(row) < 6:
            continue
        row_date, expiry = _date(row[0]), _date(row[1])
        if row_date != session_date or expiry is None:
            continue
        op, high, low, close = (_num(x) for x in row[2:6])
        q = _quote(symbol, expiry, session_date, close, op, high, low, None, "Upstox", url)
        return q, {"http_status": status, "content_type": ctype, "url": url, "parse_mode":"table"}

    day = rf"{session_date.day}\s+{session_date.strftime('%b')}\s*,?\s*{session_date.strftime('%y')}"
    pattern = rf"\b{day}\b\s+(\d{{1,2}}\s+[A-Za-z]{{3}}\s*,?\s*\d{{2}})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})"
    m = re.search(pattern, txt, re.I)
    if m:
        expiry = _date(m.group(1)); op, high, low, close = (_num(m.group(i)) for i in range(2,6))
        if expiry is None:
            raise ValueError("Upstox expiry malformed")
        q = _quote(symbol, expiry, session_date, close, op, high, low, None, "Upstox", url)
        return q, {"http_status": status, "content_type": ctype, "url": url, "parse_mode":"text"}
    raise ValueError(f"Upstox EOD row {session_date} not found")


def _et_identity_ok(txt, symbol, expiry):
    upper = txt.upper()
    symbol_ok = bool(re.search(rf"\b{re.escape(symbol)}\b\s+CONTRACT\s+DETAILS", upper))
    exchange_ok = bool(re.search(r"\bEXCHANGE\s*:\s*MCX\b", upper))
    iso = re.escape(expiry.isoformat())
    expiry_ok = bool(
        re.search(rf"\({iso}\)", txt, re.I)
        or re.search(rf"\bEXPIRY\s+DATE\s+{iso}\b", txt, re.I)
        or re.search(rf"\bEXPIRY\s*:\s*{expiry.strftime('%d-%b-%Y')}\b", txt, re.I)
    )
    return symbol_ok and exchange_ok and expiry_ok


def fetch_et_eod(symbol, expiry, session_date):
    url = ET.format(symbol=symbol, expiry=expiry.isoformat())
    raw, status, ctype = _fetch(url)
    txt = _text(raw)
    if not _et_identity_ok(txt, symbol, expiry):
        raise ValueError("ET exact contract identity not found")
    parser = TableParser(); parser.feed(raw)
    for row in parser.rows:
        if len(row) < 8:
            continue
        row_date, row_expiry = _date(row[0]), _date(row[2])
        if row_date != session_date or row_expiry != expiry:
            continue
        op, high, low, previous_close, abs_change = (_num(x) for x in row[3:8])
        if None in (op, high, low, previous_close, abs_change):
            continue
        close = previous_close + abs_change
        q = _quote(symbol, expiry, session_date, close, op, high, low, previous_close, "Economic Times", url)
        return q, {"http_status": status, "content_type": ctype, "url": url, "parse_mode":"table"}

    day = re.escape(session_date.isoformat())
    exp = rf"{expiry.day:02d}-{expiry.strftime('%b')}-{expiry.year}"
    commodity = re.escape(DISPLAY_NAME[symbol])
    pattern = rf"\b{day}\b\s+{commodity}\s+{exp}\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})"
    m = re.search(pattern, txt, re.I)
    if m:
        op, high, low, previous_close, abs_change = (_num(m.group(i)) for i in range(1,6))
        close = previous_close + abs_change
        q = _quote(symbol, expiry, session_date, close, op, high, low, previous_close, "Economic Times", url)
        return q, {"http_status": status, "content_type": ctype, "url": url, "parse_mode":"text"}
    raise ValueError(f"ET EOD row {session_date} / {expiry.isoformat()} not found")


def _verify_pair(symbol, session_date, per):
    upstox, up_diag = fetch_upstox_eod(symbol, session_date)
    et, et_diag = fetch_et_eod(symbol, upstox.expiry, session_date)
    per["sources"]["upstox"] = {"expiry": upstox.expiry.isoformat(), **up_diag}
    per["sources"]["economic_times"] = {"expiry": et.expiry.isoformat(), **et_diag}
    if upstox.expiry != et.expiry:
        raise ValueError("historical mirrors disagree on exact expiry")
    checks = []
    for key, a, b in (
        ("ltp", upstox.ltp, et.ltp),
        ("open", upstox.open, et.open),
        ("high", upstox.high, et.high),
        ("low", upstox.low, et.low),
    ):
        ok, meta = _agree(symbol, a, b)
        per["checks"][key] = {"ok": ok, **(meta or {})}
        checks.append(ok)
    if not all(checks):
        raise ValueError("historical mirrors disagree beyond tolerance")
    upstox.previous_close = et.previous_close
    return upstox


def acquire_historical(now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(IST)
    session_date = _last_completed_session(local)
    diagnostics = {
        "adapter": "mcx-historical-mirror-v1",
        "mode": "exact_date_historical_reconciliation",
        "providers": ["Upstox", "Economic Times"],
        "fetched_at": now.isoformat(),
        "latest_completed_session": session_date.isoformat(),
        "verified_count": 0,
        "record_count": 0,
        "commodities": {},
        "errors": [],
    }
    out = []
    for symbol in TARGETS:
        per = {"status": "NOT_RECOMMEND", "trade_date": session_date.isoformat(), "sources": {}, "checks": {}}
        try:
            q = _verify_pair(symbol, session_date, per)
            per["status"] = "CROSS_SOURCE_VERIFIED"
            per["expiry"] = q.expiry.isoformat()
            out.append(_normalized(symbol, q, session_date))
            out[-1]["derivation"] = "cross-source verified Upstox + Economic Times exact-date exact-expiry EOD OHLC/close; classic pivot P/R1/S1"
            diagnostics["verified_count"] += 1
        except Exception as exc:
            per["error"] = f"{type(exc).__name__}: {exc}"
            diagnostics["errors"].append(f"{symbol}: {per['error']}")
        diagnostics["commodities"][symbol] = per
    diagnostics["record_count"] = len(out)
    return out, diagnostics
