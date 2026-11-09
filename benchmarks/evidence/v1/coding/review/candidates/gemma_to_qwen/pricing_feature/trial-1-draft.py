from money import percent_of

def quote(lines, catalog, *, discount_percent=0, shipping_cents=0, coupon=None):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    original_discount = percent_of(subtotal, discount_percent)
    
    current_shipping = shipping_cents
    coupon_discount = 0
    
    if coupon is not None:
        if not isinstance(coupon, dict):
            raise ValueError("Coupon must be a dictionary")
        
        # Validate keys
        allowed_keys = {"kind", "value", "min_subtotal"}
        if not set(coupon.keys()).issubset(allowed_keys) or "kind" not in coupon:
            raise ValueError("Invalid coupon keys")
        
        kind = coupon["kind"]
        if kind not in ("fixed", "percent", "free_shipping"):
            raise ValueError("Invalid coupon kind")
        
        # Validate min_subtotal
        min_sub = coupon.get("min_subtotal", 0)
        if not isinstance(min_sub, int) or isinstance(min_sub, bool) or min_sub < 0:
            raise ValueError("Invalid min_subtotal")
            
        # Validate value based on kind
        if kind in ("fixed", "percent"):
            if "value" not in coupon:
                raise ValueError("Fixed/percent coupons require a value")
            val = coupon["value"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError("Coupon value must be an integer")
            if kind == "fixed" and val < 0:
                raise ValueError("Fixed discount cannot be negative")
            if kind == "percent" and not (0 <= val <= 100):
                raise ValueError("Percent discount must be 0-100")
        elif kind == "free_shipping":
            if "value" in coupon:
                raise ValueError("free_shipping coupon must not have a value")

        # Apply coupon if threshold met
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