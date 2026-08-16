import json, subprocess, sys, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from verdict_engine import CSV_COLUMNS, COMMODITIES, run
def rec(c,now,**kw):
    base={"commodity":c,"instrument":f"{c} Futures","ltp":110,"breakout_level":100,"breakdown_level":90,"buy_invalidation_below":95,"sell_invalidation_above":96,"atr":2,"trend_bias":"BUY","source_timestamp":now.isoformat(),"verified":True};base.update(kw);return base
class EngineTests(unittest.TestCase):
    def setUp(self): self.now=datetime(2026,8,17,3,15,tzinfo=timezone.utc)
    def test_missing_source_fails_closed_all_five(self):
        rows,_=run([],self.now);self.assertEqual(len(rows),5);self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))
    def test_stale_fails_closed(self):
        rows,_=run([rec(c,self.now,source_timestamp=(self.now-timedelta(days=2)).isoformat()) for c in COMMODITIES],self.now,max_age_min=1440);self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))
    def test_unverified_fails_closed(self):
        rows,_=run([rec(c,self.now,verified=False) for c in COMMODITIES],self.now);self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))
    def test_bad_geometry_fails_closed(self):
        rows,_=run([rec(c,self.now,buy_invalidation_below=101) for c in COMMODITIES],self.now);self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))
    def test_buy_trigger_and_contract(self):
        rows,_=run([rec(c,self.now) for c in COMMODITIES],self.now);self.assertTrue(all(r["verdict"]=="BUY" for r in rows));self.assertEqual(tuple(rows[0].keys()),CSV_COLUMNS)
    def test_conflicting_trend_fails_closed(self):
        rows,_=run([rec(c,self.now,trend_bias="SELL") for c in COMMODITIES],self.now);self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))
    def test_python_js_live_parity_on_published_buy(self):
        rows,_=run([rec(c,self.now) for c in COMMODITIES],self.now);row=rows[0];script="const cv=require('./assets/verdict-engine.js');const row=JSON.parse(process.argv[1]);console.log(JSON.stringify(cv.computeLiveUpdate(row,row.ltp)));";p=subprocess.run(["node","-e",script,json.dumps(row)],cwd=ROOT,text=True,capture_output=True,check=True);self.assertEqual(json.loads(p.stdout)["verdict"],row["verdict"])
if __name__=="__main__":unittest.main()
