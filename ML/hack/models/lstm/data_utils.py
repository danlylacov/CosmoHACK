"""Read 30-minute measurement CSVs and prepare causal neural-network inputs."""
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd

STEP = "30min"
HORIZON = 64
METRICS = [f"proton_flux_above_{energy}_mev" for energy in (10, 50, 100)]
TARGETS = [column for metric in METRICS for column in (metric, metric + "_max")]
_METRIC = re.compile(
    r"(?:proton_flux_above_\d+_mev|electron_flux_above_2_mev|"
    r"solar_xray_flux_(?:long|short)|solar_wind_(?:speed|proton_density|"
    r"proton_temperature|dynamic_pressure)|magnetic_field_(?:magnitude|"
    r"[xyz]_(?:gse|gsm)))(?:_(?:min|max|last|count|coverage_fraction))?\Z"
)


def load_data(path):
    """Merge files, reject conflicting overlaps and retain empty time windows."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.rglob("*.csv"))
    parts, sources = [], []
    for file in files:
        if file.stat().st_size == 0:
            raise ValueError(f"{file}: empty CSV; finish downloading/converting the data first")
        columns = pd.read_csv(file, nrows=0).columns
        if "issued_at_utc" in columns:
            continue  # Text forecasts need a separate publication-aware encoder.
        if "time_utc" not in columns:
            raise ValueError(f"{file}: missing time_utc column")
        part = pd.read_csv(file)
        if part.empty:
            continue
        times = pd.to_datetime(part.pop("time_utc"), utc=True, errors="raise")
        if times.isna().any() or not times.eq(times.dt.floor(STEP)).all():
            raise ValueError(f"{file}: time_utc must lie on the UTC 30-minute grid")
        part = part.apply(pd.to_numeric, errors="raise").replace([np.inf, -np.inf], np.nan)
        part.index = pd.DatetimeIndex(times, name="time_utc")
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        sources.append({"path": str(file.resolve()), "sha256": digest.hexdigest(),
                        "rows": len(part), "start": part.index.min().isoformat(),
                        "end": part.index.max().isoformat()})
        parts.append(part)
    if not parts:
        raise ValueError(f"No measurement rows found in {path}")
    frame = pd.concat(parts).sort_index()
    duplicates = frame[frame.index.duplicated(keep=False)]
    if not duplicates.empty:
        conflicts = duplicates.groupby(level=0).nunique(dropna=True).gt(1)
        if conflicts.to_numpy().any():
            row, col = np.argwhere(conflicts.to_numpy())[0]
            raise ValueError(f"Conflicting CSV values at {conflicts.index[row]} "
                             f"for {conflicts.columns[col]}")
        frame = frame.groupby(level=0).first()
    slots = int((frame.index[-1] - frame.index[0]) / pd.Timedelta(STEP)) + 1
    if slots > 1_000_000 or slots * len(frame.columns) > 100_000_000:
        raise ValueError("CSV time span is too large; check timestamps or split the archive")
    grid = pd.date_range(frame.index[0], periods=slots, freq=STEP, name="time_utc")
    return frame.reindex(grid), sources


def completed_inputs(frame, cutoff):
    """Exclude raw interval indices whose complete interval ends after cutoff."""
    result = frame.loc[frame.index + pd.Timedelta(STEP) <= cutoff].copy()
    for column in result:
        period = ("24h" if column.startswith(("geomagnetic_daily_", "solar_radio_flux_"))
                  or column == "sunspot_number" else
                  "3h" if column in ("geomagnetic_kp", "geomagnetic_ap") else
                  "1h" if column in ("geomagnetic_hp60", "geomagnetic_ap60") else None)
        if period:
            unfinished = result.index.floor(period) + pd.Timedelta(period) > cutoff
            result.loc[unfinished, column] = np.nan
    return result


def prepare_features(frame, feature_set="all"):
    """Respect completed index intervals; derive features from past rows only."""
    frame = frame.copy()
    for column in frame:
        lag = (48 if column.startswith(("geomagnetic_daily_", "solar_radio_flux_"))
               or column == "sunspot_number" else
               5 if column in ("geomagnetic_kp", "geomagnetic_ap") else
               1 if column in ("geomagnetic_hp60", "geomagnetic_ap60") else 0)
        if lag:
            frame[column] = frame[column].shift(lag)
    if feature_set != "engineered":
        return frame
    extra = {}
    selected = TARGETS + ["solar_xray_flux_long", "solar_xray_flux_short",
                         "solar_wind_speed", "solar_wind_proton_density",
                         "magnetic_field_z_gsm", "magnetic_field_magnitude"]
    for column in selected:
        if column not in frame:
            continue
        raw = frame[column]
        if "flux" in column:
            raw = raw.where(raw >= 0)
        series = (np.log10(raw.clip(lower=1e-12)) if column.startswith("solar_xray_")
                  else np.sign(raw) * np.log1p(raw.abs()))
        for hours in (1, 6, 24):
            window = hours * 2
            extra[f"derived:{column}:mean_{hours}h"] = series.rolling(window, min_periods=1).mean()
            extra[f"derived:{column}:std_{hours}h"] = series.rolling(window, min_periods=2).std()
            extra[f"derived:{column}:change_{hours}h"] = series - series.shift(window)
    if isinstance(frame.index, pd.DatetimeIndex):
        for name, phase in (("hour", frame.index.hour / 24 + frame.index.minute / 1440),
                            ("year", (frame.index.dayofyear - 1) / 365.25)):
            extra[f"derived:time:{name}_sin"] = np.sin(2 * np.pi * phase)
            extra[f"derived:time:{name}_cos"] = np.cos(2 * np.pi * phase)
    return pd.concat((frame, pd.DataFrame(extra, index=frame.index)), axis=1)


def select_features(frame, feature_set="all"):
    """Every observed numeric input; core is retained for controlled experiments."""
    return sorted(column for column in frame.columns
                  if (feature_set != "core" or _METRIC.fullmatch(column))
                  and frame[column].notna().any()
                  and not (column.endswith("_count") and column[:-6] in frame
                           and not frame[column[:-6]].notna().any()))


def targets(frame):
    """Labels describe the observed CSV maxima; missing maxima stay unknown."""
    values = frame.reindex(columns=TARGETS).to_numpy(dtype=np.float32, copy=True)
    values[~np.isfinite(values) | (values < 0)] = np.nan
    maximum10, maximum100 = values[:, 1], values[:, 5]
    valid = np.isfinite(maximum10) & np.isfinite(maximum100)
    labels = np.full(len(frame), np.nan, dtype=np.float32)
    labels[valid] = 0
    labels[(maximum10 >= 10) | (maximum100 >= 1)] = 1
    return values, labels


def _feature_values(frame, columns, version=1):
    values = frame.reindex(columns=columns).to_numpy(dtype=np.float64, copy=True)
    values[~np.isfinite(values)] = np.nan
    for index, column in enumerate(columns):
        if column.startswith("derived:"):
            continue
        if column.endswith(("_count", "_coverage_fraction")):
            values[values[:, index] < 0, index] = np.nan
            if version >= 2:
                values[:, index] = np.log1p(values[:, index])
        elif "flux" in column or column.startswith(("differential_", "solar_euv_")):
            values[values[:, index] < 0, index] = np.nan
            if column.startswith(("solar_xray_", "solar_euv_")):
                values[:, index] = np.log10(np.maximum(values[:, index], 1e-12))
            else:
                values[:, index] = np.log1p(values[:, index])
        elif version >= 2 and not column.startswith("solar_mgii_"):
            values[:, index] = np.sign(values[:, index]) * np.log1p(np.abs(values[:, index]))
    return values


def fit_transformer(train_frame, columns=None, max_age=96, feature_set="all", anchors=False):
    """Fit only on training rows; preserve every choice in the checkpoint."""
    frame = prepare_features(train_frame, feature_set)
    columns = select_features(frame, feature_set) if columns is None else list(columns)
    if not columns or max_age < 1:
        raise ValueError("Need observed features and a positive max_age")
    values = _feature_values(frame, columns, version=2)
    observed = np.isfinite(values)
    counts = observed.sum(axis=0).clip(min=1)
    mean = np.nansum(values, axis=0) / counts
    variance = np.nansum((values - mean) ** 2, axis=0) / counts
    scale = np.sqrt(variance)
    scale[scale < 1e-12] = 1
    return {"columns": columns, "mean": mean.tolist(), "scale": scale.tolist(),
            "max_age": int(max_age), "feature_set": feature_set, "version": 2,
            "anchors": bool(anchors)}


def transform_features(frame, state):
    """Values + missing masks + age; no filling or rolling from the future."""
    prepared = prepare_features(frame, state["feature_set"]) if "feature_set" in state else frame
    values = _feature_values(prepared, state["columns"], state.get("version", 1))
    observed = np.isfinite(values)
    max_age = state["max_age"]
    positions = np.arange(len(frame))[:, None]
    last = np.maximum.accumulate(np.where(observed, positions, -max_age), axis=0)
    ages = np.minimum(positions - last, max_age)
    filled = pd.DataFrame(values).ffill(limit=max_age - 1 if max_age > 1 else 1).to_numpy()
    filled[ages >= max_age] = np.nan
    scaled = (filled - np.asarray(state["mean"])) / np.asarray(state["scale"])
    scaled = np.nan_to_num(scaled, nan=0, posinf=10, neginf=-10).clip(-10, 10)
    features = [scaled, ~observed, ages / max_age]
    if state.get("anchors"):
        y, _ = targets(frame)
        base = pd.DataFrame(np.log1p(y))
        if max_age > 1:
            base = base.ffill(limit=max_age - 1)
        base = base.fillna(0).to_numpy()
        base[:, 1::2] = np.maximum(base[:, 0::2], base[:, 1::2])
        features.append(base)
    return np.concatenate(features, axis=1).astype(np.float32)
