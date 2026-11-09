import re

RESERVED = {'and', 'or', 'not', 'true', 'false', 'null'}

# Token patterns
IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
INT_RE = re.compile(r'0|-?[1-9][0-9]*')
STR_RE = re.compile(r'"(\\.|[^"\\])*"')
OP_RE = re.compile(r'!=|<=|>=|=|<|>')
WS_RE = re.compile(r'[ \t\r\n]+')


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        # Skip whitespace
        m = WS_RE.match(text, i)
        if m:
            i = m.end()
            continue
        
        # Check for string
        if text[i] == '"':
            m = STR_RE.match(text, i)
            if not m:
                raise ValueError('Invalid string literal')
            s = m.group(0)
            # Parse JSON string
            try:
                val = json.loads(s)
            except Exception:
                raise ValueError('Invalid string literal')
            tokens.append(('LITERAL', val))
            i = m.end()
            continue
        
        # Check for integer
        if text[i].isdigit() or (text[i] == '-' and i + 1 < n and text[i+1].isdigit()):
            m = INT_RE.match(text, i)
            if m:
                tokens.append(('LITERAL', int(m.group(0))))
                i = m.end()
                continue
            else:
                raise ValueError('Invalid integer literal')
        
        # Check for identifier or keyword
        m = IDENT_RE.match(text, i)
        if m:
            word = m.group(0)
            if word in RESERVED:
                tokens.append((word.upper(), word))
            else:
                tokens.append(('IDENT', word))
            i = m.end()
            continue
        
        # Check for operator
        m = OP_RE.match(text, i)
        if m:
            tokens.append(('OP', m.group(0)))
            i = m.end()
            continue
        
        # Check for parentheses
        if text[i] == '(':
            tokens.append(('LPAREN', '('))
            i += 1
            continue
        if text[i] == ')':
            tokens.append(('RPAREN', ')'))
            i += 1
            continue
        
        raise ValueError(f'Unexpected character: {text[i]!r}')
    
    return tokens


def _parse_expr(tokens, pos):
    return _parse_or(tokens, pos)


def _parse_or(tokens, pos):
    left, pos = _parse_and(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] == 'OR':
        pos += 1
        right, pos = _parse_and(tokens, pos)
        left = ('or', left, right)
    return left, pos


def _parse_and(tokens, pos):
    left, pos = _parse_unary(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] == 'AND':
        pos += 1
        right, pos = _parse_unary(tokens, pos)
        left = ('and', left, right)
    return left, pos


def _parse_unary(tokens, pos):
    if pos < len(tokens) and tokens[pos][0] == 'NOT':
        pos += 1
        operand, pos = _parse_unary(tokens, pos)
        return ('not', operand), pos
    
    if pos < len(tokens) and tokens[pos][0] == 'LPAREN':
        pos += 1
        expr, pos = _parse_expr(tokens, pos)
        if pos >= len(tokens) or tokens[pos][0] != 'RPAREN':
            raise ValueError('Expected closing parenthesis')
        pos += 1
        return expr, pos
    
    return _parse_comparison(tokens, pos)


def _parse_comparison(tokens, pos):
    if pos >= len(tokens) or tokens[pos][0] != 'IDENT':
        raise ValueError('Expected identifier')
    field = tokens[pos][1]
    pos += 1
    
    if pos >= len(tokens) or tokens[pos][0] != 'OP':
        raise ValueError('Expected operator')
    op = tokens[pos][1]
    pos += 1
    
    if pos >= len(tokens) or tokens[pos][0] != 'LITERAL':
        raise ValueError('Expected literal')
    literal = tokens[pos][1]
    pos += 1
    
    return ('cmp', field, op, literal), pos


def parse_query(text):
    if not isinstance(text, str):
        raise ValueError('Input must be a string')
    
    tokens = _tokenize(text)
    
    if not tokens:
        raise ValueError('Empty input')
    
    result, pos = _parse_expr(tokens, 0)
    
    if pos != len(tokens):
        raise ValueError('Trailing tokens')
    
    return result


import json