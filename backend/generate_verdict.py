#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from adapter.source_adapter import acquire
from verdict_engine import CSV_COLUMNS, run
ROOT=Path(__file__).resolve().parents[1]

def atomic(path:Path,text:str):
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as f: f.write(text)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def load_eod_result():
    configured=os.getenv("EOD_VERIFIED_SOURCE_PATH","").strip()
    if not configured: return acquire(ROOT),None
    p=Path(configured); p=p if p.is_absolute() else (ROOT/p)
    obj=json.loads(p.read_text("utf-8"))
    if not isinstance(obj,dict) or obj.get("schema_version")!="commodity-eod-verified-input-v1": raise ValueError("invalid governed EOD snapshot schema")
    records=obj.get("records"); diagnostics=obj.get("diagnostics")
    if not isinstance(records,list) or not isinstance(diagnostics,dict): raise ValueError("invalid governed EOD snapshot payload")
    if diagnostics.get("adapter")!="mcx-multi-source-v1": raise ValueError("governed EOD snapshot adapter mismatch")
    return SimpleNamespace(ok=bool(obj.get("ok")),records=records,diagnostics=diagnostics),str(p)

def load_current_observations():
    configured=os.getenv("CURRENT_PRICE_SOURCE_PATH","").strip()
    if not configured: return [],None,None
    p=Path(configured); p=p if p.is_absolute() else (ROOT/p)
    obj=json.loads(p.read_text("utf-8"))
    rows=obj.get("records") if isinstance(obj,dict) else obj
    diagnostics=obj.get("diagnostics") if isinstance(obj,dict) else None
    if not isinstance(rows,list): raise ValueError("current price input must be a list or {records:[...]}")
    required={"commodity","verified","instrument"}
    normalized=[]
    for i,row in enumerate(rows):
        if not isinstance(row,dict): raise ValueError(f"current price row {i+1} must be an object")
        if not required.issubset(row): raise ValueError(f"current price row {i+1} missing commodity/verified/instrument")
        if "price" not in row and "ltp" not in row: raise ValueError(f"current price row {i+1} missing price")
        if "timestamp" not in row and "source_timestamp" not in row: raise ValueError(f"current price row {i+1} missing timestamp")
        normalized.append(dict(row))
    return normalized,str(p),diagnostics

def main():
    now=datetime.now(timezone.utc); result,eod_source=load_eod_result(); current,current_source,current_diag=load_current_observations()
    require_current=os.getenv("REQUIRE_CURRENT_SESSION_OBSERVATION","0").strip().lower() in {"1","true","yes"}
    rows,audits=run(result.records if result.ok else [],now,current_observations=current,require_current=require_current)
    sio=io.StringIO(newline=""); w=csv.DictWriter(sio,fieldnames=CSV_COLUMNS,lineterminator="\n"); w.writeheader(); w.writerows(rows); atomic(ROOT/"verdict.csv",sio.getvalue())
    actionable=sum(r["verdict"] in {"BUY","SELL"} for r in rows); timestamps=[]; source_ages=[]
    for r in result.records:
        v=r.get("source_timestamp")
        if v:
            timestamps.append(str(v))
            try:
                parsed=datetime.fromisoformat(str(v).replace("Z","+00:00")); parsed=parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc); source_ages.append(round((now-parsed.astimezone(timezone.utc)).total_seconds()/60,2))
            except ValueError: pass
    current_timestamps=[str(r.get("timestamp",r.get("source_timestamp"))) for r in current]
    audit={"schema_version":"commodity-verdict-audit-v2","engine_state_model":"EOD_LEVEL_FREEZE_THEN_CURRENT_SESSION_EVALUATION","generated_at":now.isoformat(),"workflow_status":"ok" if result.ok else "fail_closed","adapter":result.diagnostics,"commodities":audits,
      "verdict_count":{v:sum(r["verdict"]==v for r in rows) for v in ("BUY","SELL","HOLD","NOT_RECOMMEND")},"actionable_row_count":actionable,"source_timestamps":timestamps,
      "source_age_minutes":max(source_ages) if source_ages else None,"eod_snapshot_source":eod_source,"current_price_source":current_source,"current_observation_count":len(current),"current_observation_timestamps":current_timestamps,
      "require_current_session_observation":require_current,"current_price_diagnostics":current_diag,"commit_sha":os.getenv("GITHUB_SHA","local")}
    atomic(ROOT/"verdict.audit.json",json.dumps(audit,indent=2,sort_keys=True)+"\n"); atomic(ROOT/"adapter.diagnostics.json",json.dumps(result.diagnostics,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"workflow_status":audit["workflow_status"],"engine_state_model":audit["engine_state_model"],"current_observation_count":len(current),"require_current_session_observation":require_current,"actionable_row_count":actionable,"verdict_count":audit["verdict_count"]},sort_keys=True))
if __name__=="__main__": main()
