from candidate import ExpiringLRU
now=[0.0]
c=ExpiringLRU(2,lambda:now[0])
c.put("a",1,ttl=2);c.put("b",None)
assert c.get("a")==1
now[0]=2
assert c.get("a","missing")=="missing"
assert c.get("b","missing") is None
assert len(c)==1
