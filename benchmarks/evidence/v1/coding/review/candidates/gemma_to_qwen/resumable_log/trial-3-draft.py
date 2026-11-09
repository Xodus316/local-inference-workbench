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
    if state_path.exists():
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
                
                # Check if line is complete (ends with newline)
                if not line_bytes.endswith(b'\n'):
                    break

                line_str = line_bytes.decode('utf-8').rstrip('\n')
                if not line_str:
                    raise ValueError("Empty or blank line")

                try:
                    record = json.loads(line_str)
                except json.JSONDecodeError:
                    raise ValueError("Malformed JSON")

                # Validate record schema
                if not isinstance(record, dict):
                    raise ValueError("Record must be a JSON object")
                
                # Required keys and types
                for key in ('id', 'account', 'delta'):
                    if key not in record:
                        raise ValueError(f"Missing key: {key}")
                
                rid = record['id']
                acc = record['account']
                delta = record['delta']

                if not (isinstance(rid, str) and rid):
                    raise ValueError("Invalid id")
                if not (isinstance(acc, str) and acc):
                    raise ValueError("Invalid account")
                if not isinstance(delta, int) or isinstance(delta, bool):
                    raise ValueError("Invalid delta")

                processed_count += 1
                if rid not in current_seen:
                    current_seen.add(rid)
                    current_totals[acc] = current_totals.get(acc, 0) + delta
                    applied_count += 1
                
                current_offset = f.tell()

    except (UnicodeDecodeError, OSError) as e:
        raise ValueError(f"IO Error: {e}")

    if processed_count > 0:
        # Atomic write of checkpoint
        new_state = {
            "version": 1,
            "offset": current_offset,
            "totals": current_totals,
            "seen": list(current_seen)
        }
        tmp_path = state_path.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(new_state, f)
        os.replace(tmp_path, state_path)

    return {
        'processed': processed_count,
        'applied': applied_count,
        'offset': current_offset,
        'totals': current_totals
    }