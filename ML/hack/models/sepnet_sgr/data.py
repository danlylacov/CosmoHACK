"""Nine forecast targets with completed UTC Kp blocks and causal anchors."""

import numpy as np
import pandas as pd

from models.lstm import data_utils as common

STEP, HORIZON = common.STEP, common.HORIZON
TARGETS = common.TARGETS + ["solar_xray_flux_long", "solar_xray_flux_long_max", "geomagnetic_kp"]
G1 = 14 / 3
XRS_SCALE = 1e-5


def _index(frame):
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("Expected a UTC DatetimeIndex on the 30-minute grid")
    index = index.tz_convert("UTC")
    if (not index.equals(index.floor(STEP)) or not index.is_unique
            or (len(index) > 1 and not np.all(np.diff(index.asi8) == pd.Timedelta(STEP).value))):
        raise ValueError("Expected consecutive 30-minute rows, including missing windows")
    return index


def _raw(frame):
    values = frame.reindex(columns=TARGETS).to_numpy(dtype=np.float64, copy=True)
    values[~np.isfinite(values) | (values < 0)] = np.nan
    values[values[:, 8] > 9, 8] = np.nan
    return values


def targets(frame):
    """Observed S1/G1/R1 union; incomplete or inconsistent Kp blocks are unknown.

    This function validates whole target blocks, including future target rows.
    Inputs use a separate trailing calculation to avoid looking ahead.
    """
    index, values = _index(frame), _raw(frame)
    kp = pd.Series(values[:, 8], index=index)
    grouped = kp.groupby(index.floor("3h"))
    complete = grouped.transform("count").eq(6)
    consistent = grouped.transform("min").eq(grouped.transform("max"))
    values[~(complete & consistent).to_numpy(), 8] = np.nan
    required = values[:, [1, 7, 8]]
    event = np.full(len(frame), np.nan, dtype=np.float32)
    event[np.isfinite(required).all(axis=1)] = 0
    event[(values[:, 1] >= 10) | (values[:, 7] >= XRS_SCALE) | (values[:, 8] >= G1)] = 1
    # Keep physical thresholds exact for reports; encode() converts training tensors.
    return values, event


def encode(values):
    """Transform raw targets; the last axis is always the nine target fields."""
    values = np.array(values, dtype=np.float64, copy=True)
    if values.ndim == 0 or values.shape[-1] != len(TARGETS):
        raise ValueError("Expected nine targets on the last axis")
    values[~np.isfinite(values) | (values < 0)] = np.nan
    values[..., 8] = np.where(values[..., 8] <= 9, values[..., 8], np.nan)
    values[..., :6] = np.log1p(values[..., :6])
    values[..., 6:8] = np.log1p(values[..., 6:8] / XRS_SCALE)
    values[..., 8] /= 3
    return values.astype(np.float32)


def decode(values):
    """Invert encode for any array with nine targets on its last axis."""
    values = np.array(values, dtype=np.float64, copy=True)
    if values.ndim == 0 or values.shape[-1] != len(TARGETS):
        raise ValueError("Expected nine targets on the last axis")
    with np.errstate(over="ignore", invalid="ignore"):
        values[..., :6] = np.expm1(values[..., :6])
        values[..., 6:8] = np.expm1(values[..., 6:8]) * XRS_SCALE
    values[..., 8] *= 3
    return values


def inputs(frame, state):
    """Common features, next-window UTC phase, then nine encoded raw anchors.

    A Kp value becomes an anchor only at the end of six consistent observed
    half-hours. No validation of later rows can change an earlier anchor.
    The caller must mask interval values unavailable at its explicit cutoff.
    """
    index, values = _index(frame), _raw(frame)
    if state.get("anchors"):
        raise ValueError("SGR uses its own nine anchors; fit the common transformer with anchors=False")
    features = common.transform_features(frame, state)
    phase = ((index.hour * 2 + index.minute // 30 + 1) % 6).to_numpy()
    kp = pd.Series(values[:, 8], index=index)
    rolling = kp.rolling(6, min_periods=6)
    completed = (phase == 0) & rolling.count().eq(6) & rolling.min().eq(rolling.max())
    values[:, 8] = kp.where(completed).to_numpy()
    anchors = pd.DataFrame(encode(values))
    max_age = state.get("max_age", 96)
    if max_age > 1:
        anchors = anchors.ffill(limit=max_age - 1)
    anchors = anchors.fillna(0).to_numpy()
    anchors[:, 1:8:2] = np.maximum(anchors[:, :8:2], anchors[:, 1:8:2])
    return np.concatenate((features, phase[:, None], anchors), axis=1).astype(np.float32)


def split_origins(frame, y, context):
    """Disjoint target partitions; calibration/test origins are 33 hours apart."""
    index = _index(frame)
    if len(frame) != len(y) or context < 1 or not len(frame):
        raise ValueError("Need matching nonempty rows and targets, and positive context")
    active = (y[:, 1] >= 10) | (y[:, 7] >= XRS_SCALE) | (y[:, 8] >= G1)

    def floor_row(position):
        time = index[position] if position < len(index) else index[-1] + pd.Timedelta(STEP)
        return int(index.searchsorted(time.floor("3h")))

    boundaries = []
    for fraction in (0.7, 0.8, 0.9):
        boundary = floor_row(int(len(frame) * fraction))
        while boundary > 0 and active[boundary] and active[boundary - 1]:
            start = boundary
            while start > 0 and active[start] and active[start - 1]:
                start -= 1
            boundary = floor_row(start)
        boundaries.append(boundary)
    bounds = [context] + boundaries + [floor_row(len(frame))]
    groups = [np.arange(start, end - HORIZON + 1, 1 if i < 2 else 66, dtype=int)
              for i, (start, end) in enumerate(zip(bounds[:-1], bounds[1:]))]
    if any(len(group) == 0 for group in groups):
        raise ValueError("Archive too short for 72h history, 32h targets and four chronological parts. "
                         "Add history or use --smoke for a technical check.")
    return groups, boundaries
