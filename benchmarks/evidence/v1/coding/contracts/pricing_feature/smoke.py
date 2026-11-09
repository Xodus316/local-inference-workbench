from pricing import quote
assert quote([("a",2)],{"a":125},discount_percent=10,shipping_cents=30)=={"subtotal":250,"discount":25,"shipping":30,"total":255}
assert quote([("a",2)],{"a":125},discount_percent=10,shipping_cents=30,coupon={"kind":"fixed","value":100})["total"]==155
