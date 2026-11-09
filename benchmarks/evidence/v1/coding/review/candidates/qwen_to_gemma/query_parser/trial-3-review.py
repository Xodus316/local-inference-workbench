import re

RESERVED = {'and', 'or', 'not', 'true', 'false', 'null'}
IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
INT_RE = re.compile(r'-?(0|[1-9][0-9]*)')

def _skip_ws(s, i):
    n = len(s)
    while i < n and s[i] in ' \t\r\n':
        i += 1
    return i

def _parse_string(s, i):
    if i >= len(s) or s[i] != '"':
        raise ValueError('expected string')
    i += 1
    buf = []
    while i < len(s):
        c = s[i]
        if c == '"':
            i += 1
            return ''.join(buf), i
        if c == '\\':
            if i + 1 >= len(s):
                raise ValueError('bad escape')
            nxt = s[i + 1]
            if nxt == '"': buf.append('"')
            elif nxt == '\\': buf.append('\\')
            elif nxt == '/': buf.append('/')
            elif nxt == 'b': buf.append('\b')
            elif nxt == 'f': buf.append('\f')
            elif nxt == 'n': buf.append('\n')
            elif nxt == 'r': buf.append('\r')
            elif nxt == 't': buf.append('\t')
            elif nxt == 'u':
                if i + 5 >= len(s):
                    raise ValueError('bad unicode escape')
                hexs = s[i + 2:i + 6]
                try:
                    code = int(hexs, 16)
                except ValueError:
                    raise ValueError('bad unicode escape')
                buf.append(chr(code))
                i += 6
                i += 1 # skip the backslash
                continue
            else:
                raise ValueError('bad escape')
            i += 2
            continue
        if ord(c) < 0x20:
            raise ValueError('raw control character in string')
        buf.append(c)
        i += 1
    raise ValueError('unterminated string')

def _parse_literal(s, i):
    if i < len(s) and s[i] == '"':
        return _parse_string(s, i)
    m = INT_RE.match(s, i)
    if m and m.end() > i:
        end = m.end()
        if end < len(s) and (s[end].isalnum() or s[end] == '_'):
            raise ValueError('invalid literal')
        return int(m.group(0)), end
    for kw, val in (('true', True), ('false', False), ('null', None)):
        if s.startswith(kw, i):
            end = i + len(kw)
            if end < len(s) and (s[end].isalnum() or s[end] == '_'):
                raise ValueError('invalid literal')
            return val, end
    raise ValueError('expected literal')

def _parse_ident(s, i):
    m = IDENT_RE.match(s, i)
    if not m:
        raise ValueError('expected identifier')
    word = m.group(0)
    if word in RESERVED:
        raise ValueError('reserved word as identifier')
    return word, m.end()

def _parse_op(s, i):
    for op in ('!=', '<=', '>=', '=', '<', '>'):
        if s.startswith(op, i):
            return op, i + len(op)
    raise ValueError('expected operator')

class Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def skip_ws(self):
        self.i = _skip_ws(self.s, self.i)

    def peek(self):
        self.skip_ws()
        return self.s[self.i] if self.i < self.n else None

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while True:
            self.skip_ws()
            if self.s.startswith('or', self.i):
                end = self.i + 2
                if end < self.n and (self.s[end].isalnum() or self.s[end] == '_'):
                    break
                self.i = end
                right = self.parse_and()
                left = ('or', left, right)
            else:
                break
        return left

    def parse_and(self):
        left = self.parse_unary()
        while True:
            self.skip_ws()
            if self.s.startswith('and', self.i):
                end = self.i + 3
                if end < self.n and (self.s[end].isalnum() or self.s[end] == '_'):
                    break
                self.i = end
                right = self.parse_unary()
                left = ('and', left, right)
            else:
                break
        return left

    def parse_unary(self):
        self.skip_ws()
        if self.s.startswith('not', self.i):
            end = self.i + 3
            if end < self.n and (self.s[end].isalnum() or self.s[end] == '_'):
                raise ValueError('invalid token')
            self.i = end
            return ('not', self.parse_unary())
        if self.peek() == '(':
            self.i += 1
            expr = self.parse_expr()
            self.skip_ws()
            if self.i >= self.n or self.s[self.i] != ')':
                raise ValueError('expected )')
            self.i += 1
            return expr
        return self.parse_comparison()

    def parse_comparison(self):
        self.skip_ws()
        field, i = _parse_ident(self.s, self.i)
        self.i = i
        self.skip_ws()
        op, i = _parse_op(self.s, self.i)
        self.i = i
        self.skip_ws()
        lit, i = _parse_literal(self.s, self.i)
        self.i = i
        return ('cmp', field, op, lit)

def parse_query(text):
    if not isinstance(text, str):
        raise ValueError('input must be str')
    p = Parser(text)
    p.skip_ws()
    if p.i >= p.n:
        raise ValueError('empty input')
    tree = p.parse_expr()
    p.skip_ws()
    if p.i != p.n:
        raise ValueError('trailing tokens')
    return tree