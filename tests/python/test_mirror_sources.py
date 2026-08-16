import sys, unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from adapter import mirror_sources as ms
from adapter.source_adapter import _reconcile

class MirrorTests(unittest.TestCase):
    def test_5paisa_parses_exact_contract_and_timestamp(self):
        page='''<html><body><h1>Gold Price Today</h1><div>Bullion</div><div>Oct 5 2026</div><div>Oct 5 2026 Dec 4 2026</div><div>₹144,767.00</div><div>As on 14 August, 2026 | 23:31</div><div>Today's Low 143,471</div><div>Today's High 146,608</div><div>Open Price | 143,900</div><div>Previous Close | 143,283</div></body></html>'''
        with patch.object(ms,"_fetch",return_value=(page,200,"text/html")):
            q,_=ms.fetch_5paisa("GOLD")
        self.assertEqual(q.expiry,date(2026,10,5)); self.assertEqual(q.trade_time.date(),date(2026,8,14)); self.assertEqual(q.ltp,144767.0)

    def test_icici_active_keeps_full_and_mini_distinct(self):
        page='''<table><tr><th>Symbol</th><th>Strike</th><th>Expiry</th><th>LTP</th><th>CHG</th></tr><tr><td>GOLD-05-OCT-26</td><td>0</td><td>Oct 05,2026</td><td>144,767.00</td><td>1,484.00</td></tr><tr><td>GOLDM-05-OCT-26</td><td>0</td><td>Oct 05,2026</td><td>144,831.00</td><td>1,361.00</td></tr></table>'''
        with patch.object(ms,"_fetch",return_value=(page,200,"text/html")):
            rows,_=ms.fetch_icici_active()
        self.assertEqual(rows["GOLD-05-OCT-26"]["ltp"],144767.0); self.assertEqual(rows["GOLDM-05-OCT-26"]["ltp"],144831.0)

    def test_value_tolerance_rejects_material_disagreement(self):
        ok,_=ms._agree("COPPER",1338.5,1338.7); self.assertTrue(ok)
        ok,_=ms._agree("COPPER",1338.5,1360.0); self.assertFalse(ok)

    def test_reconcile_is_per_commodity_fail_closed(self):
        good={"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":144767,"source_trade_date":"2026-08-14","verified":True}
        silver={"commodity":"SILVER","instrument":"Silver Futures (04SEP2026)","ltp":219853,"source_trade_date":"2026-08-14","verified":True}
        rows,diag=_reconcile([], [good,silver], {}, {"verified_count":2})
        self.assertEqual({r["commodity"] for r in rows},{"GOLD","SILVER"}); self.assertEqual(diag["commodities"]["CRUDE"]["status"],"NOT_RECOMMEND")

    def test_mcx_and_mirror_contract_mismatch_fails_closed(self):
        a={"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":144767,"source_trade_date":"2026-08-14","verified":True}
        b={"commodity":"GOLD","instrument":"Gold Futures (04DEC2026)","ltp":144767,"source_trade_date":"2026-08-14","verified":True}
        rows,diag=_reconcile([a],[b],{},{}); self.assertEqual(rows,[]); self.assertEqual(diag["commodities"]["GOLD"]["reason"],"MCX and mirrors disagree")

    def test_monday_premarket_anchors_to_friday_session(self):
        local=datetime(2026,8,17,4,24,tzinfo=ms.IST)
        self.assertEqual(ms._last_completed_session(local),date(2026,8,14))

    def test_full_day_holiday_is_not_a_completed_session(self):
        local=datetime(2026,1,27,8,45,tzinfo=ms.IST)
        self.assertEqual(ms._last_completed_session(local),date(2026,1,23))

    def test_normalized_record_uses_session_date_not_page_date(self):
        q=ms.MirrorQuote("5paisa","GOLD",date(2026,10,5),datetime(2026,8,17,4,20,tzinfo=ms.IST),154522,153400,156000,152900,152000,"x")
        r=ms._normalized("GOLD",q,date(2026,8,14))
        self.assertEqual(r["source_trade_date"],"2026-08-14"); self.assertTrue(r["source_timestamp"].startswith("2026-08-14T23:30")); self.assertTrue(r["source_observed_at"].startswith("2026-08-17"))

if __name__=="__main__": unittest.main()
