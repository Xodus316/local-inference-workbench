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

    try:
        log_size = log_path.stat().st_size
    except FileNotFoundError:
        log_size = 0

    if log_size < offset:
        raise ValueError("Log file truncated")

    consumed = 0
    applied = 0
    current_offset = offset
    
    new_totals = dict(totals)
    new_seen = list(seen)
    new_seen_set = set(seen_set)

    if log_size > offset:
        with open(log_path, 'rb') as f:
            f.seek(offset)
            
            while True:
                if limit is not None and consumed >= limit:
                    break
                
                line_bytes = f.readline()
                if not line_bytes:
                    break
                
                if not line_bytes.endswith(b'\n'):
                    # Incomplete line: ignore it
                    break
                
                # Process complete line
                line_content_bytes = line_bytes[:-1]
                try:
                    line_content = line_content_bytes.decode('utf-8')
                except UnicodeDecodeError as e:
                    raise ValueError(f"UTF-8 decode error: {e}")
                
                if not line_content or line_content.isspace():
                    raise ValueError("Empty or blank line in log")
                
                try:
                    record = json.loads(line_content)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON line: {e}")
                
                if not isinstance(record, dict):
                    raise ValueError("Record must be a JSON object")
                
                if set(record.keys()) != {'id', 'account', 'delta'}:
                    raise ValueError("Record must have exactly id, account, delta")
                
                rec_id = record['id']
                account = record['account']
                delta = record['delta']
                
                if not isinstance(rec_id, str) or len(rec_id) == 0:
                    raise ValueError("id must be nonempty string")
                
                if not isinstance(account, str) or len(account) == 0:
                    raise ValueError("account must be nonempty string")
                
                if isinstance(delta, bool) or not isinstance(delta, int):
                    raise ValueError("delta must be exact int, bool invalid")
                
                if rec_id not in new_seen_set:
                    new_seen_set.add(rec_id)
                    new_seen.append(rec_id)
                    new_totals[account] = new_totals.get(account, 0) + delta
                    applied += 1
                
                consumed += 1
                current_offset += len(line_bytes)

    if consumed > 0:
        checkpoint = {
            'version': 1,
            'offset': current_offset,
            'totals': new_totals,
            'seen': new_seen
        }
        
        state_dir = state_path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(state_dir))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tmp_f:
                json.dump(checkpoint, tmp_f)
            os.replace(tmp_path, str(state_path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        
        totals = new_totals

    return {
        'processed': consumed,
        'applied': applied,
        'offset': current_offset,
        'totals': totals
    }