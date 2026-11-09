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
                checkpoint = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"Invalid checkpoint: {e}")
        
        if checkpoint.get('version') != 1:
            raise ValueError("Invalid checkpoint version")
        
        offset = checkpoint.get('offset', 0)
        totals = checkpoint.get('totals', {})
        seen = checkpoint.get('seen', [])
        seen_set = set(seen)
    
    # Check log size
    try:
        log_size = log_path.stat().st_size
    except OSError as e:
        raise ValueError(f"Cannot access log file: {e}")
    
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
            
            # Split into lines, keeping track of byte offsets
            # We need to process complete lines (ending with newline)
            # The last line might be incomplete (no newline)
            
            lines = []
            current_start = 0
            i = 0
            
            while i < len(data):
                if data[i:i+1] == b'\n':
                    line_bytes = data[current_start:i+1]
                    lines.append(line_bytes)
                    current_start = i + 1
                i += 1
            
            # If there's remaining data without a newline, it's incomplete - ignore it
            # The last complete line ends at current_start - 1 if current_start > 0
            
            # Process lines up to limit
            for line_bytes in lines:
                if limit is not None and consumed >= limit:
                    break
                
                # Parse the line
                try:
                    line_str = line_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    raise ValueError("Malformed line: invalid UTF-8")
                
                # Check for empty/blank lines
                if line_str.strip() == '':
                    raise ValueError("Malformed line: empty or blank")
                
                try:
                    record = json.loads(line_str)
                except json.JSONDecodeError:
                    raise ValueError("Malformed line: invalid JSON")
                
                # Validate record structure
                if not isinstance(record, dict):
                    raise ValueError("Malformed line: not a JSON object")
                
                if set(record.keys()) != {'id', 'account', 'delta'}:
                    raise ValueError("Malformed line: wrong keys")
                
                rec_id = record['id']
                account = record['account']
                delta = record['delta']
                
                if not isinstance(rec_id, str) or rec_id == '':
                    raise ValueError("Malformed line: invalid id")
                
                if not isinstance(account, str) or account == '':
                    raise ValueError("Malformed line: invalid account")
                
                if isinstance(delta, bool) or not isinstance(delta, int):
                    raise ValueError("Malformed line: invalid delta")
                
                # Update offset
                new_offset += len(line_bytes)
                consumed += 1
                
                # Check if ID already seen
                if rec_id not in seen_set:
                    seen_set.add(rec_id)
                    seen.append(rec_id)
                    totals[account] = totals.get(account, 0) + delta
                    applied += 1
                
                # If limit reached, stop
                if limit is not None and consumed >= limit:
                    break
    
    # If no records were consumed, leave checkpoint unchanged
    if consumed == 0:
        return {
            'processed': 0,
            'applied': 0,
            'offset': offset,
            'totals': totals
        }
    
    # Write new checkpoint atomically
    new_checkpoint = {
        'version': 1,
        'offset': new_offset,
        'totals': totals,
        'seen': seen
    }
    
    checkpoint_dir = state_path.parent
    
    # Create temp file in the same directory
    fd, tmp_path = tempfile.mkstemp(dir=checkpoint_dir, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_f:
            json.dump(new_checkpoint, tmp_f)
        
        # Atomic replacement
        os.replace(tmp_path, state_path)
    except:
        # Clean up temp file if it exists
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    
    return {
        'processed': consumed,
        'applied': applied,
        'offset': new_offset,
        'totals': totals
    }