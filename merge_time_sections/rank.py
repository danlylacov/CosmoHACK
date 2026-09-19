"""Sliding windows of fixed duration over a minute grid of the horizon."""

from __future__ import annotations

import heapq
import math
from collections import deque
from datetime import datetime, timedelta

from .timeutil import iso_z, parse_utc

DEFAULT_DURATION_MIN = 30
DEFAULT_STEP_MIN = 30
DEFAULT_TOP_K = 5
DISTANCE_ATTENTION_KM = 10.0
DEFAULT_D_FAR_KM = DISTANCE_ATTENTION_KM

WEATHER_UNUSED_REASON = "Погодные факторы не участвовали в расчёте"
NO_CANDIDATES_REASON = (
    "SOCRATES не обнаружил отслеживаемых сближений в пределах области скрининга."
)
ORBITAL_NO_CRITICAL_REASON = (
    "По доступным орбитальным данным критические сближения не обнаружены."
)
ADVERSE_NONE_REASON = (
    "Неблагоприятные воздействия в пределах доступных данных не обнаружены."
)


def distance_safety(
    distance_km: float,
    d_crit: float,
    d_attention: float = DISTANCE_ATTENTION_KM,
) -> float:
    if distance_km < d_crit:
        value = 0.0
    elif distance_km >= d_attention:
        value = 1.0
    elif d_attention <= d_crit:
        value = 1.0
    else:
        value = (distance_km - d_crit) / (d_attention - d_crit)
    return min(1.0, max(0.0, value))


def dist_factor(
    distance_km: float | None,
    d_crit: float | None,
    d_far: float,
) -> float | None:
    """Piecewise distance safety. Unknown distance stays unknown (not 1.0)."""
    if distance_km is None or d_crit is None:
        return None
    return distance_safety(float(distance_km), float(d_crit), float(d_far))


def _minute_index(moment, origin) -> int:
    return int((moment - origin).total_seconds() // 60)


def _hold_eva(records: list[dict], origin, n: int) -> list[float | None]:
    values: list[float | None] = [None] * n
    if n <= 0:
        return values
    timed = []
    for record in records:
        timed.append((_minute_index(parse_utc(record["time"]), origin), record.get("eva_coefficient")))
    timed.sort(key=lambda item: item[0])
    for i, (idx, coef) in enumerate(timed):
        start_i = max(idx, 0)
        end_i = timed[i + 1][0] if i + 1 < len(timed) else n
        end_i = min(max(end_i, start_i), n)
        for j in range(start_i, end_i):
            values[j] = coef
    if values[0] is None and timed:
        first_coef = timed[0][1]
        first_idx = max(timed[0][0], 0)
        for j in range(0, min(first_idx, n)):
            values[j] = first_coef
    return values


def _combine_danger(debris: float | None, weather: float | None) -> float | None:
    if debris is None and weather is None:
        return None
    if weather is None:
        return debris
    if debris is None:
        return weather
    return max(debris, weather)


def _merge_half_open(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged: list[list[datetime]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _parse_interval_times(interval: dict) -> tuple[datetime, datetime] | None:
    try:
        start = parse_utc(interval["start_time"])
        end = parse_utc(interval["end_time"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def _critical_seconds_per_minute(
    merged: list[tuple[datetime, datetime]],
    origin: datetime,
    n: int,
) -> list[int]:
    out = [0] * n
    if not merged or n <= 0:
        return out
    j = 0
    minute = timedelta(minutes=1)
    for i in range(n):
        t0 = origin + timedelta(minutes=i)
        t1 = t0 + minute
        while j < len(merged) and merged[j][1] <= t0:
            j += 1
        k = j
        total = 0.0
        while k < len(merged) and merged[k][0] < t1:
            a = max(merged[k][0], t0)
            b = min(merged[k][1], t1)
            if b > a:
                total += (b - a).total_seconds()
            k += 1
        out[i] = int(min(60, max(0, round(total))))
    return out


def _interval_min_per_minute(
    intervals: list[dict],
    origin: datetime,
    n: int,
) -> list[float | None]:
    values: list[float | None] = [None] * n
    minute = timedelta(minutes=1)
    for interval in intervals:
        parsed = _parse_interval_times(interval)
        if parsed is None:
            continue
        c_start, c_end = parsed
        raw = interval.get("minimum_distance_km")
        if raw is None:
            continue
        try:
            dist = float(raw)
        except (TypeError, ValueError):
            continue
        for i in range(n):
            t0 = origin + timedelta(minutes=i)
            t1 = t0 + minute
            if c_start < t1 and c_end > t0:
                if values[i] is None or dist < values[i]:
                    values[i] = dist
    return values


def _find_safe_spans(is_safe: list[bool]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(is_safe)
    while i < n:
        if is_safe[i]:
            j = i + 1
            while j < n and is_safe[j]:
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _windows_overlap(a: dict, b: dict) -> bool:
    return a["start_i"] < b["end_i"] and b["start_i"] < a["end_i"]


def _classify_status(coverage: float, overlap: int, peak: float | None) -> str:
    if coverage < 1.0:
        return "INSUFFICIENT_DATA"
    if overlap > 0:
        return "REQUIRES_REVIEW"
    if peak is not None and peak > 0:
        return "CAUTION"
    return "SAFE"


def _reasons_for_window(
    *,
    status: str,
    critical_overlap_seconds: int,
    data_coverage: float,
    distance_min_km: float | None,
    no_candidates: bool,
    d_crit_km: float | None,
) -> list[str]:
    reasons: list[str] = []
    if status == "SAFE":
        reasons.append(ADVERSE_NONE_REASON)
        reasons.append(ORBITAL_NO_CRITICAL_REASON)
    elif status == "CAUTION":
        reasons.append(
            "Критических условий не обнаружено, но присутствуют факторы, требующие внимания."
        )
    elif status == "REQUIRES_REVIEW":
        minutes = max(1, math.ceil(critical_overlap_seconds / 60)) if critical_overlap_seconds else 0
        reasons.append(
            f"Окно пересекается с критическими условиями в течение {minutes} минут. "
            "Выполнение ВКД не рекомендуется без дополнительной проверки."
        )
    else:
        missing_pct = int(round((1.0 - data_coverage) * 100))
        reasons.append(
            f"Для {missing_pct}% окна отсутствуют необходимые данные. "
            "Безопасность окна не подтверждена."
        )

    if no_candidates:
        reasons.append(NO_CANDIDATES_REASON)

    if distance_min_km is not None:
        reasons.append(
            f"Минимальная дистанция до отслеживаемого объекта — {distance_min_km} км"
        )
        if (
            critical_overlap_seconds > 0
            and d_crit_km is not None
            and distance_min_km >= d_crit_km
        ):
            reasons.append(
                "Блокировка задана секундным уточнением критического интервала, "
                "а не минутной выборкой расстояния."
            )

    reasons.append(WEATHER_UNUSED_REASON)
    return reasons


def _public_window(internal: dict, no_candidates: bool, d_crit_km: float | None) -> dict:
    coverage = internal["data_coverage"]
    overlap = internal["critical_overlap_seconds"]
    peak = internal["peak_danger"]
    status = _classify_status(coverage, overlap, peak)
    if coverage == 0:
        safety = None
        danger = None
    else:
        peak_val = 0.0 if peak is None else peak
        danger = round(peak_val, 3)
        safety = round(1.0 - peak_val, 3)
        peak = peak_val

    avg = internal["average_danger"]
    dist_min = internal["distance_min_km"]
    if dist_min is not None:
        dist_min = round(float(dist_min), 3)

    factors = []
    if dist_min is not None or overlap > 0:
        factors.append({
            "type": "DEBRIS_PROXIMITY",
            "critical_overlap_seconds": overlap,
            "minimum_distance_km": dist_min,
        })

    return {
        "start": internal["start"],
        "end": internal["end"],
        "status": status,
        "safety": safety,
        "danger": danger,
        "peak_danger": None if coverage == 0 else round(peak if peak is not None else 0.0, 3),
        "average_danger": None if avg is None else round(avg, 3),
        "critical_overlap_seconds": overlap,
        "adverse_duration_seconds": internal["adverse_duration_seconds"],
        "data_coverage": round(coverage, 3),
        "eva_min": None,
        "distance_min_km": dist_min,
        "blocked": overlap > 0,
        "factors": factors,
        "reasons": _reasons_for_window(
            status=status,
            critical_overlap_seconds=overlap,
            data_coverage=coverage,
            distance_min_km=dist_min,
            no_candidates=no_candidates,
            d_crit_km=d_crit_km,
        ),
        "start_i": internal["start_i"],
        "end_i": internal["end_i"],
        "zone_id": internal.get("zone_id"),
        "margin": internal.get("margin", 0),
        "insufficient_data": coverage < 1.0,
    }


def _strip_internal(window: dict) -> dict:
    return {key: value for key, value in window.items() if key not in {
        "start_i", "end_i", "zone_id", "margin", "insufficient_data",
    }}


def _ranking_key(window: dict) -> tuple:
    peak = window["peak_danger"]
    avg = window["average_danger"]
    dmin = window["distance_min_km"]
    return (
        window["insufficient_data"],
        window["blocked"],
        window["critical_overlap_seconds"],
        float("inf") if peak is None else peak,
        float("inf") if avg is None else avg,
        window["adverse_duration_seconds"],
        -window["data_coverage"],
        -dmin if dmin is not None else float("inf"),
        window["start"],
    )


def _select_diverse_safe(windows: list[dict], top_k: int) -> list[dict]:
    if not windows or top_k <= 0:
        return []
    by_zone: dict[int, list[dict]] = {}
    for window in windows:
        by_zone.setdefault(int(window["zone_id"]), []).append(window)
    for zone_windows in by_zone.values():
        zone_windows.sort(key=lambda item: (-item["margin"], item["start_i"]))

    selected: list[dict] = []
    used: set[int] = set()
    zone_order = sorted(
        by_zone.keys(),
        key=lambda zone: (-by_zone[zone][0]["margin"], by_zone[zone][0]["start_i"]),
    )
    for zone in zone_order:
        if len(selected) >= top_k:
            break
        pick = by_zone[zone][0]
        selected.append(pick)
        used.add(pick["start_i"])

    rest = [window for window in windows if window["start_i"] not in used]
    rest.sort(key=lambda item: (-item["margin"], item["start_i"]))
    for window in rest:
        if len(selected) >= top_k:
            break
        if any(_windows_overlap(window, chosen) for chosen in selected):
            continue
        selected.append(window)
        used.add(window["start_i"])

    for window in rest:
        if len(selected) >= top_k:
            break
        if window["start_i"] in used:
            continue
        selected.append(window)
        used.add(window["start_i"])
    return selected


def build_minute_grid(
    bundle: dict,
    *,
    d_far_km: float = DISTANCE_ATTENTION_KM,
    d_crit_km: float | None = None,
) -> dict:
    origin = parse_utc(bundle["start"])
    end = parse_utc(bundle["end"])
    n = max(0, int((end - origin).total_seconds() // 60))
    weather = bundle.get("weather") or {}
    distances = bundle.get("distances") or {}
    request = distances.get("request") or {}
    if d_crit_km is None:
        raw_crit = request.get("critical_distance_km")
        d_crit_km = float(raw_crit) if raw_crit is not None else None

    dq = distances.get("data_quality") or {}
    no_candidates = dq.get("status") == "NO_CANDIDATES"

    eva = _hold_eva(weather.get("records") or [], origin, n)

    distance: list[float | None] = [None] * n
    known = [False] * n
    for sample in distances.get("samples") or []:
        idx = _minute_index(parse_utc(sample["timestamp"]), origin)
        if idx < 0 or idx >= n:
            continue
        dist = sample.get("distance_km")
        if dist is None:
            continue
        value = float(dist)
        if distance[idx] is None or value < distance[idx]:
            distance[idx] = value
        known[idx] = True

    if no_candidates:
        known = [True] * n

    raw_intervals = distances.get("critical_intervals") or []
    parsed_ranges = []
    for interval in raw_intervals:
        parsed = _parse_interval_times(interval)
        if parsed is not None:
            parsed_ranges.append(parsed)
    merged = _merge_half_open(parsed_ranges)
    critical_seconds = _critical_seconds_per_minute(merged, origin, n)
    interval_min = _interval_min_per_minute(raw_intervals, origin, n)

    debris_danger: list[float | None] = [None] * n
    distance_safety_vals: list[float | None] = [None] * n
    weather_danger: list[float | None] = [None] * n
    weather_blocked = [False] * n
    debris_blocked = [False] * n
    combined_danger: list[float | None] = [None] * n
    blocked = [False] * n

    for i in range(n):
        if no_candidates:
            distance_safety_vals[i] = 1.0
            debris_danger[i] = 0.0
        elif not known[i]:
            distance_safety_vals[i] = None
            debris_danger[i] = None
        elif d_crit_km is None:
            distance_safety_vals[i] = None
            debris_danger[i] = None
            known[i] = False
        else:
            safety = distance_safety(distance[i], d_crit_km, d_far_km)
            distance_safety_vals[i] = safety
            debris_danger[i] = 1.0 - safety

        if critical_seconds[i] > 0:
            distance_safety_vals[i] = 0.0
            debris_danger[i] = 1.0
            debris_blocked[i] = True

        combined_danger[i] = _combine_danger(debris_danger[i], weather_danger[i])
        blocked[i] = debris_blocked[i] or weather_blocked[i]

    is_safe = [
        known[i] is True
        and critical_seconds[i] == 0
        and combined_danger[i] == 0
        for i in range(n)
    ]

    effective_distance: list[float | None] = []
    for i in range(n):
        sample_d = distance[i]
        extra_d = interval_min[i]
        if sample_d is not None and extra_d is not None:
            effective_distance.append(min(sample_d, extra_d))
        elif sample_d is not None:
            effective_distance.append(sample_d)
        elif extra_d is not None:
            effective_distance.append(extra_d)
        else:
            effective_distance.append(None)

    return {
        "origin": origin,
        "n": n,
        "eva": eva,
        "distance": distance,
        "effective_distance": effective_distance,
        "known": known,
        "distance_safety": distance_safety_vals,
        "debris_danger": debris_danger,
        "weather_danger": weather_danger,
        "combined_danger": combined_danger,
        "critical_seconds": critical_seconds,
        "interval_min": interval_min,
        "debris_blocked": debris_blocked,
        "weather_blocked": weather_blocked,
        "blocked": blocked,
        "is_safe": is_safe,
        "d_crit_km": d_crit_km,
        "d_far_km": d_far_km,
        "no_candidates": no_candidates,
        "unknown_minutes": sum(1 for flag in known if not flag),
    }


def _prefix_sums(grid: dict) -> dict[str, list[float]]:
    n = grid["n"]
    known_p = [0]
    danger_p = [0.0]
    adverse_p = [0]
    crit_p = [0]
    for i in range(n):
        is_known = bool(grid["known"][i])
        danger = grid["combined_danger"][i]
        known_p.append(known_p[-1] + (1 if is_known else 0))
        danger_p.append(danger_p[-1] + (float(danger) if is_known and danger is not None else 0.0))
        adverse = 0
        if is_known and danger is not None and danger > 0:
            adverse = 60
        elif danger is not None and danger > 0:
            adverse = 60
        adverse_p.append(adverse_p[-1] + adverse)
        crit_p.append(crit_p[-1] + int(grid["critical_seconds"][i]))
    return {
        "known": known_p,
        "danger": danger_p,
        "adverse": adverse_p,
        "critical": crit_p,
    }


def _window_from_prefixes(
    *,
    left: int,
    duration: int,
    origin: datetime,
    prefixes: dict[str, list[float]],
    peak_danger: float | None,
    distance_min_km: float | None,
) -> dict:
    right = left + duration
    known_minutes = int(prefixes["known"][right] - prefixes["known"][left])
    total_danger = float(prefixes["danger"][right] - prefixes["danger"][left])
    adverse_duration_seconds = int(prefixes["adverse"][right] - prefixes["adverse"][left])
    critical_overlap_seconds = int(prefixes["critical"][right] - prefixes["critical"][left])
    data_coverage = known_minutes / duration if duration else 0.0
    average_danger = (total_danger / known_minutes) if known_minutes else None
    if peak_danger is None and known_minutes == duration and critical_overlap_seconds == 0:
        peak_danger = 0.0
    return {
        "start_i": left,
        "end_i": right,
        "start": iso_z(origin + timedelta(minutes=left)),
        "end": iso_z(origin + timedelta(minutes=right)),
        "known_minutes": known_minutes,
        "total_danger": total_danger,
        "critical_overlap_seconds": critical_overlap_seconds,
        "adverse_duration_seconds": adverse_duration_seconds,
        "data_coverage": data_coverage,
        "peak_danger": peak_danger,
        "average_danger": average_danger,
        "distance_min_km": distance_min_km,
        "blocked": critical_overlap_seconds > 0,
        "insufficient_data": data_coverage < 1.0,
    }


def _enumerate_all_windows(
    grid: dict,
    duration: int,
    step_min: int,
) -> list[dict]:
    n = grid["n"]
    origin = grid["origin"]
    prefixes = _prefix_sums(grid)
    last_start = n - duration
    if last_start < 0:
        return []

    danger = grid["combined_danger"]
    effective = grid["effective_distance"]
    peak_dq: deque[int] = deque()
    dist_dq: deque[int] = deque()
    windows: list[dict] = []

    for i in range(n):
        dval = danger[i]
        if dval is not None:
            while peak_dq and danger[peak_dq[-1]] <= dval:
                peak_dq.pop()
            peak_dq.append(i)

        dmin_i = effective[i]
        if dmin_i is not None:
            while dist_dq and effective[dist_dq[-1]] >= dmin_i:
                dist_dq.pop()
            dist_dq.append(i)

        left = i - duration + 1
        if left < 0:
            continue
        while peak_dq and peak_dq[0] < left:
            peak_dq.popleft()
        while dist_dq and dist_dq[0] < left:
            dist_dq.popleft()
        if left > last_start:
            continue
        if left % step_min != 0:
            continue

        peak = danger[peak_dq[0]] if peak_dq else None
        dmin = effective[dist_dq[0]] if dist_dq else None
        windows.append(_window_from_prefixes(
            left=left,
            duration=duration,
            origin=origin,
            prefixes=prefixes,
            peak_danger=peak,
            distance_min_km=dmin,
        ))
    return windows


def _safe_windows_in_zones(
    grid: dict,
    all_windows: list[dict],
    duration: int,
) -> list[dict]:
    spans = _find_safe_spans(grid["is_safe"])
    span_of: dict[int, int] = {}
    for zone_id, (start, end) in enumerate(spans):
        for i in range(start, end):
            span_of[i] = zone_id

    safe: list[dict] = []
    for window in all_windows:
        left = window["start_i"]
        right = window["end_i"]
        if left not in span_of:
            continue
        zone_id = span_of[left]
        span_start, span_end = spans[zone_id]
        if right > span_end:
            continue
        if not (
            window["data_coverage"] == 1
            and window["critical_overlap_seconds"] == 0
            and (window["peak_danger"] or 0) == 0
            and not window["insufficient_data"]
        ):
            continue
        window = dict(window)
        window["zone_id"] = zone_id
        window["margin"] = min(left - span_start, span_end - right)
        safe.append(window)
    return safe


def rank_eva_windows(
    bundle: dict,
    d: int = DEFAULT_DURATION_MIN,
    *,
    duration_min: int | None = None,
    step_min: int = DEFAULT_STEP_MIN,
    top_k: int = DEFAULT_TOP_K,
    d_far_km: float = DISTANCE_ATTENTION_KM,
    d_crit_km: float | None = None,
) -> dict:
    """Top-k windows of length `d` minutes, starts every `step_min`."""
    duration = duration_min if duration_min is not None else d
    if duration <= 0 or step_min <= 0 or top_k <= 0:
        raise ValueError("d, step_min, and top_k must be positive")

    grid = build_minute_grid(bundle, d_far_km=d_far_km, d_crit_km=d_crit_km)
    n = grid["n"]
    no_candidates = grid["no_candidates"]
    d_crit = grid["d_crit_km"]

    all_windows = [
        _public_window(item, no_candidates, d_crit)
        for item in _enumerate_all_windows(grid, duration, step_min)
    ]
    safe_internal = _safe_windows_in_zones(grid, all_windows, duration)
    safe_count = len(safe_internal)

    if safe_count >= top_k:
        chosen = _select_diverse_safe(safe_internal, top_k)
    else:
        chosen = _select_diverse_safe(safe_internal, safe_count)
        used = {item["start_i"] for item in chosen}
        fallback = heapq.nsmallest(top_k, all_windows, key=_ranking_key)
        for item in fallback:
            if len(chosen) >= top_k:
                break
            if item["start_i"] in used:
                continue
            chosen.append(item)
            used.add(item["start_i"])

    return {
        "start": bundle["start"],
        "end": bundle["end"],
        "d": duration,
        "n": n,
        "step_min": step_min,
        "windows": [_strip_internal(item) for item in chosen],
        "safe_count": safe_count,
        "unknown_minutes": grid["unknown_minutes"],
        "no_candidates": no_candidates,
    }
