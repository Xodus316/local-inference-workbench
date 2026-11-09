from money import percent_of

def _validate_coupon(coupon):
    if not isinstance(coupon, dict):
        raise ValueError("coupon must be a dict")
    
    allowed_keys = {'kind', 'value', 'min_subtotal'}
    if not set(coupon.keys()).issubset(allowed_keys):
        raise ValueError("coupon contains invalid keys")
    
    if 'kind' not in coupon:
        raise ValueError("coupon must have a 'kind' key")
    
    kind = coupon['kind']
    if kind not in ('fixed', 'percent', 'free_shipping'):
        raise ValueError("invalid coupon kind")
    
    # Validate min_subtotal if present
    if 'min_subtotal' in coupon:
        min_subtotal = coupon['min_subtotal']
        if isinstance(min_subtotal, bool) or not isinstance(min_subtotal, int):
            raise ValueError("min_subtotal must be an exact nonnegative int")
        if min_subtotal < 0:
            raise ValueError("min_subtotal must be nonnegative")
    
    # Validate value based on kind
    if kind in ('fixed', 'percent'):
        if 'value' not in coupon:
            raise ValueError(f"{kind} coupon requires a 'value' key")
        value = coupon['value']
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{kind} coupon value must be an exact int")
        if kind == 'fixed' and value < 0:
            raise ValueError("fixed coupon value must be nonnegative")
        if kind == 'percent' and not (0 <= value <= 100):
            raise ValueError("percent coupon value must be between 0 and 100")
    else:  # free_shipping
        if 'value' in coupon:
            raise ValueError("free_shipping coupon must not have a 'value' key")

def quote(lines, catalog, *, discount_percent=0, shipping_cents=0, coupon=None):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    
    # Validate coupon structure first, even if threshold not met
    if coupon is not None:
        _validate_coupon(coupon)
    
    # Original discount
    discount = percent_of(subtotal, discount_percent)
    
    # Coupon logic
    additional_discount = 0
    final_shipping = shipping_cents
    
    if coupon is not None:
        min_subtotal = coupon.get('min_subtotal', 0)
        if subtotal >= min_subtotal:
            kind = coupon['kind']
            if kind == 'fixed':
                value = coupon['value']
                remaining = subtotal - discount
                additional_discount = min(value, remaining)
            elif kind == 'percent':
                value = coupon['value']
                remaining = subtotal - discount
                additional_discount = min(percent_of(remaining, value), remaining)
            elif kind == 'free_shipping':
                final_shipping = 0
    
    total_discount = discount + additional_discount
    total = subtotal - total_discount + final_shipping
    
    return {
        "subtotal": subtotal,
        "discount": total_discount,
        "shipping": final_shipping,
        "total": total
    }
