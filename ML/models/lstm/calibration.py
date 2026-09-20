"""Fit uncertainty on calibration origins; report separate holdout validation."""

import math

import numpy as np
import torch
from torch.nn import functional as F


BANDS = [(0, 12), (12, 24), (24, 48), (48, 64)]
LEVEL = 0.8


def default_state():
    return {
        "interval_level": LEVEL,
        "interval_offsets": np.zeros((64, 6)).tolist(),
        "interval_calibrated": np.zeros((64, 6), dtype=bool).tolist(),
        "interval_valid": np.zeros((64, 6), dtype=bool).tolist(),
        "probability_groups": [
            dict(start=a, end=b, scale=1.0, bias=0.0, calibrated=False, valid=False,
                 reason="insufficient_calibration_data") for a, b in BANDS
        ],
    }


def _bounds(q, offsets):
    lower = np.maximum(0, q[..., 0] - offsets)
    upper = q[..., 2] + offsets
    # Widen only: preserve the mean <= maximum relationship after calibration.
    lower[..., 0::2] = np.minimum(lower[..., 0::2], lower[..., 1::2])
    upper[..., 1::2] = np.maximum(upper[..., 0::2], upper[..., 1::2])
    return lower, upper


def intervals(q, state, *, validated=False):
    """Return fitted log1p(pfu) bounds, optionally requiring holdout validation.

    Finite experimental bounds do not imply verified coverage: callers must
    check ``interval_valid`` when requiring verified coverage.
    """
    lower, upper = _bounds(np.asarray(q), np.asarray(state["interval_offsets"]))
    available = np.asarray(state["interval_valid"], dtype=bool)
    if not validated:
        available = available | np.asarray(state.get("interval_calibrated", available), dtype=bool)
    return np.where(available, lower, np.nan), np.where(available, upper, np.nan)


def _sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -60, 60)))


def probabilities(logits, state, *, validated=False):
    """Return fitted probabilities, optionally requiring holdout validation.

    The group's ``valid`` flag records holdout skill for evaluation; callers can
    explicitly require it through ``validated=True``.
    Legacy states have only ``valid`` and retain their original masking.
    """
    logits = np.asarray(logits)
    result = np.full(logits.shape, np.nan, dtype=float)
    for group in state["probability_groups"]:
        available = group["valid"] or (not validated and group.get("calibrated", False))
        if available:
            band = slice(group["start"], group["end"])
            result[..., band] = _sigmoid(
                logits[..., band] * group["scale"] + group["bias"])
    return result


def _platt(logits, labels):
    x = torch.as_tensor(logits, dtype=torch.float64)
    y = torch.as_tensor(labels, dtype=torch.float64)
    prevalence = float(y.mean())
    slope = torch.tensor(0.0, dtype=torch.float64, requires_grad=True)
    bias = torch.tensor(math.log(prevalence / (1 - prevalence)),
                        dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([slope, bias], max_iter=75,
                                 line_search_fn="strong_wolfe")

    def closure():
        optimizer.zero_grad()
        loss = F.binary_cross_entropy_with_logits(F.softplus(slope) * x + bias, y)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(F.softplus(slope).detach()), float(bias.detach())


def _event_metrics(labels, p, prevalence, threshold):
    truth, warning = labels == 1, p >= threshold
    hit = int(np.sum(truth & warning))
    missed = int(np.sum(truth & ~warning))
    false = int(np.sum(~truth & warning))
    brier = float(np.mean((p - labels) ** 2))
    baseline_brier = float(np.mean((prevalence - labels) ** 2))
    return {
        "samples": len(labels), "positive_windows": int(truth.sum()),
        "brier": brier, "baseline_brier": baseline_brier,
        "brier_skill": 1 - brier / baseline_brier if baseline_brier > 0 else None,
        "pod": hit / (hit + missed) if hit + missed else None,
        "far": false / (hit + false) if hit + false else None,
        "missed_windows": missed, "false_alarm_windows": false,
    }


def calibrate(cal_outputs, test_outputs, policy, classifier_trained,
              cal_event_count, test_event_count):
    """Fit on calibration only; evaluate on disjoint, nonoverlapping test origins.

    Output tuples: (quantiles, logits, log1p targets, joint binary event labels).
    Event counts are counts of independent episodes, supplied by the caller.
    """
    state = default_state()
    report = {"intervals": {}, "bands": [], "notes": [
        "Each band offset uses the 80% order statistic of per-origin maximum "
        "residuals, across observed horizons. Support counts origins, not pooled windows. "
        "Temporal dependence "
        "precludes claiming a rigorous conformal coverage guarantee.",
        "POD and FAR are window metrics; FAR is false alarms / all warnings.",
        "Calibration availability is separate from holdout validation. Fitted "
        "probabilities without holdout skill remain experimental; external evaluation is separate.",
        "A single observed event episode permits research calibration, not a reliable "
        "estimate of performance on independent future events.",
    ]}
    if cal_outputs is None:
        return state, report
    cq, cl, cy, ce = (np.asarray(x) for x in cal_outputs)
    if test_outputs is None:
        test_outputs = tuple(np.empty((0,) + x.shape[1:]) for x in (cq, cl, cy, ce))
    tq, tl, ty, te = (np.asarray(x) for x in test_outputs)
    minimum = policy.get("min_calibration_samples", 10)
    test_minimum = policy.get("min_test_samples", minimum)
    offsets = np.zeros((64, 6))
    observed_cal = np.isfinite(cq[..., 0]) & np.isfinite(cq[..., 2]) & np.isfinite(cy)
    cal_counts = observed_cal.sum(axis=0)
    interval_bands = []
    for a, b in BANDS:
        origin_counts = []
        for target in range(6):
            lo, hi, y = cq[:, a:b, target, 0], cq[:, a:b, target, 2], cy[:, a:b, target]
            observed = observed_cal[:, a:b, target]
            scores = np.maximum(np.maximum(lo - y, y - hi), 0)
            # One score per origin, even when a band has many correlated windows.
            scores = np.max(np.where(observed, scores, -np.inf), axis=1)
            scores = scores[np.isfinite(scores)]
            n = len(scores)
            origin_counts.append(n)
            if n >= minimum:
                rank = min(n, math.ceil((n + 1) * LEVEL))
                offsets[a:b, target] = np.partition(scores, rank - 1)[rank - 1]
        interval_bands.append(dict(start_hour=a / 2, end_hour=b / 2,
                                   calibration_origins=origin_counts,
                                   calibration_windows=observed_cal[:, a:b].sum(axis=(0, 1)).tolist()))
    lower, upper = _bounds(tq, offsets)
    observed = np.isfinite(ty) & np.isfinite(lower) & np.isfinite(upper)
    counts = observed.sum(axis=0)
    covered = (observed & (ty >= lower) & (ty <= upper)).sum(axis=0)
    coverage = np.divide(covered, counts, out=np.zeros((64, 6)), where=counts > 0)
    calibrated = cal_counts >= minimum
    valid = (calibrated & (counts >= test_minimum)
             & (coverage >= policy.get("min_interval_coverage", 0.7)))
    state.update(interval_offsets=offsets.tolist(), interval_calibrated=calibrated.tolist(),
                 interval_valid=valid.tolist())
    report["intervals"] = dict(method="band_max_residual_per_origin", bands=interval_bands,
                               calibration_samples=cal_counts.tolist(),
                               test_samples=counts.tolist(), coverage=coverage.tolist(),
                               calibration_available=calibrated.tolist(),
                               valid=valid.tolist())
    for group in state["probability_groups"]:
        a, b = group["start"], group["end"]
        cm = np.isfinite(cl[:, a:b]) & np.isin(ce[:, a:b], [0, 1])
        tm = np.isfinite(tl[:, a:b]) & np.isin(te[:, a:b], [0, 1])
        c_logits, c_labels = cl[:, a:b][cm], ce[:, a:b][cm]
        t_logits, t_labels = tl[:, a:b][tm], te[:, a:b][tm]
        metrics = dict(start_hour=a / 2, end_hour=b / 2,
                       calibration_origins=int(cm.any(axis=1).sum()),
                       test_origins=int(tm.any(axis=1).sum()),
                       calibration_event_episodes=int(cal_event_count),
                       test_event_episodes=int(test_event_count))
        error = np.abs(np.expm1(tq[:, a:b, :, 1]) - np.expm1(ty[:, a:b]))
        regression = np.isfinite(error)
        metrics["mae_pfu"] = [float(error[..., j][regression[..., j]].mean())
                              if regression[..., j].any() else None for j in range(6)]
        if not classifier_trained:
            group["reason"] = "event_head_untrained"
        elif (metrics["calibration_origins"] < minimum
              or len(np.unique(c_labels)) < 2
              or cal_event_count < policy.get("min_calibration_events", 1)):
            group["reason"] = "insufficient_calibration_events_or_samples"
        else:
            scale, bias = _platt(c_logits, c_labels)
            if not np.isfinite([scale, bias]).all():
                group["reason"] = "calibration_fit_failed"
            else:
                group.update(scale=scale, bias=bias, calibrated=True)
                if (metrics["test_origins"] < test_minimum or len(np.unique(t_labels)) < 2
                        or test_event_count < policy.get("min_test_events", 1)):
                    group["reason"] = "insufficient_test_events_or_samples"
                else:
                    p = _sigmoid(t_logits * scale + bias)
                    metrics.update(_event_metrics(t_labels, p, float(c_labels.mean()),
                                                  policy["risk_threshold"]))
                    group["valid"] = (metrics["brier_skill"] is not None
                                      and metrics["brier_skill"] >= policy.get("min_brier_skill", 0.01))
                    group["reason"] = "validated" if group["valid"] else "brier_not_better_than_prevalence"
        metrics.update(calibrated=group["calibrated"],
                       probability_valid=group["valid"], reason=group["reason"])
        report["bands"].append(metrics)
    return state, report
