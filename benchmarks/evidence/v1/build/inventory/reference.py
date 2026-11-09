"""Evaluator-only reference, never provided to Build models."""
import json
import re
import sys


def name(value):
    if type(value) is not str or re.fullmatch('[a-z]+', value) is None:
        raise ValueError('invalid name')


def allocate(data):
    if type(data) is not dict or type(data['stock']) is not dict or type(data['orders']) is not list:
        raise ValueError('invalid root')
    stock = dict(data['stock'])
    for k,v in stock.items():
        name(k)
        if type(v) is not int or v < 0: raise ValueError('invalid stock')
    seen = set()
    for o in data['orders']:
        if type(o) is not dict: raise ValueError('invalid order')
        name(o['id'])
        if o['id'] in seen: raise ValueError('duplicate id')
        seen.add(o['id'])
        if type(o['priority']) is not int or type(o['items']) is not dict or not o['items']:
            raise ValueError('invalid priority or items')
        for k,v in o['items'].items():
            name(k)
            if type(v) is not int or v <= 0: raise ValueError('invalid quantity')
    fulfilled,rejected = [],[]
    for o in sorted(data['orders'], key=lambda o:(-o['priority'],o['id'])):
        if all(stock.get(k,0)>=v for k,v in o['items'].items()):
            for k,v in o['items'].items():stock[k]-=v
            fulfilled.append(o['id'])
        else:rejected.append(o['id'])
    return dict(fulfilled=fulfilled,rejected=rejected,remaining=[[k,stock[k]] for k in sorted(stock)])


if __name__=='__main__':
    try: print(json.dumps(allocate(json.load(sys.stdin))))
    except (ValueError,TypeError,KeyError):sys.exit(1)
