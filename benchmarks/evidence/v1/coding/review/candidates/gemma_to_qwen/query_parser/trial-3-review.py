import re
import json

def parse_query(text):
    tokens = []
    pos = 0
    n = len(text)
    
    while pos < n:
        char = text[pos]
        if char in ' \t\r\n':
            pos += 1
            continue
        
        if char == '"':
            start = pos
            pos += 1
            res = ['"']
            while pos < n:
                c = text[pos]
                if c == '\\':
                    if pos + 1 < n:
                        res.append(c)
                        res.append(text[pos+1])
                        pos += 2
                    else:
                        raise ValueError("Unterminated escape")
                elif c == '"':
                    res.append(c)
                    pos += 1
                    break
                elif ord(c) < 32:
                    raise ValueError("Control char in string")
                else:
                    res.append(c)
                    pos += 1
            else:
                raise ValueError("Unterminated string")
            tokens.append(('STR', json.loads("".join(res))))
            continue
        
        if char == '(':
            tokens.append(('LPAREN', '('))
            pos += 1
            continue
        if char == ')':
            tokens.append(('RPAREN', ')'))
            pos += 1
            continue
        
        # Operators
        op_match = re.match(r'^(!=|<=|>=|=|<|>)', text[pos:])
        if op_match:
            op = op_match.group(0)
            tokens.append(('OP', op))
            pos += len(op)
            continue
        
        # Numbers
        num_match = re.match(r'^-?(0|[1-9][0-9]*)', text[pos:])
        if num_match:
            num_str = num_match.group(0)
            tokens.append(('INT', int(num_str)))
            pos += len(num_str)
            continue
        
        # Identifiers / Keywords
        id_match = re.match(r'^[A-Za-z_][A-Za-z0-9_]*', text[pos:])
        if id_match:
            word = id_match.group(0)
            pos += len(word)
            # All words are treated as ID tokens. Keywords are valid identifiers.
            tokens.append(('ID', word))
            continue
        
        raise ValueError(f"Unexpected character {char}")

    idx = 0
    depth = 0
    
    def peek():
        return tokens[idx] if idx < len(tokens) else (None, None)
    
    def consume(expected_type=None):
        nonlocal idx
        t_type, t_val = peek()
        if expected_type and t_type != expected_type:
            raise ValueError("Unexpected token")
        idx += 1
        return t_val

    def parse_expr():
        return parse_or()

    def parse_or():
        left = parse_and()
        while peek()[0] == 'ID' and peek()[1] == 'or':
            consume()
            right = parse_and()
            left = ('or', left, right)
        return left

    def parse_and():
        left = parse_unary()
        while peek()[0] == 'ID' and peek()[1] == 'and':
            consume()
            right = parse_unary()
            left = ('and', left, right)
        return left

    def parse_unary():
        nonlocal depth
        t_type, t_val = peek()
        if t_type == 'ID' and t_val == 'not':
            consume()
            return ('not', parse_unary())
        if t_type == 'LPAREN':
            depth += 1
            if depth > 40:
                raise ValueError("Nesting too deep")
            consume()
            res = parse_expr()
            consume('RPAREN')
            depth -= 1
            return res
        
        # Comparison
        field = consume('ID')
        op = consume('OP')
        lit_type, lit_val = peek()
        if lit_type in ('INT', 'STR', 'ID'):
            # If it's an ID, it must be a keyword literal (true/false/null)
            if lit_type == 'ID' and lit_val not in ('true', 'false', 'null'):
                raise ValueError("Expected literal")
            consume()
            if lit_type == 'ID':
                if lit_val == 'true':
                    lit_val = True
                elif lit_val == 'false':
                    lit_val = False
                elif lit_val == 'null':
                    lit_val = None
            return ('cmp', field, op, lit_val)
        raise ValueError("Expected literal")

    if not tokens:
        raise ValueError("Empty input")
    
    result = parse_expr()
    if idx < len(tokens):
        raise ValueError("Trailing tokens")
    return result