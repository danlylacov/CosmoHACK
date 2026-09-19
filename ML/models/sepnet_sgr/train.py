"""Train SEPNET for proton fluxes, X-ray flux and three-hour Kp."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset

from models.lstm.data_utils import HORIZON, STEP, fit_transformer, load_data
from models.lstm.model import collect, loss
from models.sepnet_sgr.calibration import calibrate, default_state
from models.sepnet_sgr.data import TARGETS, decode, encode, inputs, split_origins, targets
from models.sepnet_sgr.fallback import statistics_rows
from models.sepnet_sgr.model import SEPNETSGR
from models.sepnet_sgr.scales import levels, validate_policy


def data_path():
    for folder in (ROOT / 'data', ROOT.parent / 'sepnet/data', ROOT.parent / 'lstm/data'):
        if any(folder.rglob('*.csv')):
            return folder
    return ROOT.parents[1] / 'space_weather_model_data/output'


class Windows(Dataset):
    def __init__(self, x, y, event, origins, context):
        self.x, self.y, self.event = x, encode(y), event
        self.origins, self.context = origins, context

    def __len__(self):
        return len(self.origins)

    def __getitem__(self, index):
        start = self.origins[index]
        end = min(start + HORIZON, len(self.y))
        y = np.full((HORIZON, len(TARGETS)), np.nan, dtype=np.float32)
        events = np.full(HORIZON, np.nan, dtype=np.float32)
        y[:end - start], events[:end - start] = self.y[start:end], self.event[start:end]
        return self.x[start - self.context:start], y, events


def regression_report(outputs, raw, origins):
    predicted, truth = decode(outputs[0][..., 1]), decode(outputs[2])
    origins = np.asarray(origins)
    last = raw[origins - 1].copy()
    last[:, 8] = raw[origins - 6, 8]  # Only the completed Kp interval is known.
    persistence = np.broadcast_to(last[:, None], truth.shape)
    result = {}
    for name, start, end in (('0-6h', 0, 12), ('6-12h', 12, 24), ('12-24h', 24, 48), ('24-32h', 48, 64)):
        band = {}
        for column, metric in enumerate(TARGETS):
            actual, pred, naive = (value[:, start:end, column] for value in (truth, predicted, persistence))
            valid = np.isfinite(actual) & np.isfinite(pred) & np.isfinite(naive)
            band[metric] = {'values': int(valid.sum()),
                'mae': float(np.abs(pred - actual)[valid].mean()) if valid.any() else None,
                'persistence_mae': float(np.abs(naive - actual)[valid].mean()) if valid.any() else None}
        result[name] = band
    return result


def scale_report(outputs, raw, origins):
    predicted = decode(outputs[0][..., 1])
    # Observed categories must not cross thresholds during a float32 log round-trip.
    truth = raw[np.asarray(origins)[:, None] + np.arange(HORIZON)]
    report = {}
    for scale in ("s_level", "g_level", "r_level"):
        matrix = np.zeros((6, 6), dtype=int)
        for pred, actual in zip(predicted.reshape(-1, 9), truth.reshape(-1, 9)):
            estimate = levels(pred[1], pred[8], pred[7])[scale]
            observed = levels(actual[1], actual[8], actual[7])[scale]
            if estimate is not None and observed is not None:
                matrix[observed, estimate] += 1
        total = int(matrix.sum())
        hits, missed, false = int(matrix[1:, 1:].sum()), int(matrix[1:, 0].sum()), int(matrix[0, 1:].sum())
        report[scale] = dict(samples=total, confusion_observed_by_predicted=matrix.tolist(),
            accuracy=float(np.trace(matrix) / total) if total else None,
            pod=hits / (hits + missed) if hits + missed else None,
            far=false / (hits + false) if hits + false else None)
    return report


def train(args):
    if (args.epochs < 1 or args.batch_size < 1 or args.hidden_size < 1 or args.learning_rate <= 0
            or args.event_weight < 0 or args.context_hours <= 0 or args.context_hours * 2 % 1):
        raise ValueError('Invalid training arguments; history must be a positive multiple of 0.5 hours')
    policy = validate_policy(json.loads(args.policy.read_text()))
    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    frame, sources = load_data(args.data)
    y, event = targets(frame)
    context = min(12, int(args.context_hours * 2)) if args.smoke else int(args.context_hours * 2)
    if args.smoke:
        groups, boundaries = [np.arange(context, len(frame))], [len(frame)]
    else:
        groups, boundaries = split_origins(frame, y, context)
    groups = [np.asarray([i for i in group if np.isfinite(y[i:i + HORIZON]).any()]) for group in groups]
    if any(not len(group) for group in groups):
        raise ValueError('Archive too short or has no observed targets. Add history or use --smoke.')
    transformer = fit_transformer(frame.iloc[:boundaries[0]], feature_set=args.features, anchors=False)
    x = inputs(frame, transformer)
    datasets = [Windows(x, y, event, group, context) for group in groups]
    loaders = [DataLoader(dataset, batch_size=args.batch_size, shuffle=i == 0)
               for i, dataset in enumerate(datasets)]
    config = dict(input_size=x.shape[1], hidden_size=args.hidden_size, layers=2)
    model = SEPNETSGR(**config)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    labels = event[context:boundaries[0]]
    positive, negative = int((labels == 1).sum()), int((labels == 0).sum())
    classifier = positive > 0 and negative > 0 and args.event_weight > 0 and not args.smoke
    weight = min(20, negative / positive) if classifier else None
    best, best_state, stale, history = float('inf'), None, 0, []
    epochs = min(args.epochs, 2) if args.smoke else args.epochs
    print(f'SEPNET-SGR: rows={len(frame)}, features={len(transformer["columns"])}, '
          f'context={context}, windows={[len(d) for d in datasets]}')
    print(f'Observed S1/G1/R1 labels: positive={positive}, negative={negative}')
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for batch, target, labels in loaders[0]:
            optimizer.zero_grad()
            value = loss(*model(batch), target, labels, weight, event_weight=args.event_weight)
            if not torch.isfinite(value):
                raise ValueError('Non-finite loss; check inputs')
            value.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            optimizer.step()
            total += value.item() * len(batch)
        train_loss = total / len(datasets[0])
        validation_loss = train_loss
        if not args.smoke:
            model.eval()
            with torch.no_grad():
                validation_loss = sum(loss(*model(a), b, c, weight, event_weight=args.event_weight).item() * len(a)
                                      for a, b, c in loaders[1]) / len(datasets[1])
        history.append(dict(epoch=epoch + 1, train_loss=train_loss, validation_loss=validation_loss))
        print(f'Epoch {epoch + 1}/{epochs}: train={train_loss:.5f}, validation={validation_loss:.5f}')
        if validation_loss < best:
            best, best_state, stale = validation_loss, copy.deepcopy(model.state_dict()), 0
        else:
            stale += 1
        if stale >= 10:
            break
    model.load_state_dict(best_state)
    support = np.zeros((HORIZON, len(TARGETS)), dtype=int)
    event_support = np.zeros((HORIZON, 2), dtype=int)
    for origin in groups[0]:
        observed = np.isfinite(y[origin:origin + HORIZON])
        support[:len(observed)] += observed
        label = event[origin:origin + HORIZON]
        event_support[:len(label)] += np.stack((label == 0, label == 1), axis=1)
    state, evaluation = default_state(), {}
    if not args.smoke:
        calibration_output, test_output = collect(model, loaders[2]), collect(model, loaders[3])
        for values in (calibration_output, test_output):
            values[1][:, ~(event_support > 0).all(axis=1)] = np.nan
        state, evaluation = calibrate(calibration_output, test_output, policy, classifier)
        evaluation['regression'] = regression_report(test_output, y, groups[3])
        evaluation['scale_levels'] = scale_report(test_output, y, groups[3])
        evaluation['validation_regression'] = regression_report(collect(model, loaders[1]), y, groups[1])
    data_end = frame.index[-1] + pd.Timedelta(STEP)
    report = dict(model_type='sepnet_sgr', mode='smoke' if args.smoke else 'archive',
        context_hours=context / 2, feature_set=args.features, feature_count=len(transformer['columns']),
        features=transformer['columns'], targets=TARGETS, data_start_utc=frame.index[0].isoformat(),
        data_end_utc=data_end.isoformat(), training_positive_windows=positive,
        training_negative_windows=negative, classifier_trained=classifier,
        windows=[len(d) for d in datasets],
        split_boundaries_utc=[frame.index[i].isoformat() for i in boundaries if i < len(frame)],
        best_epoch=min(history, key=lambda row: row['validation_loss'])['epoch'], history=history,
        hyperparameters=dict(learning_rate=args.learning_rate, epochs=args.epochs, seed=args.seed,
                             hidden_size=args.hidden_size, batch_size=args.batch_size, event_weight=args.event_weight),
        evaluation=evaluation, limitations=[
            'Threshold estimates from merged CSV observations, not official NOAA alerts.',
            'No astronaut dose, orbit, communications geometry or spacecraft protection model.',
            'Publication times and within-window sampling quality are not known from CSV.',
            'Kp labels require complete consistent three-hour UTC blocks; holdout origins are 33 hours apart.'])
    digest = hashlib.sha256()
    for directory in (ROOT.parent / 'lstm', ROOT.parent / 'sepnet', ROOT):
        for path in sorted(directory.glob('*.py')):
            if not path.name.startswith('test_'):
                digest.update(f'{directory.name}/{path.name}'.encode() + path.read_bytes())
    fallback_statistics = statistics_rows(frame.index[:boundaries[0]].to_pydatetime(), y[:boundaries[0]])
    training_end = frame.index[boundaries[0] - 1] + pd.Timedelta(STEP)
    checkpoint = dict(format_version=1, model_type='sepnet_sgr', model_config=config,
        state_dict=best_state, transformer=transformer, context=context, calibration=state,
        support=support.tolist(), event_support=event_support.tolist(), policy=policy,
        smoke=args.smoke, sources=sources, training_data_end_utc=data_end.isoformat(), report=report,
        fallback_statistics=fallback_statistics,
        code_sha256=digest.hexdigest(), versions=dict(torch=str(torch.__version__), numpy=np.__version__, pandas=pd.__version__))
    args.output.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.output / 'model.pt')
    sidecar = {key: checkpoint[key] for key in ('format_version', 'model_type', 'policy', 'context',
                                               'smoke', 'sources', 'code_sha256', 'versions')}
    sidecar.update(statistics=fallback_statistics, training_data_end_utc=training_end.isoformat())
    (args.output / 'model.fallback.json').write_text(json.dumps(sidecar, allow_nan=False, separators=(',', ':')))
    print(f'Saved {args.output / "model.pt"}')


def main():
    parser = argparse.ArgumentParser(description='Train SEPNET-SGR: protons, X-ray, Kp and NOAA scale inputs.')
    parser.add_argument('--data', type=Path, default=data_path())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--policy', type=Path, default=ROOT / 'policy.json')
    parser.add_argument('--context-hours', type=float, default=72)
    parser.add_argument('--features', choices=('core', 'all', 'engineered'), default='engineered')
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--hidden-size', type=int, default=64)
    parser.add_argument('--learning-rate', type=float, default=3e-4)
    parser.add_argument('--event-weight', type=float, default=0.1)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    args.output = args.output or ROOT / 'artifacts' / ('smoke' if args.smoke else '')
    try:
        train(args)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f'Error: {error}\n')


if __name__ == '__main__':
    main()
