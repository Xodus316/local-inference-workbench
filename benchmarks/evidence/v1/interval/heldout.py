import copy
import json
import sys
from solution import merge_ranges

M = 10**30
valid = [
    ([], []), ([[0,0]], [[0,0]]), ([[2,4],[-1,0]], [[-1,0],[2,4]]),
    ([[5,6],[1,2],[3,4]], [[1,6]]), ([[5,6],[1,5]], [[1,6]]),
    ([[1,10],[2,3],[6,7]], [[1,10]]), ([[2,3],[2,3],[3,3]], [[2,3]]),
    ([[-5,-3],[-2,1]], [[-5,1]]), ([[8,8],[5,5],[6,6]], [[5,6],[8,8]]),
    ([[0,M],[M+1,M+5]], [[0,M+5]]), ([[-M,-M],[M,M]], [[-M,-M],[M,M]]),
    ([[7,8],[0,0],[3,4],[4,5],[1,2]], [[0,5],[7,8]]),
]
bad = [None, (), 'abc', {}, [()], [(1,2)], [[0]], [[1,2,3]], [[3,2]], [[False,1]], [[0,True]],
       [[1.0,2]], [[0,1.0]], [['1',2]], [[1,2],[4,None]], [[[1],2]], [0]]
results = []
for i, (arg, expected) in enumerate(valid):
    frozen = copy.deepcopy(arg)
    try:
        out = merge_ranges(arg)
        correct = type(out) is list and all(type(p) is list and all(type(v) is int for v in p) for p in out)
        correct = correct and out == expected and arg == frozen
    except Exception:
        correct = False
    results.append([f'valid_{i}', correct])
for i, arg in enumerate(bad):
    frozen = copy.deepcopy(arg)
    try:
        merge_ranges(arg); correct = False
    except ValueError:
        correct = arg == frozen
    except Exception:
        correct = False
    results.append([f'invalid_{i}', correct])
print(json.dumps(results))
sys.exit(0 if all(v for _,v in results) else 1)
