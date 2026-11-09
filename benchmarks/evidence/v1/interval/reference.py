def merge_ranges(rows):
    if type(rows) is not list:
        raise ValueError()
    for p in rows:
        if type(p) is not list or len(p) != 2 or not all(type(n) is int for n in p) or p[0] > p[1]:
            raise ValueError()
    result = []
    for start, end in sorted(rows):
        if result and start <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
