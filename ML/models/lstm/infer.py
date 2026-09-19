"""Run: python3 models/lstm/infer.py [--smoke] [--origin 2024-05-10T12:00:00Z]."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

if __package__:
    from .calibration import intervals, probabilities
    from .data_utils import HORIZON, METRICS, STEP, completed_inputs, load_data, targets, transform_features
    from .model import LSTMForecast
    from .train import ROOT, data_path
else:
    from calibration import intervals, probabilities
    from data_utils import HORIZON, METRICS, STEP, completed_inputs, load_data, targets, transform_features
    from model import LSTMForecast
    from train import ROOT, data_path


def utc(value):
    return pd.to_datetime(value, utc=True)


def number(value):
    return float(value) if np.isfinite(value) else None


def forecast(checkpoint_path, data, origin=None, cutoff=None, *,
             model_class=LSTMForecast, model_type="lstm"):
    torch.set_num_threads(2)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if checkpoint.get("format_version") != 1:
        raise ValueError("Unsupported checkpoint format")
    if checkpoint.get("model_type", "lstm") != model_type:
        raise ValueError(f"Checkpoint belongs to {checkpoint.get('model_type', 'lstm')}, "
                         f"but this script runs {model_type}")
    frame, sources = load_data(data)
    now = pd.Timestamp.now(tz="UTC")
    origin = (min(frame.index[-1] + pd.Timedelta(STEP), now.floor(STEP)) if origin is None
              else now.floor(STEP) if origin == "now" else utc(origin))
    cutoff = origin if cutoff is None else utc(cutoff)
    if pd.isna(origin) or pd.isna(cutoff) or origin != origin.floor(STEP) or cutoff > origin:
        raise ValueError("Origin must be on the 30-minute UTC grid; cutoff must be <= origin")
    # An aggregate is only available after the entire half-hour has ended.
    past = completed_inputs(frame, cutoff)
    context = checkpoint["context"]
    grid = pd.date_range(end=origin - pd.Timedelta(STEP), periods=context, freq=STEP)
    # Retain additional past observations for causal filling and age features.
    history = past.reindex(past.index.union(grid)).sort_index()
    inputs = transform_features(history, checkpoint["transformer"])[-context:]
    recent = history.reindex(grid)
    y, _ = targets(recent)
    policy, reasons = checkpoint["policy"], []
    if np.isfinite(y[:, [0, 1, 4, 5]]).all(axis=1).mean() < policy["min_history_fraction"]:
        reasons.append("insufficient_history")
    for column in (0, 1, 4, 5):
        seen = np.flatnonzero(np.isfinite(y[:, column]))
        if not len(seen) or (context - 1 - seen[-1]) * 30 > policy["max_age_minutes"]:
            reasons.append("stale_critical_data")
            break
    blocked = bool(reasons)
    if checkpoint["smoke"]:
        reasons.append("smoke_test_only")
    q = np.full((1, HORIZON, 6, 3), np.nan)
    logits = np.full((1, HORIZON), np.nan)
    if not blocked:
        model = model_class(**checkpoint["model_config"])
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        with torch.no_grad():
            output, raw_logits = model(torch.from_numpy(inputs[None]))
        q, logits = output.numpy(), raw_logits.numpy()
    with np.errstate(over="ignore", invalid="ignore"):
        predicted = np.expm1(q[0, ..., 1])
        lower, upper = intervals(q, checkpoint["calibration"])
        validated_lower, validated_upper = intervals(q, checkpoint["calibration"], validated=True)
        lower, upper = np.expm1(lower[0]), np.expm1(upper[0])
    probability = probabilities(logits, checkpoint["calibration"])[0]
    event_support = np.asarray(checkpoint.get("event_support", np.zeros((HORIZON, 2))))
    probability[~(event_support > 0).all(axis=1)] = np.nan
    support = np.asarray(checkpoint["support"]) > 0
    predicted[~support] = np.nan
    lower[~support], upper[~support] = np.nan, np.nan
    model_version = hashlib.sha256(Path(checkpoint_path).read_bytes()).hexdigest()
    source_refs = [{"path": s["path"], "sha256": s["sha256"]} for s in sources]
    rows = []
    for h in range(HORIZON):
        why = list(reasons)
        if not support[h].all():
            why.append("untrained_horizon_or_target")
        validated = bool(np.isfinite(validated_lower[0, h]).all() and np.isfinite(validated_upper[0, h]).all())
        p = number(probability[h])
        if not validated:
            why.append("unvalidated_intervals")
        if p is None:
            why.append("uncalibrated_probability")
        allowed = None
        if not why and validated and p is not None:
            allowed = p < policy["risk_threshold"]
        row = {"start_utc": (origin + h * pd.Timedelta(STEP)).isoformat(),
               "end_utc": (origin + (h + 1) * pd.Timedelta(STEP)).isoformat()}
        for index, metric in enumerate(METRICS):
            for offset, statistic in enumerate(("mean", "max")):
                j = 2 * index + offset
                row[f"{metric}_{statistic}_pred"] = number(predicted[h, j])
                row[f"{metric}_{statistic}_lower"] = number(lower[h, j])
                row[f"{metric}_{statistic}_upper"] = number(upper[h, j])
        row.update(p_adverse=p, eva_allowed=allowed,
                   reason=sorted(set(why)) if why else ["probability_below_policy_threshold" if allowed
                                                      else "probability_at_or_above_policy_threshold"],
                   source_refs=source_refs)
        rows.append(row)
    return {"forecast_origin_utc": origin.isoformat(), "issued_at_utc": now.isoformat(),
            "model_type": model_type, "context_hours": context / 2,
            "data_cutoff_utc": cutoff.isoformat(), "model_version": model_version,
            "policy_version": policy["version"], "policy": policy,
            "assessment_scope": "space_weather_proxy",
            "label_definition": checkpoint.get("report", {}).get("label_definition", "legacy"), "historical_mode": "retrospective_csv",
            "publication_times_verified": False, "smoke": checkpoint["smoke"],
            "training_data_end_utc": checkpoint["training_data_end_utc"],
            "interval_level": 0.8, "units": {metric: "pfu" for metric in METRICS},
            "sources": sources, "training_sources": checkpoint["sources"],
            "code_sha256": checkpoint["code_sha256"], "versions": checkpoint["versions"],
            "last_observed_window_end_utc": (past.index[-1] + pd.Timedelta(STEP)).isoformat() if len(past) else None,
            "windows": rows}


def main(root=ROOT, model_class=LSTMForecast, model_type="lstm"):
    parser = argparse.ArgumentParser(description=f"Forecast 64 half-hour windows with {model_type.upper()}.")
    parser.add_argument("--data", type=Path, default=data_path(root))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--origin", help="UTC forecast origin; default last completed data window; 'now' for current time")
    parser.add_argument("--cutoff", help="Use only windows ending by this UTC time")
    parser.add_argument("--output", type=Path, default=root / "output")
    parser.add_argument("--smoke", action="store_true", help="Use artifacts/smoke/model.pt")
    args = parser.parse_args()
    checkpoint = args.checkpoint or root / "artifacts" / ("smoke/model.pt" if args.smoke else "model.pt")
    try:
        result = forecast(checkpoint, args.data, args.origin, args.cutoff,
                          model_class=model_class, model_type=model_type)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f"Error: {error}\n")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "forecast.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    with (args.output / "forecast.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result["windows"][0]))
        writer.writeheader()
        for row in result["windows"]:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})
    print(f"Saved 64 windows: {args.output / 'forecast.json'} and forecast.csv")


if __name__ == "__main__":
    main()
