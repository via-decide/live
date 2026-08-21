import sys, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from adapter import current_session as cs


def eod(symbol,display,expiry):
    return {"commodity":display,"source_symbol":symbol,"instrument":f"{display.title()} Futures ({expiry})","verified":True}


def active_rows(records,price=100.0):
    rows={}
    for r in records:
        symbol=r["source_symbol"]; expiry=cs._expiry_from_instrument(r["instrument"])
        rows[cs._contract_token(symbol,expiry)]={"ltp":price,"change":0.0,"row":[]}
    return rows


class CurrentSessionTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,8,21,3,36,tzinfo=timezone.utc)  # 09:06 IST
        self.records=[
            eod("GOLD","GOLD","05OCT2026"),eod("SILVER","SILVER","04SEP2026"),eod("CRUDEOIL","CRUDE","21SEP2026"),
            eod("ZINC","ZINC","31AUG2026"),eod("COPPER","COPPER","31AUG2026")]

    @patch.object(cs,"fetch_et_current")
    @patch.object(cs,"fetch_icici_active")
    def test_five_exact_contracts_verify(self,icici,et):
        ts=datetime(2026,8,21,9,5,tzinfo=cs.IST)
        icici.return_value=(active_rows(self.records,100.0),{"url":"https://example.test/icici"})
        et.side_effect=lambda symbol,expiry,now,max_age_min:(100.05,ts,{"expiry":expiry.isoformat()})
        rows,diag=cs.acquire_current(self.records,self.now)
        self.assertEqual(diag["verified_count"],5); self.assertEqual(len(rows),5)
        self.assertTrue(all(r["verified"] for r in rows)); self.assertTrue(all("(" in r["instrument"] for r in rows))
        self.assertTrue(all(r["nominated_source"]=="Economic Times" for r in rows))

    @patch.object(cs,"fetch_et_current")
    @patch.object(cs,"fetch_icici_active")
    def test_cross_source_disagreement_fails_closed(self,icici,et):
        ts=datetime(2026,8,21,9,5,tzinfo=cs.IST)
        icici.return_value=(active_rows(self.records[:1],100.0),{"url":"https://example.test/icici"})
        et.return_value=(500.0,ts,{})
        rows,diag=cs.acquire_current(self.records[:1],self.now)
        self.assertEqual(rows,[]); self.assertEqual(diag["verified_count"],0)
        self.assertEqual(diag["commodities"]["GOLD"]["status"],"NOT_RECOMMEND")

if __name__=="__main__": unittest.main()
