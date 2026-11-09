def merge_ranges(rows):
    """
    Merges a list of integer ranges.
    
    Args:
        rows (list): A list of [start, end] integer pairs.
        
    Returns:
        list: A sorted list of merged [start, end] pairs.
        
    Raises:
        ValueError: If input types are incorrect, elements are not integers, 
                    if the range is malformed (start > end), or if 
                    outer/inner structures are wrong.
    """
    # Validate outer input type
    if not isinstance(rows, list):
        raise ValueError("Input 'rows' must be a list.")

    # Create a mutable copy for sorting and processing to avoid mutation
    ranges = []
    for index, row in enumerate(rows):
        # Validate inner element type
        if not isinstance(row, list):
            raise ValueError(f"Element {index} of 'rows' must be a list, found {type(row).__name__}.")
        if len(row) != 2:
            raise ValueError(f"Element {index} of 'rows' must have exactly 2 elements.")

        a, b = row

        # Validate element types (integers only, no bools or floats)
        if type(a) is not int or type(b) is not int:
            raise ValueError(f"Elements in 'rows' must be integers. Invalid value at index {index}: {a}, {b}.")

        # Validate range order (start <= end)
        if a > b:
            raise ValueError(f"Invalid range at index {index}: start ({a}) is greater than end ({b}).")

        ranges.append([a, b])

    # Handle empty input early
    if not ranges:
        return []

    # Sort the ranges by their start value
    ranges.sort(key=lambda x: x[0])

    merged = []
    current_start, current_end = ranges[0]

    for start, end in ranges[1:]:
        # Check for overlap or adjacency.
        # Adjacent means the previous end is exactly one less than the current start.
        # e.g., [1, 2] and [3, 4]. 2 is one less than 3.
        # Overlap means current start <= previous end.
        # Condition for merge: if there is no gap larger than 1 (gap = start - end - 1)
        # then they should be merged.
        if start <= current_end + 1:
            # Merge by extending the current end if the new range goes further
            current_end = max(current_end, end)
        else:
            # No overlap or adjacency, finalize the current range
            merged.append([current_start, current_end])
            current_start, current_end = start, end

    # Append the last range
    merged.append([current_start, current_end])

    return merged
