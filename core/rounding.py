from decimal import ROUND_HALF_UP, Decimal


def largest_remainder_rounding(parts: dict, total: Decimal, places: int = 3) -> dict:
    """
    Arrondi au millime (TND) en conservant une somme exacte.
    Utilisé par M1 (poids) et M2 (parts de ticket).
    """
    quantize_exp = Decimal("1").scaleb(-places)
    raw = {key: Decimal(value) for key, value in parts.items()}
    floored = {key: value.quantize(quantize_exp, rounding=ROUND_HALF_UP) for key, value in raw.items()}
    remainder = Decimal(total) - sum(floored.values())
    if remainder == 0:
        return floored

    unit = quantize_exp if remainder > 0 else -quantize_exp
    ranked = sorted(
        raw.keys(),
        key=lambda key: (raw[key] - floored[key], str(key)),
        reverse=remainder > 0,
    )
    steps = int(abs(remainder / unit))
    for key in ranked[:steps]:
        floored[key] += unit
    return floored
