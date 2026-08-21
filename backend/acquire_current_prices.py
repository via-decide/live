#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from adapter.source_adapter import acquire
from adapter.current_session import acquire_current

ROOT=Path(__file__).resolve().parents[1]

def main():
    current_out=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/current-prices.json')
    eod_out=Path(sys.argv[2]) if len(sys.argv)>2 else Path('/tmp/eod-verified.json')
    now=datetime.now(timezone.utc)
    eod=acquire(ROOT)
    eod_payload={"schema_version":"commodity-eod-verified-input-v1","generated_at":now.isoformat(),"ok":bool(eod.ok),"records":eod.records,"diagnostics":eod.diagnostics}
    eod_out.write_text(json.dumps(eod_payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    records,diagnostics=acquire_current(eod.records if eod.ok else [],now=now)
    payload={"schema_version":"commodity-current-price-input-v1","generated_at":now.isoformat(),"records":records,"diagnostics":diagnostics,"eod_snapshot_path":str(eod_out)}
    current_out.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"eod_verified_count":eod.diagnostics.get("verified_count",0),"current_verified_count":diagnostics.get("verified_count",0),"record_count":len(records),"current_path":str(current_out),"eod_path":str(eod_out)},sort_keys=True))

if __name__=='__main__': main()
