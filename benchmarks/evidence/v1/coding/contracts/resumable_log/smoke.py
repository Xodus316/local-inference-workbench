from candidate import process_log
from pathlib import Path
import tempfile,json
with tempfile.TemporaryDirectory() as d:
    log=Path(d)/"events.jsonl";state=Path(d)/"state.json"
    log.write_text(json.dumps({"id":"e1","account":"a","delta":3})+"\n",encoding="utf-8")
    assert process_log(log,state)["totals"]=={"a":3}
    assert process_log(log,state)["processed"]==0
