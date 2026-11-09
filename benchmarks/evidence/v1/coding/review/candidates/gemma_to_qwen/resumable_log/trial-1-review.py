import json
import os
from pathlib import Path

def process_log(log_path, state_path, limit=None):
    log_path = Path(log_path)
    state_path = Path(state_path)

    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        raise ValueError("limit must be None or a positive integer")

    # Load checkpoint
    checkpoint = {"version": 1, "offset": 0, "totals": {}, "seen": []}
    if state_path.exists():
        with open(state_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)
        
        # Validate checkpoint structure
        if not isinstance(checkpoint, dict):
            raise ValueError("Invalid checkpoint format")
        if checkpoint.get('version') != 1:
            raise ValueError("Invalid checkpoint version")
        if not isinstance(checkpoint.get('offset'), int):
            raise ValueError("Invalid checkpoint offset")
        if not isinstance(checkpoint.get('totals'), dict):
            raise ValueError("Invalid checkpoint totals")
        if not isinstance(checkpoint.get('seen'), list):
            raise ValueError("Invalid checkpoint seen")

    # Check for truncation
    log_size = log_path.stat().st_size
    if checkpoint['offset'] > log_size:
        raise ValueError("Log file truncated")

    current_offset = checkpoint['offset']
    totals = checkpoint['totals'].copy()
    seen = set(checkpoint['seen'])
    
    processed_count = 0
    applied_count = 0
    
    # Process lines in memory first to ensure atomicity
    lines_to_process = []
    
    try:
        with open(log_path, 'rb') as f:
            f.seek(current_offset)
            
            while limit is None or processed_count < limit:
                line_bytes = f.readline()
                if not line_bytes:
                    break
                
                # A final line without its newline is incomplete: ignore it
                if not line_bytes.endswith(b'\n'):
                    break
                
                line_str = line_bytes.decode('utf-8').rstrip('\n')
                if not line_str:
                    raise ValueError("Empty/blank complete lines are malformed")
                
                try:
                    record = json.loads(line_str)
                except json.JSONDecodeError:
                    raise ValueError("Malformed JSON line")
                
                # Validation
                if not isinstance(record, dict):
                    raise ValueError("Record must be a JSON object")
                
                # Check keys and types
                if set(record.keys()) != {'id', 'account', 'delta'}:
                    raise ValueError("Record must contain exactly id, account, and delta")
                
                rid = record['id']
                acc = record['account']
                delta = record['delta']
                
                if not (isinstance(rid, str) and rid and isinstance(acc, str) and acc and isinstance(delta, int) and not isinstance(delta, bool)):
                    raise ValueError("Invalid field types or empty values")
                
                lines_to_process.append((rid, acc, delta))
                processed_count += 1
                
                # Update offset to the end of this line
                current_offset = f.tell()
                
    except UnicodeDecodeError:
        raise ValueError("Log file contains invalid UTF-8")

    # If no lines were processed, leave checkpoint unchanged
    if processed_count == 0:
        return {
            'processed': 0,
            'applied': 0,
            'offset': current_offset,
            'totals': totals
        }

    # Apply the processed lines to totals and seen
    for rid, acc, delta in lines_to_process:
        if rid not in seen:
            seen.add(rid)
            totals[acc] = totals.get(acc, 0) + delta
            applied_count += 1

    # Atomic update of checkpoint
    new_checkpoint = {
        "version": 1,
        "offset": current_offset,
        "totals": totals,
        "seen": list(seen)
    }
    temp_path = state_path.with_suffix('.tmp')
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(new_checkpoint, f)
    os.replace(temp_path, state_path)

    return {
        'processed': processed_count,
        'applied': applied_count,
        'offset': current_offset,
        'totals': totals
    }