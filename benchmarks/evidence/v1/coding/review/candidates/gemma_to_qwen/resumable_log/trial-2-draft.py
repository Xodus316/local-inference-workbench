import json
import os
from pathlib import Path

def process_log(log_path, state_path, limit=None):
    log_path = Path(log_path)
    state_path = Path(state_path)

    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        raise ValueError("limit must be None or a positive integer")

    # Load state
    state = {"version": 1, "offset": 0, "totals": {}, "seen": []}
    state_exists = False
    if state_path.exists():
        state_exists = True
        with open(state_path, 'r', encoding='utf-8') as f:
            state = json.load(f)

    # Check for truncation
    log_size = log_path.stat().st_size
    if state['offset'] > log_size:
        raise ValueError("Log file truncated")

    processed_count = 0
    applied_count = 0
    current_offset = state['offset']
    current_totals = state['totals'].copy()
    current_seen = set(state['seen'])

    try:
        with open(log_path, 'rb') as f:
            f.seek(current_offset)
            
            while limit is None or processed_count < limit:
                line_bytes = f.readline()
                if not line_bytes:
                    break
                
                # A line is complete only if it ends with a newline
                if not line_bytes.endswith(b'\n'):
                    break

                processed_count += 1
                line_str = line_bytes.decode('utf-8').rstrip('\n')
                
                if not line_str.strip():
                    raise ValueError("Empty or blank line")

                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    raise ValueError("Malformed JSON")

                if not isinstance(data, dict):
                    raise ValueError("Line is not a JSON object")
                
                # Validate keys and types
                if not all(k in data for k in ('id', 'account', 'delta')) or len(data) != 3:
                    raise ValueError("Missing or extra keys")
                
                eid = data['id']
                acc = data['account']
                delta = data['delta']

                if not (isinstance(eid, str) and eid and isinstance(acc, str) and acc and isinstance(delta, int) and not isinstance(delta, bool)):
                    raise ValueError("Invalid field types or empty values")

                if eid not in current_seen:
                    current_totals[acc] = current_totals.get(acc, 0) + delta
                    current_seen.add(eid)
                    applied_count += 1
                
                current_offset = f.tell()

    except (UnicodeDecodeError, IOError) as e:
        raise ValueError(f"IO Error: {e}")

    if processed_count > 0:
        # Atomic write of checkpoint
        new_state = {
            "version": 1,
            "offset": current_offset,
            "totals": current_totals,
            "seen": list(current_seen)
        }
        temp_path = state_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(new_state, f)
        os.replace(temp_path, state_path)

    return {
        'processed': processed_count,
        'applied': applied_count,
        'offset': current_offset,
        'totals': current_totals
    }