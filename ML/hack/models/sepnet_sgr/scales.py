"""Research S/G/R estimates from NOAA thresholds, not official alerts.

G uses explicit lower bounds for the NOAA three-day forecast's Kp thirds:
5-, 6-, 7-, 8-, 9o. For continuous model outputs we compare directly, without
rounding: Kp=4.5 is G0, 8.667 (9-) is G4, and only Kp=9 is G5.
The continuous extension is a project convention; Kp retains its 3-hour basis.
"""

import math
from bisect import bisect_right


THRESHOLDS = {
    "S": (10, 100, 1000, 10000, 100000),  # >=10 MeV protons, pfu.
    "G": (14 / 3, 17 / 3, 20 / 3, 23 / 3, 9),
    "R": (1e-5, 5e-5, 1e-4, 1e-3, 2e-3),  # 0.1-0.8 nm X-rays, W/m2.
}
SOURCES = {
    "noaa_scales": "https://www.spaceweather.gov/noaa-scales-explanation",
    "noaa_forecast": "https://www.spaceweather.gov/products/3-day-forecast",
    "gfz_kp_notation": "https://datapub.gfz.de/download/10.5880.Kp.0001/kp_index_data_description_20210311.pdf",
}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def level(value, scale):
    """Return 0..5, or None for missing/invalid input; G requires Kp <= 9."""
    scale = scale.upper()
    if scale not in THRESHOLDS:
        raise ValueError("Scale must be S, G or R")
    value = _number(value)
    if value is None or (scale == "G" and value > 9):
        return None
    return bisect_right(THRESHOLDS[scale], value)


def levels(sflux, kp, xray):
    """Map proton maximum, 3-hour Kp and X-ray maximum to separate levels."""
    return {"s_level": level(sflux, "S"), "g_level": level(kp, "G"),
            "r_level": level(xray, "R")}


def validate_policy(policy):
    """Normalize configurable research block levels; NOAA does not set EVA policy."""
    result = dict(policy)
    for scale in "sgr":
        key = f"block_{scale}"
        value = _number(result.get(key, 1))
        if value is None or not value.is_integer() or not 1 <= value <= 5:
            raise ValueError(f"{key} must be an integer level from 1 to 5")
        result[key] = int(value)
    return result


def decision(values, policy):
    """Return (allowed, reasons), giving known danger priority over missing data."""
    policy = validate_policy(policy)
    reasons, blocked, missing = [], False, False
    for scale in "sgr":
        key = f"{scale}_level"
        value = _number(values.get(key))
        if value is None or not value.is_integer() or value > 5:
            reasons.append(f"missing_{key}")
            missing = True
        elif value >= policy[f"block_{scale}"]:
            reasons.append(f"{key}_{int(value)}_ge_block_{scale}_{policy[f'block_{scale}']}")
            blocked = True
    return (False if blocked else None if missing else True), reasons
