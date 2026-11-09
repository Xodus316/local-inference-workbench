"""Independent history-scan control; does not maintain or purge live state."""
import json
import sys


def audit(obj):
    if type(obj) is not dict or type(obj.get('events')) is not list:
        raise ValueError('invalid root')
    rows = []
    for index, item in enumerate(obj['events']):
        if type(item) is not dict:
            raise ValueError('invalid event')
        t, k, op = item['time'], item['key'], item['op']
        if type(t) is not int or t < 0 or type(k) is not str or not k or any(c not in 'abcdefghijklmnopqrstuvwxyz' for c in k):
            raise ValueError('invalid time or key')
        if type(op) is not str or op not in ['get', 'put', 'delete']:
            raise ValueError('invalid operation')
        if op == 'put':
            ttl, val = item['ttl'], item['value']
            if type(val) is not int or not (ttl is None or (type(ttl) is int and ttl >= 0)):
                raise ValueError('invalid put')
        rows.append((t, index, item))
    rows.sort(key=lambda r: (r[0], r[1]))
    history = []
    def lookup(key, now):
        for t, index, event in reversed(history):
            if event['key'] != key or event['op'] == 'get':
                continue
            if event['op'] == 'delete':
                return None
            lifetime = event['ttl']
            return event['value'] if lifetime is None or now - t < lifetime else None
        return None
    reads = []
    for t, index, event in rows:
        if event['op'] == 'get':
            reads.append([t, event['key'], lookup(event['key'], t)])
        history.append((t, index, event))
    end = rows[-1][0] if rows else 0
    live = []
    for key in sorted({r[2]['key'] for r in rows}):
        value = lookup(key, end)
        if value is not None:
            live.append([key, value])
    return {'reads': reads, 'live': live}


if __name__ == '__main__':
    try:
        print(json.dumps(audit(json.load(sys.stdin))))
    except (ValueError, TypeError, KeyError):
        sys.exit(1)
