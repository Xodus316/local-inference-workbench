from candidate import parse_query
assert parse_query('age >= 18 and not name = "x"')==('and',('cmp','age','>=',18),('not',('cmp','name','=','x')))
assert parse_query('active=true')==('cmp','active','=',True)
try:parse_query('age =')
except ValueError:pass
else:raise AssertionError('must reject incomplete comparison')
