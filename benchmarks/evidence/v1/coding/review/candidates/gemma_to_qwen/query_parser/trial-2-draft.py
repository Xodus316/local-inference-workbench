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
            res = ['']
            while pos < n:
                c = text[pos]
                if c == '\\':
                    if pos + 1 >= n:
                        raise ValueError()
                    esc = text[pos+1]
                    mapping = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '\'': '\'', '"': '"', '\\': '\\'}
                    if esc in mapping:
                        res[-1] += mapping[esc]
                    elif esc == 'u' and pos + 5 < n:
                        try:
                            res[-1] += chr(int(text[pos+2:pos+6], 16))
                        except ValueError:
                            raise ValueError()
                        pos += 5
                    else:
                        res[-1] += '\\'
                        res[-1] += esc
                    pos += 2
                elif c == '"':
                    pos += 1
                    tokens.append(('STR', ''.join(res)))
                    break
                elif ord(c) < 32:
                    raise ValueError()
                else:
                    res[-1] += c
                    pos += 1
            else:
                raise ValueError()
            continue
        
        if char == '(':
            tokens.append(('LPAREN', '('))
            pos += 1
            continue
        if char == ')':
            tokens.append(('RPAREN', ')'))
            pos += 1
            continue
        
        if char == '=':
            tokens.append(('OP', '='))
            pos += 1
            continue
        if text.startswith('!=', pos):
            tokens.append(('OP', '!='))
            pos += 2
            continue
        if text.startswith('<=', pos):
            tokens.append(('OP', '<='))
            pos += 2
            continue
        if text.startswith('>=', pos):
            tokens.append(('OP', '>='))
            pos += 2
            continue
        if char == '<':
            tokens.append(('OP', '<'))
            pos += 1
            continue
        if char == '>':
            tokens.append(('OP', '>'))
            pos += 1
            continue
        
        if char == '-':
            start = pos
            pos += 1
            if pos < n and text[pos].isdigit():
                if text[pos] == '0':
                    if pos + 1 < n and text[pos+1].isdigit():
                        raise ValueError()
                while pos < n and text[pos].isdigit():
                    pos += 1
                tokens.append(('INT', int(text[start:pos])))
                continue
            else:
                raise ValueError()
        
        if char.isdigit():
            start = pos
            if char == '0':
                if pos + 1 < n and text[pos+1].isdigit():
                    raise ValueError()
            while pos < n and text[pos].isdigit():
                pos += 1
            tokens.append(('INT', int(text[start:pos])))
            continue
        
        if char.isalpha() or char == '_':
            start = pos
            while pos < n and (text[pos].isalnum() or text[pos] == '_'):
                pos += 1
            word = text[start:pos]
            if word == 'and': tokens.append(('AND', 'and'))
            elif word == 'or': tokens.append(('OR', 'or'))
            elif word == 'not': tokens.append(('NOT', 'not'))
            elif word == 'true': tokens.append(('BOOL', True))
            elif word == 'false': tokens.append(('BOOL', False))
            elif word == 'null': tokens.append(('NULL', None))
            else: tokens.append(('IDENT', word))
            continue
        
        raise ValueError()

    idx = 0
    def peek():
        return tokens[idx] if idx < len(tokens) else (None, None)
    
    def consume(expected_type=None):
        nonlocal idx
        t, v = peek()
        if expected_type and t != expected_type:
            raise ValueError()
        idx += 1
        return v

    def parse_expr():
        return parse_or()

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
        t, v = peek()
        if t == 'NOT':
            consume()
            return ('not', parse_unary())
        if t == 'LPAREN':
            consume()
            e = parse_expr()
            consume('RPAREN')
            return e
        return parse_comparison()

    def parse_comparison():
        t, v = peek()
        if t != 'IDENT':
            raise ValueError()
        field = consume()
        t, op = peek()
        if t != 'OP':
            raise ValueError()
        consume()
        t, val = peek()
        if t not in ('INT', 'STR', 'BOOL', 'NULL'):
            raise ValueError()
        consume()
        return ('cmp', field, op, val)

    res = parse_expr()
    if idx != len(tokens):
        raise ValueError()
    return res