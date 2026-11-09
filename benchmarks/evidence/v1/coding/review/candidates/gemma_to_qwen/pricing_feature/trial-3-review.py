from money import percent_of

def quote(lines, catalog, *, discount_percent=0, shipping_cents=0, coupon=None):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    original_discount = percent_of(subtotal, discount_percent)

    current_shipping = shipping_cents
    coupon_discount = 0

    if coupon is not None:
        if not isinstance(coupon, dict):
            raise ValueError("Coupon must be a dictionary")

        allowed_keys = {"kind", "value", "min_subtotal"}
        if not set(coupon.keys()).issubset(allowed_keys):
            raise ValueError("Invalid coupon keys")

        if "kind" not in coupon:
            raise ValueError("Coupon must have a 'kind' key")

        kind = coupon["kind"]
        if kind not in ("fixed", "percent", "free_shipping"):
            raise ValueError("Invalid coupon kind")

        min_sub = coupon.get("min_subtotal", 0)
        if not isinstance(min_sub, int) or isinstance(min_sub, bool) or min_sub < 0:
            raise ValueError("Invalid min_subtotal")

        if kind in ("fixed", "percent"):
            if "value" not in coupon:
                raise ValueError(f"{kind} coupon must have a 'value' key")
            val = coupon["value"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError("Invalid coupon value")
            if kind == "fixed" and val < 0:
                raise ValueError("Invalid fixed coupon value")
            if kind == "percent" and not (0 <= val <= 100):
                raise ValueError("Invalid percent coupon value")
        else:
            if "value" in coupon:
                raise ValueError("free_shipping coupon must not have a value")

        if subtotal >= min_sub:
            if kind == "fixed":
                remaining = subtotal - original_discount
                coupon_discount = min(coupon["value"], remaining)
            elif kind == "percent":
                remaining = subtotal - original_discount
                coupon_discount = percent_of(remaining, coupon["value"])
            elif kind == "free_shipping":
                current_shipping = 0

    total_discount = original_discount + coupon_discount
    return {
        "subtotal": subtotal,
        "discount": total_discount,
        "shipping": current_shipping,
        "total": subtotal - total_discount + current_shipping
    }
