"""1-minute grid + 30-minute sliding windows for EVA ranking."""

from __future__ import annotations

from datetime import timedelta

from .timeutil import iso_z, parse_utc

DEFAULT_DURATION_MIN = 30
DEFAULT_STEP_MIN = 1
DEFAULT_TOP_K = 5
DEFAULT_D_FAR_KM = 10.0


def dist_factor(
    distance_km: float | None,
    d_crit: float | None,
    d_far: float,
) -> float:
    if distance_km is None:
        return 1.0
    lo = 0.0 if d_crit is None else float(d_crit)
    if d_crit is not None and distance_km < d_crit:
        return 0.0
    if distance_km >= d_far:
        return 1.0
    if d_far <= lo:
        return 1.0
    return (distance_km - lo) / (d_far - lo)


def _cell_safety(eva: float | None, factor: float) -> float:
    if eva is None:
        return factor
    return min(eva, factor)


def _minute_index(moment, origin) -> int:
    return int((moment - origin).total_seconds() // 60)


def _fill_distances(known: dict[int, float], n: int) -> list[float | None]:
    values: list[float | None] = [None] * n
    if not known or n <= 0:
        return values
    keys = sorted(idx for idx in known if 0 <= idx < n)
    if not keys:
        first = min(known)
        last = max(known)
        fill = known[first] if first < 0 else known[last]
        return [fill] * n
    for i in range(0, keys[0]):
        values[i] = known[keys[0]]
    for left, right in zip(keys, keys[1:]):
        values[left] = known[left]
        span = right - left
        for i in range(left + 1, right):
            t = (i - left) / span
            values[i] = known[left] + t * (known[right] - known[left])
    values[keys[-1]] = known[keys[-1]]
    for i in range(keys[-1] + 1, n):
        values[i] = known[keys[-1]]
    return values


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


def _mark_blocked(intervals: list[dict], origin, n: int) -> list[bool]:
    blocked = [False] * n
    minute = timedelta(minutes=1)
    for interval in intervals:
        c_start = parse_utc(interval["start_time"])
        c_end = parse_utc(interval["end_time"])
        for i in range(n):
            t0 = origin + timedelta(minutes=i)
            t1 = t0 + minute
            if c_start < t1 and c_end > t0:
                blocked[i] = True
    return blocked


def build_minute_grid(
    bundle: dict,
    *,
    d_far_km: float = DEFAULT_D_FAR_KM,
    d_crit_km: float | None = None,
) -> dict:
    origin = parse_utc(bundle["start"])
    end = parse_utc(bundle["end"])
    n = max(0, int((end - origin).total_seconds() // 60))
    weather = bundle.get("weather") or {}
    distances = bundle.get("distances") or {}
    request = distances.get("request") or {}
    if d_crit_km is None:
        d_crit_km = request.get("critical_distance_km")

    eva = _hold_eva(weather.get("records") or [], origin, n)

    known: dict[int, float] = {}
    for sample in distances.get("samples") or []:
        dist = sample.get("distance_km")
        if dist is None:
            continue
        idx = _minute_index(parse_utc(sample["timestamp"]), origin)
        if idx in known:
            known[idx] = min(known[idx], float(dist))
        else:
            known[idx] = float(dist)
    distance = _fill_distances(known, n)
    blocked = _mark_blocked(distances.get("critical_intervals") or [], origin, n)

    factor = [dist_factor(distance[i], d_crit_km, d_far_km) for i in range(n)]
    for i in range(n):
        if blocked[i]:
            factor[i] = 0.0
    safety = [_cell_safety(eva[i], factor[i]) for i in range(n)]
    return {
        "origin": origin,
        "n": n,
        "eva": eva,
        "distance": distance,
        "blocked": blocked,
        "dist_factor": factor,
        "safety": safety,
        "d_crit_km": d_crit_km,
        "d_far_km": d_far_km,
    }


def _window_stats(grid: dict, start_i: int, duration: int) -> dict | None:
    end_i = start_i + duration
    blocked_slice = grid["blocked"][start_i:end_i]
    if any(blocked_slice):
        return None
    safety_slice = grid["safety"][start_i:end_i]
    eva_slice = [value for value in grid["eva"][start_i:end_i] if value is not None]
    dist_slice = [value for value in grid["distance"][start_i:end_i] if value is not None]
    safety = min(safety_slice) if safety_slice else 0.0
    origin = grid["origin"]
    return {
        "start": iso_z(origin + timedelta(minutes=start_i)),
        "end": iso_z(origin + timedelta(minutes=end_i)),
        "safety": round(safety, 3),
        "danger": round(1.0 - safety, 3),
        "eva_min": round(min(eva_slice), 3) if eva_slice else None,
        "distance_min_km": round(min(dist_slice), 3) if dist_slice else None,
        "blocked": False,
    }


def _overlaps(left: dict, right: dict) -> bool:
    return left["start"] < right["end"] and right["start"] < left["end"]


def rank_eva_windows(
    bundle: dict,
    *,
    duration_min: int = DEFAULT_DURATION_MIN,
    step_min: int = DEFAULT_STEP_MIN,
    top_k: int = DEFAULT_TOP_K,
    non_overlap: bool = True,
    d_far_km: float = DEFAULT_D_FAR_KM,
    d_crit_km: float | None = None,
) -> dict:
    """Score 30-min EVA slots on a 1-min grid; return top-k by rising danger."""
    if duration_min <= 0 or step_min <= 0 or top_k <= 0:
        raise ValueError("duration_min, step_min, and top_k must be positive")

    grid = build_minute_grid(bundle, d_far_km=d_far_km, d_crit_km=d_crit_km)
    candidates = []
    last_start = grid["n"] - duration_min
    for start_i in range(0, last_start + 1, step_min):
        stats = _window_stats(grid, start_i, duration_min)
        if stats is not None:
            candidates.append(stats)

    candidates.sort(key=lambda item: (item["danger"], item["start"]))
    if non_overlap:
        picked = []
        for window in candidates:
            if any(_overlaps(window, taken) for taken in picked):
                continue
            picked.append(window)
            if len(picked) >= top_k:
                break
        windows = picked
    else:
        windows = candidates[:top_k]

    return {
        "start": bundle["start"],
        "end": bundle["end"],
        "windows": windows,
    }
