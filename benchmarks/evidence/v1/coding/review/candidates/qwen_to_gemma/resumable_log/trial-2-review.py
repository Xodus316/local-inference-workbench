import json
import os
import tempfile
from pathlib import Path

def process_log(log_path, state_path, limit=None):
    log_path = Path(log_path)
    state_path = Path(state_path)

    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be None or a positive integer")

    offset = 0
    totals = {}
    seen = []
    
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
    
    try:
        log_size = log_path.stat().st_size
    except OSError as e:
        raise ValueError(f"Cannot access log file: {e}")
    
    if log_size < offset:
        raise ValueError("Log file truncated")
    
    # Work on copies to ensure atomicity of the state update
    current_totals = totals.copy()
    current_seen = list(seen)
    seen_set = set(seen)
    
    consumed = 0
    applied = 0
    new_offset = offset
    
    if log_size > offset:
        with open(log_path, 'rb') as f:
            f.seek(offset)
            data = f.read()
            
            lines = []
            current_start = 0
            for i in range(len(data)):
                if data[i:i+1] == b'\n':
                    lines.append(data[current_start:i+1])
                    current_start = i + 1
            
            for line_bytes in lines:
                if limit is not None and consumed >= limit:
                    break
                
                try:
                    line_str = line_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    raise ValueError("Malformed line: invalid UTF-8")
                
                # Empty/blank lines are malformed
                if not line_str or line_str.isspace():
                    raise ValueError("Malformed line: empty or blank")
                
                try:
                    record = json.loads(line_str)
                except json.JSONDecodeError:
                    raise ValueError("Malformed line: invalid JSON")
                
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
                
                new_offset += len(line_bytes)
                consumed += 1
                
                if rec_id not in seen_set:
                    seen_set.add(rec_id)
                    current_seen.append(rec_id)
                    current_totals[account] = current_totals.get(account, 0) + delta
                    applied += 1
    
    if consumed == 0:
        return {
            'processed': 0,
            'applied': 0,
            'offset': offset,
            'totals': totals
        }
    
    new_checkpoint = {
        'version': 1,
        'offset': new_offset,
        'totals': current_totals,
        'seen': current_seen
    }
    
    checkpoint_dir = state_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=checkpoint_dir, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_f:
            json.dump(new_checkpoint, tmp_f)
        os.replace(tmp_path, state_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    
    return {
        'processed': consumed,
        'applied': applied,
        'offset': new_offset,
        'totals': current_totals
    }