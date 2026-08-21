import json, subprocess, sys, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"backend"))
from verdict_engine import CSV_COLUMNS, COMMODITIES, run


def eod(c,now,**kw):
    # Valid completed EOD bar: H=110, L=90, C=100 => P=100, R1=110, S1=90.
    base={"commodity":c,"instrument":f"{c} Futures (31AUG2026)","ltp":100,"breakout_level":110,"breakdown_level":90,
          "buy_invalidation_below":100,"sell_invalidation_above":100,"atr":None,"trend_bias":"NEUTRAL",
          "source_timestamp":(now-timedelta(hours=9)).isoformat(),"source_trade_date":(now-timedelta(days=1)).date().isoformat(),"verified":True}
    base.update(kw); return base


def live(c,now,price,**kw):
    base={"commodity":c,"price":price,"timestamp":(now-timedelta(minutes=1)).isoformat(),"verified":True}
    base.update(kw); return base


class EngineTests(unittest.TestCase):
    def setUp(self): self.now=datetime(2026,8,21,3,31,tzinfo=timezone.utc)  # 09:01 IST

    def test_missing_source_fails_closed_all_five(self):
        rows,_=run([],self.now); self.assertEqual(len(rows),5); self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))

    def test_stale_eod_fails_closed(self):
        rows,_=run([eod(c,self.now,source_timestamp=(self.now-timedelta(days=2)).isoformat()) for c in COMMODITIES],self.now,max_age_min=1440)
        self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))

    def test_unverified_eod_fails_closed(self):
        rows,_=run([eod(c,self.now,verified=False) for c in COMMODITIES],self.now)
        self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))

    def test_bad_geometry_fails_closed(self):
        rows,_=run([eod(c,self.now,buy_invalidation_below=111) for c in COMMODITIES],self.now)
        self.assertTrue(all(r["verdict"]=="NOT_RECOMMEND" for r in rows))

    def test_eod_freeze_without_current_price_is_hold(self):
        rows,audit=run([eod(c,self.now) for c in COMMODITIES],self.now)
        self.assertTrue(all(r["verdict"]=="HOLD" for r in rows))
        self.assertTrue(all(a["reason"]=="levels frozen; awaiting current-session price" for a in audit))

    def test_regression_hold_to_buy_requires_separate_timestamped_observation(self):
        source=eod("GOLD",self.now,trend_bias="BUY")
        pre,_=run([source],self.now)
        self.assertEqual(pre[0]["verdict"],"HOLD")
        # Buffer is 0.05 from the EOD reference 100, so 110.10 clears frozen R1+buffer.
        post,audit=run([source],self.now,current_observations=[live("GOLD",self.now,110.10)])
        self.assertEqual(post[0]["verdict"],"BUY")
        self.assertEqual(post[0]["ltp"],"110.1")
        self.assertIn("current-session observation",audit[0]["reason"])

    def test_regression_hold_to_sell_requires_separate_timestamped_observation(self):
        source=eod("SILVER",self.now,trend_bias="SELL")
        pre,_=run([source],self.now)
        self.assertEqual(pre[1]["verdict"],"HOLD")
        post,audit=run([source],self.now,current_observations=[live("SILVER",self.now,89.90)])
        self.assertEqual(post[1]["verdict"],"SELL")
        self.assertEqual(post[1]["ltp"],"89.9")
        self.assertIn("current-session observation",audit[1]["reason"])

    def test_same_eod_close_cannot_be_reused_as_current_observation(self):
        source=eod("GOLD",self.now,trend_bias="BUY")
        rows,_=run([source],self.now,current_observations=[{"commodity":"GOLD","price":120,"timestamp":source["source_timestamp"],"verified":True}])
        self.assertEqual(rows[0]["verdict"],"NOT_RECOMMEND")
        self.assertEqual(rows[0]["confidence_score_percent"],"0")

    def test_current_observation_must_be_verified_and_fresh(self):
        source=eod("GOLD",self.now,trend_bias="BUY")
        bad_unverified=live("GOLD",self.now,120,verified=False)
        rows,_=run([source],self.now,current_observations=[bad_unverified]); self.assertEqual(rows[0]["verdict"],"NOT_RECOMMEND")
        stale=live("GOLD",self.now,120,timestamp=(self.now-timedelta(hours=2)).isoformat())
        rows,_=run([source],self.now,current_observations=[stale],current_max_age_min=30); self.assertEqual(rows[0]["verdict"],"NOT_RECOMMEND")

    def test_current_price_inside_frozen_range_remains_hold(self):
        source=eod("GOLD",self.now,trend_bias="BUY")
        rows,_=run([source],self.now,current_observations=[live("GOLD",self.now,105)])
        self.assertEqual(rows[0]["verdict"],"HOLD")

    def test_signal_trend_conflict_fails_closed(self):
        source=eod("GOLD",self.now,trend_bias="SELL")
        rows,_=run([source],self.now,current_observations=[live("GOLD",self.now,120)])
        self.assertEqual(rows[0]["verdict"],"NOT_RECOMMEND")

    def test_python_js_parity_on_published_buy(self):
        source=eod("GOLD",self.now,trend_bias="BUY")
        rows,_=run([source],self.now,current_observations=[live("GOLD",self.now,110.10)])
        row=rows[0]
        script="const cv=require('./assets/verdict-engine.js');const row=JSON.parse(process.argv[1]);console.log(JSON.stringify(cv.computeLiveUpdate(row,row.ltp)));"
        p=subprocess.run(["node","-e",script,json.dumps(row)],cwd=ROOT,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(p.stdout)["verdict"],"BUY")
        self.assertEqual(tuple(row.keys()),CSV_COLUMNS)

if __name__=="__main__": unittest.main()
