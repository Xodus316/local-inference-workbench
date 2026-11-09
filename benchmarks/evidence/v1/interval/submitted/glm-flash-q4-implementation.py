def merge_ranges(rows):
    """
    Merges a list of inclusive integer ranges.
    
    Args:
        rows: A list of pairs [start, end] where start <= end and types are integers.
              Empty list returns empty list.
              Raises ValueError for invalid types or malformed pairs.
    
    Returns:
        A sorted list of merged [start, end] lists. Merges overlaps and adjacent ranges.
    """
    # Validate outer container
    if not isinstance(rows, list):
        raise ValueError("Input must be a list")
    
    def validate_pair(pair):
        if not isinstance(pair, list):
            raise ValueError("Each pair must be a list")
        if len(pair) != 2:
            raise ValueError("Each pair must have exactly two elements")
        # Use type() check to exclude booleans (True/False) which are subclasses of int
        if type(pair[0]) is not int or type(pair[1]) is not int:
            raise ValueError("Elements must be integers")
        if pair[0] > pair[1]:
            raise ValueError("Start must be less than or equal to end")
    
    # Create a deep copy of the input to prevent mutation
    intervals = [list(pair) for pair in rows]
    
    # Handle empty input
    if not intervals:
        return []
    
    # Sort by start time
    intervals.sort(key=lambda x: (x[0], x[1]))
    
    # Merge
    merged = [intervals[0]]
    for current in intervals[1:]:
        prev = merged[-1]
        # Check for overlap or adjacency
        # Adjacent: current.start == prev.end + 1
        # Overlap: current.start <= prev.end
        if current[0] <= prev[1] + 1:
            # Merge: update end to the furthest point reached
            if current[1] > prev[1]:
                prev[1] = current[1]
        else:
            merged.append(current)
            
    return merged