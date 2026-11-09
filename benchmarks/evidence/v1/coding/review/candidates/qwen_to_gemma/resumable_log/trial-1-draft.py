import json
import os
import tempfile
from pathlib import Path

def process_log(log_path, state_path, limit=None):
    log_path = Path(log_path)
    state_path = Path(state_path)

    # Validate limit
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be None or a positive integer")

    # Load checkpoint
    offset = 0
    totals = {}
    seen = []
    seen_set = set()
    
    if state_path.exists():
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('version') != 1:
                raise ValueError("Invalid checkpoint version")
            offset = data['offset']
            totals = data['totals']
            seen = data['seen']
            seen_set = set(seen)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(f"Invalid checkpoint: {e}")

    # Check log size
    try:
        log_size = log_path.stat().st_size
    except FileNotFoundError:
        log_size = 0

    if log_size < offset:
        raise ValueError("Log file truncated")

    # Read log from offset
    consumed = 0
    applied = 0
    new_offset = offset
    
    if log_size > offset:
        with open(log_path, 'rb') as f:
            f.seek(offset)
            data = f.read()
            
            # Split into lines, keeping track of newlines
            # We need to process complete lines (ending with \n)
            # The last line might be incomplete (no \n)
            
            # Find all complete lines
            lines = []
            current_start = 0
            i = 0
            while i < len(data):
                if data[i] == 0x0A:  # \n
                    line_bytes = data[current_start:i+1]
                    lines.append(line_bytes)
                    current_start = i + 1
                i += 1
            
            # The remaining data from current_start to end is incomplete (no trailing newline)
            # We ignore it
            
            # Process lines up to limit
            lines_to_process = lines
            if limit is not None:
                lines_to_process = lines[:limit]
            
            # Validate and process all lines in lines_to_process
            # If any is malformed, raise ValueError and don't update state
            new_totals = dict(totals)
            new_seen = list(seen)
            new_seen_set = set(seen_set)
            
            for line_bytes in lines_to_process:
                # Remove the trailing newline for parsing
                line_content = line_bytes[:-1]  # Remove \n
                
                # Check for empty/blank lines
                if len(line_content) == 0:
                    raise ValueError("Empty line in log")
                
                # Try to parse JSON
                try:
                    record = json.loads(line_content.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise ValueError(f"Malformed JSON line: {e}")
                
                # Validate record structure
                if not isinstance(record, dict):
                    raise ValueError("Record must be a JSON object")
                
                # Check for exactly the required keys
                if set(record.keys()) != {'id', 'account', 'delta'}:
                    raise ValueError("Record must have exactly id, account, delta")
                
                rec_id = record['id']
                account = record['account']
                delta = record['delta']
                
                # Validate id
                if not isinstance(rec_id, str) or len(rec_id) == 0:
                    raise ValueError("id must be nonempty string")
                
                # Validate account
                if not isinstance(account, str) or len(account) == 0:
                    raise ValueError("account must be nonempty string")
                
                # Validate delta (exact int, bool invalid)
                if isinstance(delta, bool) or not isinstance(delta, int):
                    raise ValueError("delta must be exact int, bool invalid")
                
                # Update state
                if rec_id not in new_seen_set:
                    new_seen_set.add(rec_id)
                    new_seen.append(rec_id)
                    new_totals[account] = new_totals.get(account, 0) + delta
                    applied += 1
                
                consumed += 1
                new_offset += len(line_bytes)
            
            # If we consumed any records, write checkpoint
            if consumed > 0:
                checkpoint = {
                    'version': 1,
                    'offset': new_offset,
                    'totals': new_totals,
                    'seen': new_seen
                }
                
                # Write to temp file in same directory, then atomic replace
                state_dir = state_path.parent
                fd, tmp_path = tempfile.mkstemp(dir=str(state_dir))
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as tmp_f:
                        json.dump(checkpoint, tmp_f)
                    os.replace(tmp_path, str(state_path))
                except:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
                
                totals = new_totals
                seen = new_seen
                seen_set = new_seen_set
    
    return {
        'processed': consumed,
        'applied': applied,
        'offset': new_offset,
        'totals': totals
    }
