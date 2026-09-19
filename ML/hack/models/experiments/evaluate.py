"""Re-evaluate saved experiments and write a compact, reproducible comparison."""
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.lstm.calibration import calibrate
from models.lstm.data_utils import load_data, targets, transform_features
from models.lstm.model import LSTMForecast, Windows, collect
from models.lstm.train import event_count, regression_report, split_origins
from models.sepnet.model import SEPNETForecast


def evaluate(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    family = checkpoint["model_type"]
    directory = os.path.commonpath([str(Path(item["path"]).parent) for item in checkpoint["sources"]])
    frame, sources = load_data(directory)
    if sources != checkpoint["sources"]:
        raise ValueError("Input archive has changed since this experiment")
    y, events = targets(frame)
    origins, boundaries = split_origins(y, checkpoint["context"])
    origins = [np.asarray([i for i in group if np.isfinite(y[i:i + 64]).any()]) for group in origins]
    x = transform_features(frame, checkpoint["transformer"])
    model = (LSTMForecast if family == "lstm" else SEPNETForecast)(**checkpoint["model_config"])
    model.load_state_dict(checkpoint["state_dict"])
    outputs = [collect(model, DataLoader(Windows(x, y, events, group, checkpoint["context"]),
                                        batch_size=64)) for group in origins[1:]]
    for values in outputs:
        values[1][:, ~(np.asarray(checkpoint["event_support"]) > 0).all(axis=1)] = np.nan
    policy = json.loads((ROOT / "models" / family / "policy.json").read_text())
    calibration, evaluation = calibrate(outputs[1], outputs[2], policy,
        checkpoint["report"]["classifier_trained"],
        event_count(events[boundaries[1]:boundaries[2]]), event_count(events[boundaries[2]:]))
    evaluation["validation_regression"] = regression_report(outputs[0], y, origins[1], family)
    evaluation["regression"] = regression_report(outputs[2], y, origins[3], family)
    checkpoint["policy"], checkpoint["calibration"] = policy, calibration
    checkpoint["report"]["evaluation"] = evaluation
    digest = hashlib.sha256()
    for directory in sorted({ROOT / "models/lstm", ROOT / "models" / family}):
        for source in sorted(directory.glob("*.py")):
            if not source.name.startswith("test_"):
                digest.update(f"{directory.name}/{source.name}".encode() + source.read_bytes())
    checkpoint["training_code_sha256"] = checkpoint.get("training_code_sha256", checkpoint["code_sha256"])
    checkpoint["code_sha256"] = digest.hexdigest()
    checkpoint["report"]["recalibration_code_sha256"] = digest.hexdigest()
    torch.save(checkpoint, path)
    (path.parent / "training_report.json").write_text(json.dumps(checkpoint["report"], indent=2, allow_nan=False))
    report = checkpoint["report"]
    return {"model": family, "variant": path.parent.name, "features": report["feature_count"],
            "best_epoch": report["best_epoch"],
            "validation_loss": min(row["validation_loss"] for row in report["history"]),
            "validation_mae": evaluation["validation_regression"],
            "test_mae": evaluation["regression"], "probabilities": evaluation["bands"],
            "validated_intervals": int(np.asarray(calibration["interval_valid"]).sum()),
            "checkpoint": str(path.relative_to(ROOT))}


def main():
    torch.set_num_threads(2)
    results = []
    for path in sorted((ROOT / "models").glob("*/artifacts/experiments/*/model.pt")):
        result = evaluate(path)
        results.append(result)
        print(result["model"], result["variant"], result["validation_loss"], flush=True)
    (ROOT / "models/experiments/results.json").write_text(json.dumps(results, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
