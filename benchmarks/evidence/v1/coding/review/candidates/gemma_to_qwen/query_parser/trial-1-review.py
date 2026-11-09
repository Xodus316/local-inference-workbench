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
            res = ""
            while pos < n:
                if text[pos] == '\\':
                    if pos + 1 >= n: raise ValueError()
                    esc = text[pos+1]
                    if esc == '"': res += '"'
                    elif esc == '\\': res += '\\'
                    elif esc == '/': res += '/'
                    elif esc == 'b': res += '\b'
                    elif esc == 'f': res += '\f'
                    elif esc == 'n': res += '\n'
                    elif esc == 'r': res += '\r'
                    elif esc == 't': res += '\t'
                    else: raise ValueError()
                    pos += 2
                elif text[pos] == '"':
                    pos += 1
                    tokens.append(('STR', res))
                    break
                elif ord(text[pos]) < 32: raise ValueError()
                else:
                    res += text[pos]
                    pos += 1
            else: raise ValueError()
            continue
        
        if char == '(': tokens.append(('LPAREN', '(')); pos += 1; continue
        if char == ')': tokens.append(('RPAREN', ')')); pos += 1; continue
        
        if char in '!=<>':
            op = char
            if pos + 1 < n and text[pos+1] == '=':
                op += '='
                pos += 2
            else:
                pos += 1
            if op not in ('=', '!=', '<', '<=', '>', '>='):
                raise ValueError()
            tokens.append(('OP', op))
            continue
        
        if char == '=': tokens.append(('OP', '=')); pos += 1; continue
        
        if char == '-':
            if pos + 1 < n and text[pos+1].isdigit():
                start = pos
                pos += 1
                while pos < n and text[pos].isdigit(): pos += 1
                tokens.append(('INT', int(text[start:pos])))
                continue
            else:
                raise ValueError()
        
        if char.isdigit():
            if char == '0' and pos + 1 < n and text[pos+1].isdigit(): raise ValueError()
            start = pos
            while pos < n and text[pos].isdigit(): pos += 1
            tokens.append(('INT', int(text[start:pos])))
            continue
        
        if char.isalpha() or char == '_':
            start = pos
            while pos < n and (text[pos].isalnum() or text[pos] == '_'): pos += 1
            word = text[start:pos]
            if word == 'and': tokens.append(('AND', 'and'))
            elif word == 'or': tokens.append(('OR', 'or'))
            elif word == 'not': tokens.append(('NOT', 'not'))
            elif word == 'true': tokens.append(('BOOL', True))
            elif word == 'false': tokens.append(('BOOL', False))
            elif word == 'null': tokens.append(('NULL', None))
            else: tokens.append(('ID', word))
            continue
        
        raise ValueError()

    idx = 0
    def peek(): return tokens[idx] if idx < len(tokens) else (None, None)
    def consume(type=None): 
        nonlocal idx
        t = peek()
        if type and t[0] != type: raise ValueError()
        idx += 1
        return t

    def parse_expr(): return parse_or()

    def parse_or():
        left = parse_and()
        while peek()[0] == 'OR':
            consume()
            right = parse_and()
            left = ('or', left, right)
        return left

    def parse_and():
        left = parse_unary()
        while peek()[0] == 'AND':
            consume()
            right = parse_unary()
            left = ('and', left, right)
        return left

    def parse_unary():
        if peek()[0] == 'NOT':
            consume()
            return ('not', parse_unary())
        if peek()[0] == 'LPAREN':
            consume()
            e = parse_expr()
            consume('RPAREN')
            return e
        return parse_comparison()

    def parse_comparison():
        t = consume('ID')
        field = t[1]
        op = consume('OP')[1]
        lit_t = peek()
        if lit_t[0] == 'INT': val = consume()[1]
        elif lit_t[0] == 'STR': val = consume()[1]
        elif lit_t[0] == 'BOOL': val = consume()[1]
        elif lit_t[0] == 'NULL': val = consume()[1]
        else: raise ValueError()
        return ('cmp', field, op, val)

    res = parse_expr()
    if idx != len(tokens): raise ValueError()
    return res