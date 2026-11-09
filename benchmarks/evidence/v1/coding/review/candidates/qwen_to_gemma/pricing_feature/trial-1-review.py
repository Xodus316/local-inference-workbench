from money import percent_of

def _validate_coupon(coupon):
    if not isinstance(coupon, dict):
        raise ValueError("coupon must be a dict")
    
    allowed_keys = {'kind', 'value', 'min_subtotal'}
    if not set(coupon.keys()).issubset(allowed_keys):
        raise ValueError("coupon contains invalid keys")
    
    if 'kind' not in coupon:
        raise ValueError("coupon missing 'kind'")
    
    kind = coupon['kind']
    if kind not in ('fixed', 'percent', 'free_shipping'):
        raise ValueError("invalid coupon kind")
    
    # Validate min_subtotal if present
    if 'min_subtotal' in coupon:
        ms = coupon['min_subtotal']
        if isinstance(ms, bool) or not isinstance(ms, int):
            raise ValueError("min_subtotal must be an exact int")
        if ms < 0:
            raise ValueError("min_subtotal must be nonnegative")
    
    # Validate value based on kind
    if kind in ('fixed', 'percent'):
        if 'value' not in coupon:
            raise ValueError(f"coupon kind '{kind}' requires 'value'")
        v = coupon['value']
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("value must be an exact int")
        if kind == 'fixed' and v < 0:
            raise ValueError("fixed value must be nonnegative")
        if kind == 'percent' and (v < 0 or v > 100):
            raise ValueError("percent value must be 0..100")
    else:  # free_shipping
        if 'value' in coupon:
            raise ValueError("free_shipping must not have 'value'")

def quote(lines, catalog, *, discount_percent=0, shipping_cents=0, coupon=None):
    subtotal = sum(catalog[sku] * quantity for sku, quantity in lines)
    
    # Validate coupon structure first (even if threshold not met)
    if coupon is not None:
        _validate_coupon(coupon)
    
    # Original discount
    discount = percent_of(subtotal, discount_percent)
    
    # Coupon logic
    coupon_discount = 0
    final_shipping = shipping_cents
    
    if coupon is not None:
        min_subtotal = coupon.get('min_subtotal', 0)
        if subtotal >= min_subtotal:
            kind = coupon['kind']
            if kind == 'fixed':
                remaining = subtotal - discount
                coupon_discount = min(coupon['value'], remaining)
            elif kind == 'percent':
                remaining = subtotal - discount
                coupon_discount = min(percent_of(remaining, coupon['value']), remaining)
            elif kind == 'free_shipping':
                final_shipping = 0
    
    total_discount = discount + coupon_discount
    total = subtotal - total_discount + final_shipping
    
    return {
        "subtotal": subtotal,
        "discount": total_discount,
        "shipping": final_shipping,
        "total": total
    }