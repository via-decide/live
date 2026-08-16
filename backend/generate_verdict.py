#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
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
def main():
    now=datetime.now(timezone.utc); result=acquire(ROOT); rows,audits=run(result.records if result.ok else [],now)
    sio=io.StringIO(newline=""); w=csv.DictWriter(sio,fieldnames=CSV_COLUMNS,lineterminator="\n"); w.writeheader(); w.writerows(rows); atomic(ROOT/"verdict.csv",sio.getvalue())
    actionable=sum(r["verdict"] in {"BUY","SELL"} for r in rows); timestamps=[]; source_ages=[]
    for r in result.records:
        v=r.get("source_timestamp")
        if v:
            timestamps.append(str(v))
            try:
                parsed=datetime.fromisoformat(str(v).replace("Z","+00:00")); parsed=parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc); source_ages.append(round((now-parsed.astimezone(timezone.utc)).total_seconds()/60,2))
            except ValueError: pass
    audit={"schema_version":"commodity-verdict-audit-v1","generated_at":now.isoformat(),"workflow_status":"ok" if result.ok else "fail_closed","adapter":result.diagnostics,"commodities":audits,
      "verdict_count":{v:sum(r["verdict"]==v for r in rows) for v in ("BUY","SELL","HOLD","NOT_RECOMMEND")},"actionable_row_count":actionable,"source_timestamps":timestamps,
      "source_age_minutes":max(source_ages) if source_ages else None,"commit_sha":os.getenv("GITHUB_SHA","local")}
    atomic(ROOT/"verdict.audit.json",json.dumps(audit,indent=2,sort_keys=True)+"\n"); atomic(ROOT/"adapter.diagnostics.json",json.dumps(result.diagnostics,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"workflow_status":audit["workflow_status"],"actionable_row_count":actionable,"verdict_count":audit["verdict_count"]},sort_keys=True))
if __name__=="__main__": main()
