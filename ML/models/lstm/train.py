"""Run: python3 models/lstm/train.py [--smoke]."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

if __package__:
    from .calibration import calibrate, default_state
    from .data_utils import HORIZON, STEP, fit_transformer, load_data, targets, transform_features
    from .model import LSTMForecast, Windows, collect, loss
else:
    from calibration import calibrate, default_state
    from data_utils import HORIZON, STEP, fit_transformer, load_data, targets, transform_features
    from model import LSTMForecast, Windows, collect, loss

ROOT = Path(__file__).resolve().parent


def data_path(root=ROOT):
    for candidate in (root / "data", root.parent / "lstm/data"):
        if any(candidate.rglob("*.csv")):
            return candidate
    return root.parents[1] / "space_weather_model_data/output"


def event_count(labels):
    labels = np.asarray(labels)
    positive = labels[np.isfinite(labels)] == 1  # Unknown gaps cannot establish a new episode.
    if not len(positive):
        return 0
    return int(np.count_nonzero(positive & ~np.r_[False, positive[:-1]]))


def split_origins(y, context):
    """Non-overlapping target periods, with a crossing event kept in one period."""
    active = (y[:, 1] >= 10) | (y[:, 5] >= 1)
    boundaries = [int(len(y) * fraction) for fraction in (0.7, 0.8, 0.9)]
    for i, boundary in enumerate(boundaries):
        while boundary > 0 and active[boundary] and active[boundary - 1]:
            boundary -= 1
        boundaries[i] = boundary
    bounds = [context] + boundaries + [len(y)]
    groups = [np.arange(start, end - HORIZON + 1, 1 if i < 2 else HORIZON)
              for i, (start, end) in enumerate(zip(bounds[:-1], bounds[1:]))]
    if any(len(group) == 0 for group in groups):
        raise ValueError("Archive too short for train/validation/calibration/test and 32h targets. "
                         "Add history to the data directory, or use --smoke for a technical check.")
    return groups, boundaries


def regression_report(outputs, y, origins, model_type="lstm"):
    q, _, target, _ = outputs
    pred = np.expm1(q[..., 1])
    truth = np.expm1(target)
    baseline = y[np.asarray(origins) - 1, None, :]
    result = {}
    for name, start, end in (("0-6h", 0, 12), ("6-12h", 12, 24),
                             ("12-24h", 24, 48), ("24-32h", 48, 64)):
        valid = np.isfinite(truth[:, start:end]) & np.isfinite(baseline)
        def mae(values):
            return float(np.abs(values - truth[:, start:end])[valid].mean()) if valid.any() else None
        estimates = pred[:, start:end]
        naive = np.broadcast_to(baseline, truth[:, start:end].shape)
        adverse = (truth[:, start:end, 1] >= 10) | (truth[:, start:end, 5] >= 1)
        result[name] = {f"{model_type}_mae_pfu": mae(estimates),
                        "persistence_mae_pfu": mae(naive),
                        "compared_values": int(valid.sum())}
        known = np.isfinite(truth[:, start:end, 1]) & np.isfinite(truth[:, start:end, 5])
        for label, selected in (("event", adverse), ("quiet", known & ~adverse)):
            mask = valid & selected[..., None]
            result[name][label] = {
                "values": int(mask.sum()),
                "model_mae_pfu": float(np.abs(estimates - truth[:, start:end])[mask].mean()) if mask.any() else None,
                "persistence_mae_pfu": float(np.abs(naive - truth[:, start:end])[mask].mean()) if mask.any() else None}
    return result


def train(args, model_class=LSTMForecast, model_type="lstm", root=ROOT):
    if (args.epochs < 1 or args.batch_size < 1 or args.hidden_size < 1
            or args.context_hours <= 0 or args.learning_rate <= 0 or args.event_weight < 0):
        raise ValueError("Epochs, batch size, hidden size, context and learning rate must be positive; event weight cannot be negative")
    if args.context_hours * 2 != int(args.context_hours * 2):
        raise ValueError("Context must be a multiple of 0.5 hours")
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    frame, sources = load_data(args.data)
    context = min(12, int(args.context_hours * 2)) if args.smoke else int(args.context_hours * 2)
    y, event = targets(frame)
    if args.smoke:
        groups, boundaries = [np.arange(context, len(frame))], [len(frame)]
    else:
        groups, boundaries = split_origins(y, context)
    # Missing labels do not create fake negative events or fake zero flux.
    groups = [np.asarray([i for i in group if np.isfinite(y[i:i + HORIZON]).any()]) for group in groups]
    if any(len(group) == 0 for group in groups):
        raise ValueError("Not enough windows with observed proton targets")
    transformer = fit_transformer(frame.iloc[:boundaries[0]],
                                  feature_set=args.features, anchors=not args.no_residual)
    x = transform_features(frame, transformer)
    datasets = [Windows(x, y, event, group, context) for group in groups]
    loaders = [DataLoader(dataset, batch_size=args.batch_size, shuffle=i == 0)
               for i, dataset in enumerate(datasets)]
    config = {"input_size": x.shape[1], "hidden_size": args.hidden_size, "layers": 2, "residual": not args.no_residual}
    model = model_class(**config)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    train_labels = event[context:boundaries[0]]
    positives, negatives = int((train_labels == 1).sum()), int((train_labels == 0).sum())
    classifier_trained = positives > 0 and negatives > 0 and not args.smoke and args.event_weight > 0
    pos_weight = min(20, negatives / positives) if classifier_trained else None
    best_loss, best_state, bad_epochs, history = float("inf"), None, 0, []
    epochs = min(args.epochs, 2) if args.smoke else args.epochs
    print(f"Model={model_type}, data={args.data}")
    print(f"Rows={len(frame)}, features={len(transformer['columns'])}, inputs={x.shape[1]}, "
          f"context={context}, windows={[len(d) for d in datasets]}")
    print(f"Event labels: positive={positives}, negative={negatives}; classifier={classifier_trained}")
    for epoch in range(epochs):
        model.train()
        train_loss = 0.0
        for inputs, target, labels in loaders[0]:
            optimizer.zero_grad()
            pred, logits = model(inputs)
            value = loss(pred, logits, target, labels, pos_weight, event_weight=args.event_weight)
            if not torch.isfinite(value):
                raise ValueError("Non-finite training loss; check input data")
            value.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            train_loss += value.item() * len(inputs)
        train_loss /= len(datasets[0])
        val_loss = train_loss
        if not args.smoke:
            model.eval()
            with torch.no_grad():
                val_loss = sum(loss(*model(a), b, c, pos_weight, event_weight=args.event_weight).item() * len(a)
                               for a, b, c in loaders[1]) / len(datasets[1])
        history.append({"epoch": epoch + 1, "train_loss": train_loss, "validation_loss": val_loss})
        print(f"Epoch {epoch + 1}/{epochs}: train={train_loss:.5f}, validation={val_loss:.5f}")
        if val_loss < best_loss:
            best_loss, best_state, bad_epochs = val_loss, copy.deepcopy(model.state_dict()), 0
        else:
            bad_epochs += 1
        if bad_epochs >= 5:
            break
    model.load_state_dict(best_state)
    support = np.zeros((HORIZON, 6), dtype=int)
    event_support = np.zeros((HORIZON, 2), dtype=int)
    for origin in groups[0]:
        available = np.isfinite(y[origin:origin + HORIZON])
        support[:len(available)] += available
        labels = event[origin:origin + HORIZON]
        event_support[:len(labels)] += np.stack((labels == 0, labels == 1), axis=1)
    policy = json.loads(args.policy.read_text())
    calibration, evaluation = default_state(), {}
    if not args.smoke:
        calibration_outputs, test_outputs = collect(model, loaders[2]), collect(model, loaders[3])
        for outputs in (calibration_outputs, test_outputs):
            outputs[1][:, ~(event_support > 0).all(axis=1)] = np.nan
        calibration, evaluation = calibrate(
            calibration_outputs, test_outputs, policy, classifier_trained,
            event_count(event[boundaries[1]:boundaries[2]]), event_count(event[boundaries[2]:]))
        evaluation["regression"] = regression_report(test_outputs, y, groups[3], model_type)
        evaluation["validation_regression"] = regression_report(collect(model, loaders[1]), y, groups[1], model_type)
    # Include every held-out period influencing calibration/validation eligibility.
    data_end = frame.index[-1] + pd.Timedelta(STEP)
    report = {"mode": "smoke" if args.smoke else "archive", "model_type": model_type,
              "context_hours": context / 2, "feature_set": args.features,
              "feature_count": len(transformer["columns"]), "features": transformer["columns"],
              "residual": not args.no_residual, "event_weight": args.event_weight,
              "hyperparameters": {"learning_rate": args.learning_rate, "epochs": args.epochs,
                                  "batch_size": args.batch_size, "hidden_size": args.hidden_size,
                                  "seed": args.seed, "shuffle_train": True},
              "label_definition": "Observed CSV max10 >= 10 or max100 >= 1; missing maxima unknown",
              "best_epoch": min(history, key=lambda row: row["validation_loss"])["epoch"],
              "data_start_utc": frame.index[0].isoformat(), "data_end_utc": data_end.isoformat(),
              "split_boundaries_utc": [frame.index[i].isoformat() for i in boundaries if i < len(frame)],
              "windows": [len(d) for d in datasets], "training_positive_windows": positives,
              "training_negative_windows": negatives, "classifier_trained": classifier_trained,
              "observed_training_event_episodes": event_count(train_labels),
              "history": history, "evaluation": evaluation,
              "limitations": ["CSV publication times, instrument quality and versions are unverified.",
                               "No astronaut dose or orbit model; space-weather proxy only.",
                               "Event labels describe observed maxima; within-window sampling gaps are not certified.",
                               "Late-appearing columns absent in the training period cannot be learned.",
                               "Very few storms; internal scores are not proof of future event skill."]}
    versions = {"torch": str(torch.__version__), "numpy": np.__version__, "pandas": pd.__version__}
    code_hash = hashlib.sha256()
    for directory in sorted({ROOT, root}):
        for path in sorted(directory.glob("*.py")):
            if not path.name.startswith("test_"):
                code_hash.update(f"{directory.name}/{path.name}".encode() + path.read_bytes())
    checkpoint = {"format_version": 1, "model_type": model_type,
                  "model_config": config, "state_dict": best_state,
                  "transformer": transformer, "context": context, "calibration": calibration,
                  "policy": policy, "support": support.tolist(), "smoke": args.smoke,
                  "event_support": event_support.tolist(),
                  "training_data_end_utc": data_end.isoformat(), "sources": sources,
                  "report": report, "versions": versions, "seed": args.seed,
                  "code_sha256": code_hash.hexdigest()}
    args.output.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.output / "model.pt")
    (args.output / "training_report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(f"Saved {args.output / 'model.pt'}")
    if args.smoke:
        print("SMOKE ONLY: no validated intervals, probabilities or EVA decisions.")
    else:
        valid_intervals = np.asarray(calibration["interval_valid"]).sum()
        calibrated_bands = sum(group.get("calibrated", group["valid"]) for group in calibration["probability_groups"])
        valid_bands = sum(group["valid"] for group in calibration["probability_groups"])
        print(f"Calibration: {calibrated_bands}/4 fitted probability bands, {valid_bands}/4 validated; "
              f"{valid_intervals}/384 validated intervals.")
    if not args.smoke and not classifier_trained:
        print("Event probabilities disabled: need positive event weight and both observed label classes.")


def main(root=ROOT, model_class=LSTMForecast, model_type="lstm"):
    parser = argparse.ArgumentParser(description=f"Train {model_type.upper()} from measurement CSVs.")
    parser.add_argument("--data", type=Path, default=data_path(root))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy", type=Path, default=root / "policy.json")
    parser.add_argument("--context-hours", type=float, default=72)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--features", choices=("core", "all", "engineered"),
                        default="all" if model_type == "lstm" else "engineered")
    parser.add_argument("--no-residual", action="store_true", help="Use the original absolute forecast head")
    parser.add_argument("--event-weight", type=float, default=0.1)
    parser.add_argument("--learning-rate", type=float, default=1e-3 if model_type == "lstm" else 3e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--smoke", action="store_true", help="Partial targets on a tiny sample; never validates risk")
    args = parser.parse_args()
    if args.output is None:
        args.output = root / "artifacts" / "smoke" if args.smoke else root / "artifacts"
    try:
        train(args, model_class, model_type, root)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
