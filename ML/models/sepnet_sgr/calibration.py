"""Calibrate S/G/R uncertainty; keep holdout metrics separate from availability."""

import math

import numpy as np

from models.lstm.calibration import _event_metrics, _platt, _sigmoid

HORIZON, TARGETS, LEVEL = 64, 9, 0.8


def default_state():
    return {
        "interval_level": LEVEL,
        "interval_offsets": [0.0] * TARGETS,
        "interval_available": [False] * TARGETS,
        "probability": {"scale": 1.0, "bias": 0.0, "available": False,
                        "reason": "insufficient_calibration_data"},
    }


def _bounds(q, offsets):
    lower = np.maximum(0, q[..., 0] - offsets)
    upper = q[..., 2] + offsets
    # Widen the four mean/max pairs without changing the Kp target.
    lower[..., :8:2] = np.minimum(lower[..., :8:2], lower[..., 1:8:2])
    upper[..., 1:8:2] = np.maximum(upper[..., :8:2], upper[..., 1:8:2])
    lower[..., 8] = np.clip(lower[..., 8], 0, 3)
    upper[..., 8] = np.clip(upper[..., 8], 0, 3)
    return lower, upper


def intervals(q, state):
    """Return bounds in encoded target units; missing calibration yields NaN."""
    lower, upper = _bounds(np.asarray(q), np.asarray(state["interval_offsets"]))
    available = np.asarray(state["interval_available"], dtype=bool)
    return np.where(available, lower, np.nan), np.where(available, upper, np.nan)


def probabilities(logits, state):
    """Return fitted P(S>=1 or G>=1 or R>=1), independently of holdout skill."""
    logits = np.asarray(logits)
    fit = state["probability"]
    if not fit["available"]:
        return np.full(logits.shape, np.nan)
    return _sigmoid(logits * fit["scale"] + fit["bias"])


def calibrate(cal_outputs, test_outputs, policy, classifier_trained):
    """Use one maximum residual per origin/target and one pooled Platt fit.

    Outputs are (quantiles, logits, encoded targets, joint event labels). Global
    target offsets preserve Kp consistency within UTC three-hour intervals.
    Holdout data only populate the report; they never change the fitted state.
    """
    state = default_state()
    report = {"intervals": {}, "probability": {}, "notes": [
        "Offsets use the 80% order statistic of per-origin maximum residuals across 64 horizons.",
        "Targets retain their separate encoded units; coverage does not require decoding.",
        "Temporal dependence and few independent events limit conclusions from internal scores.",
        "POD and FAR describe windows; FAR is false alarms / all warnings.",
    ]}
    if cal_outputs is None:
        return state, report
    cq, cl, cy, ce = (np.asarray(value) for value in cal_outputs)
    if test_outputs is None:
        test_outputs = tuple(np.empty((0,) + value.shape[1:]) for value in (cq, cl, cy, ce))
    tq, tl, ty, te = (np.asarray(value) for value in test_outputs)
    minimum = policy.get("min_calibration_samples", 10)
    observed = np.isfinite(cq[..., 0]) & np.isfinite(cq[..., 2]) & np.isfinite(cy)
    residual = np.maximum(np.maximum(cq[..., 0] - cy, cy - cq[..., 2]), 0)
    scores = np.max(np.where(observed, residual, -np.inf), axis=1)
    counts = np.isfinite(scores).sum(0)
    for target in range(TARGETS):
        values = scores[:, target][np.isfinite(scores[:, target])]
        n = len(values)
        if n >= minimum:
            rank = min(n, math.ceil((n + 1) * LEVEL))
            state["interval_offsets"][target] = float(np.partition(values, rank - 1)[rank - 1])
            state["interval_available"][target] = True
    lower, upper = intervals(tq, state)
    observed = np.isfinite(ty) & np.isfinite(lower) & np.isfinite(upper)
    test_counts = observed.sum(0)
    hits = (observed & (ty >= lower) & (ty <= upper)).sum(0)
    coverage = np.divide(hits, test_counts, out=np.zeros((HORIZON, TARGETS)), where=test_counts > 0).astype(object)
    coverage[test_counts == 0] = None
    report["intervals"] = {
        "method": "max_residual_per_origin_and_target", "calibration_origins": counts.tolist(),
        "available": state["interval_available"].copy(),
        "test_samples": test_counts.tolist(), "coverage": coverage.tolist(),
    }
    cm = np.isfinite(cl) & np.isin(ce, [0, 1])
    tm = np.isfinite(tl) & np.isin(te, [0, 1])
    c_logits, c_labels = cl[cm], ce[cm]
    t_logits, t_labels = tl[tm], te[tm]
    metrics = {"calibration_origins": int(cm.any(1).sum()), "test_origins": int(tm.any(1).sum()),
               "calibration_windows": int(cm.sum()), "test_windows": int(tm.sum()),
               "calibration_positive_windows": int((c_labels == 1).sum())}
    fit = state["probability"]
    if not classifier_trained:
        fit["reason"] = "event_head_untrained"
    elif metrics["calibration_origins"] < minimum or len(np.unique(c_labels)) < 2:
        fit["reason"] = "insufficient_calibration_origins_or_classes"
    else:
        scale, bias = _platt(c_logits, c_labels)
        if np.isfinite([scale, bias]).all():
            fit.update(scale=scale, bias=bias, available=True, reason="calibrated")
            if len(t_labels):
                metrics.update(_event_metrics(t_labels, _sigmoid(t_logits * scale + bias),
                                              float(c_labels.mean()), policy.get("risk_threshold", 0.1)))
        else:
            fit["reason"] = "calibration_fit_failed"
    report["probability"] = dict(metrics, available=fit["available"], reason=fit["reason"])
    return state, report
