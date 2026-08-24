import os, sys, unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from adapter import licensed_sources as ls
from adapter import licensed_adapter as la


class LicensedSourceTests(unittest.TestCase):
    def setUp(self): self.now=datetime(2026,8,24,3,50,tzinfo=timezone.utc)

    def test_upstox_resolves_exact_front_future(self):
        rows=[
            {"exchange":"MCX","segment":"MCX_FO","instrument_type":"FUT","name":"GOLD","expiry":"2026-10-05","instrument_key":"MCX_FO|1","trading_symbol":"GOLD05OCT26"},
            {"exchange":"MCX","segment":"MCX_FO","instrument_type":"FUT","name":"GOLD","expiry":"2026-12-05","instrument_key":"MCX_FO|2","trading_symbol":"GOLD05DEC26"},
        ]
        c=ls.resolve_upstox(rows,"GOLD",on_date=date(2026,8,21))
        self.assertEqual(c["expiry"],date(2026,10,5)); self.assertEqual(c["instrument_key"],"MCX_FO|1")

    def test_dhan_resolves_exact_expiry(self):
        rows=[{"EXCH_ID":"MCX","SEGMENT":"M","INSTRUMENT":"FUTCOM","SYMBOL_NAME":"SILVER","EXPIRY_DATE":"2026-09-04","SECURITY_ID":"101","TRADING_SYMBOL":"SILVER"}]
        c=ls.resolve_dhan(rows,"SILVER",expiry=date(2026,9,4),on_date=date(2026,8,21))
        self.assertEqual(c["security_id"],"101")

    def test_cross_source_reconcile_is_per_commodity_and_exact_contract(self):
        a=[{"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":100,"source_trade_date":"2026-08-21","verified":True},
           {"commodity":"SILVER","instrument":"Silver Futures (04SEP2026)","ltp":200,"source_trade_date":"2026-08-21","verified":True}]
        b=[{"commodity":"GOLD","instrument":"Gold Futures (05OCT2026)","ltp":100.01,"source_trade_date":"2026-08-21","verified":True},
           {"commodity":"SILVER","instrument":"Silver Futures (05DEC2026)","ltp":200,"source_trade_date":"2026-08-21","verified":True}]
        rows,diag=la._reconcile(a,b,{"adapter":"mcx-upstox-api-v1"},{"adapter":"mcx-dhan-api-v1"})
        self.assertEqual([r["commodity"] for r in rows],["GOLD"])
        self.assertEqual(diag["commodities"]["GOLD"]["status"],"CROSS_SOURCE_VERIFIED")
        self.assertEqual(diag["commodities"]["SILVER"]["status"],"NOT_RECOMMEND")

    def test_missing_api_credentials_fail_closed(self):
        with patch.dict(os.environ,{"UPSTOX_ACCESS_TOKEN":"","DHAN_ACCESS_TOKEN":"","DHAN_CLIENT_ID":""},clear=False):
            with self.assertRaisesRegex(ValueError,"UPSTOX_ACCESS_TOKEN"): ls._upstox_headers()
            with self.assertRaisesRegex(ValueError,"DHAN_ACCESS_TOKEN"): ls._dhan_headers()

    @patch.object(ls,"fetch_dhan_quote")
    @patch.object(ls,"fetch_upstox_quote")
    @patch.object(ls,"fetch_dhan_master")
    @patch.object(ls,"fetch_upstox_master")
    def test_current_session_requires_two_matching_licensed_quotes(self,um,dm,uq,dq):
        um.return_value=([{"exchange":"MCX","segment":"MCX_FO","instrument_type":"FUT","name":"ZINC","expiry":"2026-08-31","instrument_key":"MCX_FO|9","trading_symbol":"ZINC31AUG26"}],{})
        dm.return_value=([{"EXCH_ID":"MCX","SEGMENT":"M","INSTRUMENT":"FUTCOM","SYMBOL_NAME":"ZINC","EXPIRY_DATE":"2026-08-31","SECURITY_ID":"9","TRADING_SYMBOL":"ZINC"}],{})
        uq.return_value=({"ltp":407.75,"previous_close":406.0,"timestamp":None},{})
        dq.return_value=({"ltp":407.70,"previous_close":406.0},{})
        eod=[{"commodity":"ZINC","source_symbol":"ZINC","instrument":"Zinc Futures (31AUG2026)","verified":True}]
        rows,diag=ls.acquire_current(eod,now=datetime(2026,8,24,4,0,tzinfo=timezone.utc))
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]["nominated_source"],"Upstox"); self.assertEqual(diag["verified_count"],1)


if __name__=="__main__": unittest.main()
