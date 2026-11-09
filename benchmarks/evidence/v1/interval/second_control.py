def merge_ranges(rows):
    if not isinstance(rows, list):
        raise ValueError()
    events = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError()
        a, b = row
        if type(a) != int or type(b) != int or b < a:
            raise ValueError()
        events[a] = events.get(a, 0) + 1
        events[b+1] = events.get(b+1, 0) - 1
    output = []
    level = 0
    for position in sorted(events):
        old = level
        level += events[position]
        if not old and level:
            start = position
        if old and not level:
            output.append([start, position-1])
    return output
