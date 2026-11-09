def merge_ranges(rows):
    """Merge integer ranges given as a list of [start, end] lists.

    Args:
        rows: List of [start, end] pairs representing inclusive integer ranges.
              start and end must be integers (not bool), start <= end.
    Returns:
        Sorted list of [start, end] lists representing the union of all ranges,
        with overlapping and adjacent ranges merged.
    Raises:
        ValueError: If any input is malformed, wrong type, or start > end.
    """
    # Validate outer type
    if not isinstance(rows, list):
        raise ValueError("Input must be a list")

    validated = []
    for row in rows:
        # Validate inner type and length
        if not isinstance(row, list):
            raise ValueError("Each element must be a list")
        if len(row) != 2:
            raise ValueError("Each inner list must contain exactly two integers")
        start, end = row[0], row[1]
        # Validate types: integers only, booleans and floats are invalid
        if not (isinstance(start, int) and not isinstance(start, bool)):
            raise ValueError("Start must be an integer")
        if not (isinstance(end, int) and not isinstance(end, bool)):
            raise ValueError("End must be an integer")
        # Require start <= end
        if start > end:
            raise ValueError("Start must be less than or equal to end")
        validated.append((start, end))

    # Sort by start, then end – runtime depends on n, not on integer span
    validated.sort(key=lambda x: (x[0], x[1]))

    # Empty input
    if not validated:
        return []

    # Merge overlapping and adjacent ranges.
    # Adjacent means one ends exactly one less than the next starts.
    # Condition: next.start <= current.end + 1 triggers a merge.
    result = []
    cur_start, cur_end = validated[0]

    for start, end in validated[1:]:
        if start <= cur_end + 1:
            # Overlap or adjacent – extend current range if needed
            if end > cur_end:
                cur_end = end
        else:
            result.append([cur_start, cur_end])
            cur_start, cur_end = start, end

    result.append([cur_start, cur_end])
    return result
