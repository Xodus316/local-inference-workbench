from money import percent_of

def quote(lines, catalog, *, discount_percent=0, shipping_cents=0):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    discount = percent_of(subtotal, discount_percent)
    return {"subtotal": subtotal, "discount": discount, "shipping": shipping_cents,
            "total": subtotal - discount + shipping_cents}
