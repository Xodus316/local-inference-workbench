"""Check two trusted controls and prove each seeded defect observable before generation."""
import itertools
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from segments import audit as segments
from sweep import audit as sweep

ROOT = Path(__file__).resolve().parent


def grid(data):
    lo, hi = data["window"]
    counts = [sum(a <= t < b for a, b in data["intervals"]) for t in range(lo, hi)]
    peak = max(counts)
    hot = [n >= data["threshold"] for n in counts]
    edges = [lo+i for i in range(len(hot)+1) if ([False]+hot)[i] != (hot+[False])[i]]
    return {"covered":sum(n > 0 for n in counts), "peak":peak,
            "first_peak":lo+counts.index(peak) if peak else None,
            "alerts":[list(pair) for pair in zip(edges[::2],edges[1::2])]}


def cli(path, stdin):
    p = subprocess.run([sys.executable, str(path)], input=stdin, text=True,
                       capture_output=True, timeout=3)
    if p.returncode:
        return ("invalid", p.stdout)
    return ("ok", json.loads(p.stdout))


def main():
    held = json.loads((ROOT/"heldout.json").read_text())
    n = 0
    intervals = list(itertools.combinations(range(-2,5), 2))
    for size in range(3):
        for rows in itertools.product(intervals, repeat=size):
            for k in (1,2,3):
                data = {"window":[-1,3],"intervals":[list(x) for x in rows],"threshold":k}
                assert sweep(data) == segments(data) == grid(data)
                n += 1
    for path in (ROOT/"sweep.py", ROOT/"segments.py"):
        for data, oracle in held["cases"].values():
            assert cli(path,json.dumps(data)) == (("ok",oracle) if oracle is not None else ("invalid",""))
        for invalid in ('{', 'null','{"threshold":true}', '{"window":[0,2],"threshold":1.0,"intervals":[]}'):
            assert cli(path,invalid) == ("invalid","")
    code = (ROOT/"sweep.py").read_text()
    with TemporaryDirectory() as temp:
        path = Path(temp)/"solution.py"
        for m in held["mutations"]:
            assert code.count(m["original"]) == 1
            path.write_text(code.replace(m["original"],m["replacement"]))
            compile(path.read_text(),str(path),"exec")
            data, oracle = held["cases"][m["witness"]]
            assert cli(path,json.dumps(data)) != (("ok",oracle) if oracle is not None else ("invalid",""))
    print(json.dumps({"valid_inputs_checked":n, "controls":2, "witnessed_mutations":len(held["mutations"])}))


if __name__ == "__main__":
    main()
