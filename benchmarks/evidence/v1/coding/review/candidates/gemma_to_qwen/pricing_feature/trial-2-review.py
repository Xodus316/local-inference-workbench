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
        if not set(coupon.keys()).issubset(allowed_keys):
            raise ValueError("Invalid keys in coupon")
        
        kind = coupon.get("kind")
        if kind not in ("fixed", "percent", "free_shipping"):
            raise ValueError("Invalid coupon kind")
        
        # Validate min_subtotal
        min_sub = coupon.get("min_subtotal", 0)
        if not isinstance(min_sub, int) or isinstance(min_sub, bool) or min_sub < 0:
            raise ValueError("Invalid min_subtotal")
        
        # Validate value based on kind
        val = coupon.get("value")
        if kind == "fixed":
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise ValueError("Invalid fixed coupon value")
        elif kind == "percent":
            if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= 100):
                raise ValueError("Invalid percent coupon value")
        elif kind == "free_shipping":
            if "value" in coupon:
                raise ValueError("free_shipping coupon must not have a value")
        
        # Apply coupon if eligible
        if subtotal >= min_sub:
            remaining = subtotal - original_discount
            if kind == "fixed":
                coupon_discount = min(val, remaining)
            elif kind == "percent":
                coupon_discount = min(percent_of(remaining, val), remaining)
            elif kind == "free_shipping":
                current_shipping = 0
                
    total_discount = original_discount + coupon_discount
    return {
        "subtotal": subtotal,
        "discount": total_discount,
        "shipping": current_shipping,
        "total": subtotal - total_discount + current_shipping
    }
