"""Event-delta implementation of the public occupancy CLI."""
import json
import sys
from collections import defaultdict


def pair(x):
    if not isinstance(x, list) or len(x) != 2 or not all(type(t) is int for t in x) or x[0] >= x[1]:
        raise ValueError("invalid interval")
    return x


def audit(obj):
    if not isinstance(obj, dict):
        raise ValueError("object required")
    lo, hi = pair(obj["window"])
    k = obj["threshold"]
    items = obj["intervals"]
    if type(k) is not int or k < 1 or not isinstance(items, list):
        raise ValueError("invalid threshold or intervals")
    events = defaultdict(int, {lo: 0, hi: 0})
    for original in items:
        a, b = pair(original)
        a = max(a, lo)
        b = min(b, hi)
        if a < b:
            events[a] += 1
            events[b] -= 1
    points = sorted(events)
    depth = peak = covered = 0
    first = None
    alerts = []
    for a, b in zip(points, points[1:]):
        depth += events[a]
        if depth:
            covered += b - a
        if depth > peak:
            peak, first = depth, a
        if depth >= k:
            if alerts and alerts[-1][1] == a:
                alerts[-1][1] = b
            else:
                alerts.append([a, b])
    return dict(covered=covered, peak=peak, first_peak=first, alerts=alerts)


if __name__ == "__main__":
    try:
        print(json.dumps(audit(json.load(sys.stdin))))
    except (ValueError, TypeError, KeyError):
        sys.exit(1)
