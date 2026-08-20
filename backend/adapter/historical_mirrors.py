"""Governed date-specific fallback for completed MCX EOD sessions.

This module is used only when the established 5paisa + ICICI Direct mirror path
cannot produce verified records. It does not use current-session quotes as prior
session data. Upstox and The Economic Times must independently expose the same
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


def _num(v):
    from .mirror_sources import _num as parse_num
    return parse_num(v)


def fetch_upstox_eod(symbol, session_date):
    url = UPSTOX[symbol]
    raw, status, ctype = _fetch(url)
    txt = _text(raw)
    parser = TableParser(); parser.feed(raw)
    if f"{symbol} Historical Price" not in txt.upper():
        raise ValueError("Upstox historical section not found")
    for row in parser.rows:
        if len(row) < 6:
            continue
        row_date = _date_any(row[0]); expiry = _date_any(row[1])
        if row_date != session_date or expiry is None:
            continue
        op, high, low, close = (_num(x) for x in row[2:6])
        if None in (op, high, low, close):
            continue
        if not (low <= op <= high and low <= close <= high):
            raise ValueError("invalid Upstox EOD geometry")
        q = MirrorQuote("Upstox", symbol, expiry, _session_timestamp(session_date), close, op, high, low, None, url)
        return q, {"http_status": status, "content_type": ctype, "url": url}
    raise ValueError(f"Upstox EOD row {session_date} not found")


def fetch_et_eod(symbol, expiry, session_date):
    url = ET.format(symbol=symbol, expiry=expiry.isoformat())
    raw, status, ctype = _fetch(url)
    txt = _text(raw)
    parser = TableParser(); parser.feed(raw)
    identity = re.search(
        rf"{re.escape(symbol)}\s+Contract Details.*?\({re.escape(expiry.isoformat())}\)\s+Exchange:\s*MCX",
        txt, re.I,
    )
    if not identity:
        raise ValueError("ET exact contract identity not found")
    for row in parser.rows:
        if len(row) < 8:
            continue
        row_date = _date_any(row[0]); row_expiry = _date_any(row[2])
        if row_date != session_date or row_expiry != expiry:
            continue
        op, high, low, previous_close, abs_change = (_num(x) for x in row[3:8])
        if None in (op, high, low, previous_close, abs_change):
            continue
        close = previous_close + abs_change
        if not (low <= op <= high and low <= close <= high):
            raise ValueError("invalid ET EOD geometry")
        q = MirrorQuote("Economic Times", symbol, expiry, _session_timestamp(session_date), close, op, high, low, previous_close, url)
        return q, {"http_status": status, "content_type": ctype, "url": url}
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
