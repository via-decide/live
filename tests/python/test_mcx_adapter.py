import sys, unittest
from datetime import date, datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from adapter.mcx_bhavcopy import FetchResult, acquire_latest

FIELDS=["Date","InstrumentName","Symbol","ExpiryDate","Open","High","Low","Close","PCP","Volume","OI"]
def raw(symbol, expiry="31AUG2026", close=105, pcp=100, vol=100):
    return {"Date":"14 Aug 2026","InstrumentName":"FUTCOM","Symbol":symbol,"ExpiryDate":expiry,"Open":100,"High":110,"Low":90,"Close":close,"PCP":pcp,"Volume":vol,"OI":50}

class MCXAdapterTests(unittest.TestCase):
    def test_latest_available_and_exact_five(self):
        def fetch(d):
            rows=[] if d != date(2026,8,14) else [raw(x) for x in ("GOLD","SILVER","CRUDEOIL","ZINC","COPPER")]
            return FetchResult(d,rows,200,"application/json",FIELDS)
        rows,diag=acquire_latest(datetime(2026,8,17,3,15,tzinfo=timezone.utc),8,fetch)
        self.assertEqual(diag["selected_trade_date"],"2026-08-14"); self.assertEqual({r["commodity"] for r in rows},{"GOLD","SILVER","CRUDE","ZINC","COPPER"}); self.assertTrue(all(r["verified"] for r in rows))
    def test_no_mini_substitution(self):
        def fetch(d): return FetchResult(d,[raw("GOLDM")],200,"application/json",FIELDS)
        rows,diag=acquire_latest(datetime(2026,8,17,3,15,tzinfo=timezone.utc),1,fetch)
        self.assertEqual(rows,[]); self.assertEqual(diag["selected_contracts"]["GOLD"]["status"],"missing")
    def test_nearest_positive_volume_contract(self):
        def fetch(d): return FetchResult(d,[raw("GOLD","30SEP2026",vol=500),raw("GOLD","31AUG2026",vol=10)]+[raw(x) for x in ("SILVER","CRUDEOIL","ZINC","COPPER")],200,"application/json",FIELDS)
        rows,_=acquire_latest(datetime(2026,8,17,3,15,tzinfo=timezone.utc),1,fetch)
        gold=[r for r in rows if r["commodity"]=="GOLD"][0]; self.assertIn("31AUG2026",gold["instrument"])
    def test_pivot_derivation(self):
        def fetch(d): return FetchResult(d,[raw(x) for x in ("GOLD","SILVER","CRUDEOIL","ZINC","COPPER")],200,"application/json",FIELDS)
        rows,_=acquire_latest(datetime(2026,8,17,3,15,tzinfo=timezone.utc),1,fetch); g=rows[0]
        p=(110+90+105)/3; self.assertAlmostEqual(g["buy_invalidation_below"],p); self.assertAlmostEqual(g["breakout_level"],2*p-90); self.assertAlmostEqual(g["breakdown_level"],2*p-110)
if __name__=="__main__": unittest.main()
