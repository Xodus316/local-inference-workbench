from money import percent_of


def _validate_coupon(coupon):
    if not isinstance(coupon, dict):
        raise ValueError("coupon must be a dict")

    allowed_keys = {"kind", "value", "min_subtotal"}
    extra_keys = set(coupon.keys()) - allowed_keys
    if extra_keys:
        raise ValueError(f"coupon has unexpected keys: {sorted(extra_keys)}")

    kind = coupon.get("kind")
    if kind not in ("fixed", "percent", "free_shipping"):
        raise ValueError("coupon kind must be 'fixed', 'percent', or 'free_shipping'")

    min_subtotal = coupon.get("min_subtotal", 0)
    if isinstance(min_subtotal, bool) or not isinstance(min_subtotal, int) or min_subtotal < 0:
        raise ValueError("min_subtotal must be a nonnegative integer (bool not allowed)")

    if kind == "free_shipping":
        if "value" in coupon:
            raise ValueError("free_shipping coupon must not have a 'value' field")
        return kind, None, min_subtotal

    if "value" not in coupon:
        raise ValueError(f"{kind} coupon requires a 'value' field")

    value = coupon["value"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{kind} coupon value must be an exact integer (bool not allowed)")

    if kind == "fixed":
        if value < 0:
            raise ValueError("fixed coupon value must be nonnegative")
    elif kind == "percent":
        if value < 0 or value > 100:
            raise ValueError("percent coupon value must be between 0 and 100")

    return kind, value, min_subtotal


def quote(lines, catalog, *, discount_percent=0, shipping_cents=0, coupon=None):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    base_discount = percent_of(subtotal, discount_percent)
    remaining = subtotal - base_discount

    coupon_discount = 0
    final_shipping = shipping_cents

    if coupon is not None:
        kind, value, min_subtotal = _validate_coupon(coupon)
        if subtotal >= min_subtotal:
            if kind == "fixed":
                coupon_discount = min(value, remaining)
            elif kind == "percent":
                coupon_discount = min(percent_of(remaining, value), remaining)
            elif kind == "free_shipping":
                final_shipping = 0

    total_discount = base_discount + coupon_discount
    total = subtotal - total_discount + final_shipping

    return {
        "subtotal": subtotal,
        "discount": total_discount,
        "shipping": final_shipping,
        "total": total
    }
