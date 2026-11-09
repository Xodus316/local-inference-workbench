"""Independent implementation counting active rows on each elementary segment."""
import json
import sys


def audit(data):
    def interval(value):
        if type(value) != list or len(value) != 2:
            raise ValueError()
        start, stop = value
        if type(start) != int or type(stop) != int or stop <= start:
            raise ValueError()
        return start, stop
    if type(data) != dict or type(data["threshold"]) != int or data["threshold"] <= 0:
        raise ValueError()
    left, right = interval(data["window"])
    if type(data["intervals"]) != list:
        raise ValueError()
    rows = [interval(v) for v in data["intervals"]]
    ticks = sorted({left, right} | {t for row in rows for t in row if left < t < right})
    blocks = [(t, u, sum(x <= t < y for x, y in rows)) for t, u in zip(ticks, ticks[1:])]
    high = max(count for _, _, count in blocks)
    first = next((t for t, _, n in blocks if n == high), None) if high else None
    alarms = []
    start = None
    for t, _, count in blocks:
        if count >= data["threshold"] and start is None:
            start = t
        elif count < data["threshold"] and start is not None:
            alarms.append([start, t])
            start = None
    if start is not None:
        alarms.append([start, right])
    return {"covered": sum(b-a for a,b,n in blocks if n), "peak": high,
            "first_peak": first, "alerts": alarms}


if __name__ == "__main__":
    try:
        print(json.dumps(audit(json.load(sys.stdin))))
    except (KeyError, TypeError, ValueError):
        sys.exit(2)
