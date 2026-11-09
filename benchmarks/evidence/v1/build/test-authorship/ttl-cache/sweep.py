"""Mutable-state control for the public timestamped cache CLI."""
import json
import re
import sys


def integer(value):
    return type(value) is int


def validate(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get('events'), list):
        raise ValueError('events array required')
    events = obj['events']
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('event object required')
        t, key, op = event['time'], event['key'], event['op']
        if not integer(t) or t < 0:
            raise ValueError('nonnegative integer time required')
        if not isinstance(key, str) or re.fullmatch('[a-z]+', key) is None:
            raise ValueError('lowercase ASCII key required')
        if op not in ('put', 'get', 'delete'):
            raise ValueError('unknown operation')
        if op == 'put':
            ttl = event['ttl']
            if not integer(event['value']) or (ttl is not None and (not integer(ttl) or ttl < 0)):
                raise ValueError('integer value and nonnegative TTL or null required')
    return events


def audit(obj):
    events = validate(obj)
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1]['time'], pair[0]))
    store, reads = {}, []
    def expire(t):
        for key in list(store):
            if store[key][1] is not None and store[key][1] <= t:
                del store[key]
    for index, event in ordered:
        t, key, op = event['time'], event['key'], event['op']
        expire(t)
        if op == 'put':
            ttl = event['ttl']
            expiry = None if ttl is None else t + ttl
            store[key] = (event['value'], expiry)
        elif op == 'delete':
            store.pop(key, None)
        else:
            reads.append([t, key, store.get(key, (None, None))[0]])
    expire(ordered[-1][1]['time'] if ordered else 0)
    live = [[key, store[key][0]] for key in sorted(store)]
    return {'reads': reads, 'live': live}


if __name__ == '__main__':
    try:
        print(json.dumps(audit(json.load(sys.stdin))))
    except (ValueError, TypeError, KeyError):
        sys.exit(1)
