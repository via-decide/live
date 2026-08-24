import sys, unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from adapter import historical_mirrors as hm
from adapter import source_adapter as sa


class HistoricalMirrorTests(unittest.TestCase):
    def test_date_formats_used_by_historical_sources(self):
        self.assertEqual(hm._date("19 Aug, 26"), date(2026,8,19))
        self.assertEqual(hm._date("05-Oct-2026"), date(2026,10,5))
        self.assertEqual(hm._date("2026-08-19"), date(2026,8,19))

    def test_upstox_text_row_exact_date_and_expiry(self):
        page='''<html><body><h2>GOLD Historical Price</h2>
        19 Aug, 26 05 Oct, 26 ₹160,100.00 ₹161,000.00 ₹159,900.00 ₹160,500.00 +₹300.00
        </body></html>'''
        with patch.object(hm,"_fetch",return_value=(page,200,"text/html")):
            q,_=hm.fetch_upstox_eod("GOLD",date(2026,8,19))
        self.assertEqual(q.expiry,date(2026,10,5))
        self.assertEqual(q.ltp,160500.0)
        self.assertEqual(q.open,160100.0)

    def test_et_text_row_exact_date_and_expiry(self):
        page='''<html><body>GOLD Contract Details (2026-10-05) Exchange: MCX
        2026-08-19 Gold 05-Oct-2026 160100.00 161000.00 159900.00 160200.00 300.00 0.19
        </body></html>'''
        with patch.object(hm,"_fetch",return_value=(page,200,"text/html")):
            q,_=hm.fetch_et_eod("GOLD",date(2026,10,5),date(2026,8,19))
        self.assertEqual(q.expiry,date(2026,10,5))
        self.assertEqual(q.previous_close,160200.0)
        self.assertEqual(q.ltp,160500.0)

    def test_pair_rejects_material_value_disagreement(self):
        up=hm.MirrorQuote("Upstox","GOLD",date(2026,10,5),datetime(2026,8,19,23,30,tzinfo=hm.IST),160500,160100,161000,159900,None,"u")
        et=hm.MirrorQuote("Economic Times","GOLD",date(2026,10,5),datetime(2026,8,19,23,30,tzinfo=hm.IST),165000,160100,165500,159900,160200,"e")
        per={"sources":{},"checks":{}}
        with patch.object(hm,"fetch_upstox_eod",return_value=(up,{})), patch.object(hm,"fetch_et_eod",return_value=(et,{})):
            with self.assertRaisesRegex(ValueError,"disagree beyond tolerance"):
                hm._verify_pair("GOLD",date(2026,8,19),per)

    def test_source_adapter_uses_historical_only_when_primary_empty(self):
        hist={"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":160500,"source_trade_date":"2026-08-19","verified":True}
        with patch.object(sa,"acquire_latest",return_value=([],{"adapter":"mcx-official-bhavcopy-v1"})), \
             patch.object(sa,"acquire_mirrors",return_value=([],{"adapter":"mcx-multi-source-v1","verified_count":0})), \
             patch.object(sa,"acquire_historical",return_value=([hist],{"adapter":"mcx-historical-mirror-v1","verified_count":1})):
            result=sa.acquire(ROOT)
        self.assertTrue(result.ok)
        self.assertEqual(result.records[0]["commodity"],"GOLD")
        self.assertEqual(result.diagnostics["commodities"]["GOLD"]["source"],"Upstox+EconomicTimes")
        self.assertIn("primary_mirror_attempt",result.diagnostics["mirrors"])

    def test_partial_historical_verification_does_not_void_verified_commodities(self):
        # Regression: a historical-mirror batch missing SOME commodities must still
        # publish the ones that DID cross-source verify; only the missing ones
        # fail-closed to NOT_RECOMMEND. Previously any count != 5 zeroed the batch,
        # turning a single-commodity mirror gap into a total publication outage.
        gold={"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":160500,"source_trade_date":"2026-08-19","verified":True}
        silver={"commodity":"SILVER","instrument":"Silver Futures (05DEC2026)","ltp":258000,"source_trade_date":"2026-08-19","verified":True}
        with patch.object(sa,"acquire_latest",return_value=([],{"adapter":"mcx-official-bhavcopy-v1"})), \
             patch.object(sa,"acquire_mirrors",return_value=([],{"adapter":"mcx-multi-source-v1","verified_count":0})), \
             patch.object(sa,"acquire_historical",return_value=([gold,silver],{"adapter":"mcx-historical-mirror-v1","verified_count":2})):
            result=sa.acquire(ROOT)
        self.assertTrue(result.ok)
        self.assertEqual({r["commodity"] for r in result.records}, {"GOLD","SILVER"})
        commodities=result.diagnostics["commodities"]
        self.assertEqual(commodities["GOLD"]["status"],"CROSS_SOURCE_VERIFIED")
        self.assertEqual(commodities["SILVER"]["status"],"CROSS_SOURCE_VERIFIED")
        for missing in ("CRUDE","ZINC","COPPER"):
            self.assertEqual(commodities[missing]["status"],"NOT_RECOMMEND")


if __name__=="__main__": unittest.main()
