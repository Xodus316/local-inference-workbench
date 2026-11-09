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
    
    if state_path.exists():
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)
        except (json.JSONDecodeError, OSError):
            raise ValueError("Invalid checkpoint file")
        
        if not isinstance(checkpoint, dict):
            raise ValueError("Invalid checkpoint format")
        
        if checkpoint.get('version') != 1:
            raise ValueError("Invalid checkpoint version")
        
        offset = checkpoint.get('offset', 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("Invalid checkpoint offset")
        
        totals = checkpoint.get('totals', {})
        if not isinstance(totals, dict):
            raise ValueError("Invalid checkpoint totals")
        
        seen = checkpoint.get('seen', [])
        if not isinstance(seen, list):
            raise ValueError("Invalid checkpoint seen")
        
        # Check for truncation
        try:
            log_size = log_path.stat().st_size
        except OSError:
            log_size = 0
        
        if log_size < offset:
            raise ValueError("Log file truncated")
    
    # Read log file
    try:
        with open(log_path, 'rb') as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        raise
    
    # Parse complete lines
    consumed = 0
    applied = 0
    new_offset = offset
    new_totals = dict(totals)
    new_seen = list(seen)
    seen_set = set(new_seen)
    
    # Split into lines, keeping track of byte offsets
    # We need to find complete lines (ending with newline)
    # The last line might be incomplete (no trailing newline)
    
    lines = []
    start = 0
    i = 0
    n = len(data)
    
    while i < n:
        if data[i] == 0x0a:  # newline
            line_bytes = data[start:i+1]
            lines.append(line_bytes)
            start = i + 1
        i += 1
    
    # The remaining bytes from 'start' to n are the incomplete line (if any)
    # We ignore them
    
    # Process lines up to limit
    for line_bytes in lines:
        if limit is not None and consumed >= limit:
            break
        
        # Strip the trailing newline for parsing
        line_content = line_bytes[:-1]  # Remove the newline byte
        
        # Check for empty/blank lines
        if line_content.strip() == b'':
            raise ValueError("Empty or blank line in log")
        
        # Parse JSON
        try:
            record = json.loads(line_content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Malformed JSON line")
        
        if not isinstance(record, dict):
            raise ValueError("Record must be a JSON object")
        
        # Validate fields
        if set(record.keys()) != {'id', 'account', 'delta'}:
            raise ValueError("Record must have exactly id, account, delta")
        
        rec_id = record['id']
        account = record['account']
        delta = record['delta']
        
        if not isinstance(rec_id, str) or rec_id == '':
            raise ValueError("id must be nonempty string")
        
        if not isinstance(account, str) or account == '':
            raise ValueError("account must be nonempty string")
        
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise ValueError("delta must be exact int, not bool")
        
        # Update state
        consumed += 1
        new_offset += len(line_bytes)
        
        if rec_id not in seen_set:
            seen_set.add(rec_id)
            new_seen.append(rec_id)
            new_totals[account] = new_totals.get(account, 0) + delta
            applied += 1
        # If duplicate, consume but don't update totals
    
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
        'totals': new_totals,
        'seen': new_seen
    }
    
    checkpoint_dir = state_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(checkpoint_dir), prefix='.state_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_f:
            json.dump(new_checkpoint, tmp_f)
        os.replace(tmp_path, str(state_path))
    except:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    
    return {
        'processed': consumed,
        'applied': applied,
        'offset': new_offset,
        'totals': new_totals
    }
