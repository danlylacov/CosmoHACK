"""Forecast physical S/G/R inputs and apply a configurable research EVA policy."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))

from datetime import datetime, timedelta, timezone
import math
import random

from models.sepnet_sgr.fallback import TARGETS, OUTPUTS, csv_statistics, parse_time, floor_time
from models.sepnet_sgr.scales import SOURCES, decision, levels, validate_policy


def _model_forecast(checkpoint_path, data, origin=None, cutoff=None, policy_path=None):
    # Import inside the guarded call: fallback also works without ML dependencies.
    import numpy as np
    import pandas as pd
    import torch
    from models.lstm.data_utils import HORIZON, STEP, completed_inputs, load_data
    from models.lstm.infer import number, utc
    from models.sepnet_sgr.calibration import intervals, probabilities
    from models.sepnet_sgr.data import decode, inputs, targets
    from models.sepnet_sgr.model import SEPNETSGR

    def observed_end(frame):
        raw, _ = targets(frame)
        observed = frame.index[np.isfinite(raw).any(axis=1)]
        return observed[-1] + pd.Timedelta(STEP) if len(observed) else None

    torch.set_num_threads(2)
    checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    if checkpoint.get('format_version') != 1 or checkpoint.get('model_type') != 'sepnet_sgr':
        raise ValueError('Expected a SEPNET-SGR checkpoint; train this model first')
    policy = validate_policy(json.loads(Path(policy_path).read_text()) if policy_path else checkpoint['policy'])
    frame, sources = load_data(data)
    now = pd.Timestamp.now(tz='UTC')
    origin = (min(frame.index[-1] + pd.Timedelta(STEP), now.floor(STEP)) if origin is None else
              now.floor(STEP) if origin == 'now' else utc(origin))
    cutoff = origin if cutoff is None else utc(cutoff)
    if pd.isna(origin) or pd.isna(cutoff) or origin != origin.floor(STEP) or cutoff > origin:
        raise ValueError('Origin must be on the 30-minute UTC grid; cutoff must be <= origin')
    available_end = observed_end(completed_inputs(frame, origin))
    if available_end is None:
        raise ValueError('No completed target measurements at or before the requested origin. '
                         'Choose a later origin or provide earlier history.')
    # Keep the learned 32-hour horizon anchored to actual observations.
    origin = min(origin, available_end)
    cutoff = min(cutoff, origin)
    past = completed_inputs(frame, cutoff)
    last_observed = observed_end(past)
    if last_observed is None:
        raise ValueError('No completed target measurements at or before the data cutoff. '
                         'Choose a later cutoff or provide earlier history.')
    context = checkpoint['context']
    grid = pd.date_range(end=origin - pd.Timedelta(STEP), periods=context, freq=STEP)
    padding = checkpoint["transformer"]["max_age"] + 48
    history_grid = pd.date_range(grid[0] - padding * pd.Timedelta(STEP), grid[-1], freq=STEP)
    history = past.reindex(history_grid)
    x = inputs(history, checkpoint['transformer'])[-context:]
    raw, _ = targets(history)
    recent = raw[-context:]
    available = np.isfinite(recent).mean(0) >= policy.get('min_history_fraction', 0.8)
    for column in range(len(TARGETS)):
        seen = np.flatnonzero(np.isfinite(recent[:, column]))
        max_age = policy.get('kp_max_age_minutes', 240) if column == 8 else policy.get('max_age_minutes', 60)
        if not len(seen) or (context - 1 - seen[-1]) * 30 > max_age:
            available[column] = False
    model = SEPNETSGR(**checkpoint['model_config'])
    model.load_state_dict(checkpoint['state_dict'])
    model.eval()
    with torch.no_grad():
        q, logits = (value.numpy() for value in model(torch.from_numpy(x[None])))
    if not np.isfinite(q).all() or not np.isfinite(logits).all():
        raise FloatingPointError('Model returned non-finite predictions')
    fitted_lower, fitted_upper = intervals(q, checkpoint['calibration'])
    predicted, lower, upper = decode(q[0, ..., 1]), decode(fitted_lower[0]), decode(fitted_upper[0])
    if (not np.isfinite(predicted).all()
            or not np.isfinite(lower[np.isfinite(fitted_lower[0])]).all()
            or not np.isfinite(upper[np.isfinite(fitted_upper[0])]).all()):
        raise FloatingPointError('Decoded physical predictions are not finite')
    supported = (np.asarray(checkpoint['support']) > 0) & available[None]
    for values in (predicted, lower, upper):
        values[~supported] = np.nan
    p = probabilities(logits, checkpoint['calibration'])[0]
    event_support = np.asarray(checkpoint['event_support'])
    p[~(event_support > 0).all(1)] = np.nan
    if not available[[1, 7, 8]].all():
        p[:] = np.nan
    rows = []
    source_refs = [{key: source[key] for key in ('path', 'sha256')} for source in sources]
    for h in range(HORIZON):
        start = origin + h * pd.Timedelta(STEP)
        scales = levels(predicted[h, 1], predicted[h, 8], predicted[h, 7])
        allowed, reasons = decision(scales, policy)
        if checkpoint['smoke']:
            allowed, reasons = None, reasons + ['smoke_test_only']
        row = dict(start_utc=start.isoformat(), end_utc=(start + pd.Timedelta(STEP)).isoformat(),
                   kp_interval_start_utc=start.floor('3h').isoformat(),
                   kp_interval_end_utc=(start.floor('3h') + pd.Timedelta(hours=3)).isoformat())
        for j, metric in enumerate(OUTPUTS):
            row.update({f'{metric}_pred': number(predicted[h, j]),
                        f'{metric}_lower': number(lower[h, j]), f'{metric}_upper': number(upper[h, j])})
        row.update(scales)
        for suffix, values in (('lower', lower), ('upper', upper)):
            row.update({f'{name}_{suffix}': value for name, value in
                        levels(values[h, 1], values[h, 8], values[h, 7]).items()})
        row.update(p_adverse=number(p[h]), eva_allowed=allowed,
                   reason=reasons or ['scales_below_policy_thresholds'], source_refs=source_refs)
        rows.append(row)
    return dict(model_type='sepnet_sgr', forecast_origin_utc=origin.isoformat(), issued_at_utc=now.isoformat(),
        data_cutoff_utc=cutoff.isoformat(), context_hours=context / 2, windows=rows,
        model_version=hashlib.sha256(Path(checkpoint_path).read_bytes()).hexdigest(),
        policy_version=policy['version'], policy=policy, policy_overridden=policy_path is not None,
        assessment_scope='space_weather_proxy', scale_method='estimated_noaa_threshold_categories',
        official_noaa_alert=False, decision_basis='point_forecast_s_g_r_levels',
        probability_definition='P(S>=1 or G>=1 or R>=1); independent of configured EVA block levels',
        interval_level=0.8, scale_sources=SOURCES, smoke=checkpoint['smoke'],
        historical_mode='retrospective_csv', publication_times_verified=False,
        training_data_end_utc=checkpoint['training_data_end_utc'],
        units={**{name: 'pfu' for name in OUTPUTS[:6]}, **{name: 'W/m2' for name in OUTPUTS[6:8]},
               'geomagnetic_kp': 'Kp (3-hour UTC index)'},
        sources=sources, training_sources=checkpoint['sources'], code_sha256=checkpoint['code_sha256'],
        versions=checkpoint['versions'], unavailable_input_targets=[TARGETS[j] for j in range(9) if not available[j]],
        last_observed_window_end_utc=last_observed.isoformat())


def _json_file(path):
    try:
        value = json.loads(Path(path).read_text())
        json.dumps(value, allow_nan=False)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return None


def _time(value, default):
    try:
        return parse_time(value) if value is not None and value != 'now' else default
    except Exception:
        return default


def _finite(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _reference(checkpoint_path, reference_path):
    reference = _json_file(reference_path)
    if reference.get('model_type') == 'sepnet_sgr':
        reference['_path'] = str(reference_path)
        return reference
    try:
        import torch
        state = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
        if state.get('model_type') == 'sepnet_sgr':
            reference = {key: state.get(key) for key in
                         ('policy', 'context', 'smoke', 'sources', 'code_sha256', 'versions')}
            boundaries = state.get('report', {}).get('split_boundaries_utc', [])
            reference.update(_path=str(checkpoint_path), statistics=state.get('fallback_statistics', {}),
                             training_data_end_utc=boundaries[0] if boundaries else state.get('training_data_end_utc'))
            return reference
    except Exception:
        pass
    return {}


def _fallback_forecast(checkpoint_path, data, origin, cutoff, policy_path, error):
    """Last observations plus N(0, .02 * variance), independent of ML libraries."""
    now = datetime.now(timezone.utc)
    origin = floor_time(_time(origin, now))
    cutoff = min(_time(cutoff, origin), origin)
    try:
        reference_path = Path(checkpoint_path).with_suffix('.fallback.json')
    except Exception:
        reference_path = None
    reference = _reference(checkpoint_path, reference_path)
    policy, overridden = None, False
    for candidate, override in ((_json_file(policy_path), True), (reference.get('policy'), False),
                                (_json_file(ROOT / 'policy.json'), False),
                                ({'version': 'fallback-default', 'block_s': 1, 'block_g': 1, 'block_r': 1}, False)):
        try:
            if not isinstance(candidate, dict) or not candidate:
                continue
            policy = validate_policy(candidate)
            policy.setdefault('version', 'fallback-policy')
            overridden = override
            break
        except Exception:
            continue

    available_end = None
    try:
        requested = origin
        origin_stats = csv_statistics(data, origin)
        available_end = _time(origin_stats.get('data_end_utc'), None)
        if available_end is not None:
            origin = min(origin, available_end)
            cutoff = min(cutoff, origin)
        stats = origin_stats if cutoff == requested else csv_statistics(data, cutoff)
    except Exception:
        stats = {}
    # A sidecar survives a corrupt checkpoint or unavailable ML dependencies.
    saved = reference.get('statistics', {})
    training_stats = {}
    if any(value is None for value in stats.get('last', [None] * 9)):
        try:
            training_cutoff = min(cutoff, parse_time(reference['training_data_end_utc']))
            paths = [source['path'] for source in reference['sources']]
            training_stats = csv_statistics(paths, training_cutoff)
        except Exception:
            pass
    estimates, variances, ends = [], [], []
    used_reference = used_training = False
    for j in range(9):
        value, variance, end = None, 0.0, None
        for source in (stats, saved, training_stats):
            try:
                candidate = _finite(source['last'][j])
                stamp = parse_time(source['observed_end'][j])
                if candidate is None or stamp > cutoff or (j == 8 and candidate > 9):
                    continue
                value, variance, end = candidate, _finite(source['variance'][j]) or 0.0, stamp
                used_reference |= source is saved
                used_training |= source is training_stats
                break
            except Exception:
                continue
        estimates.append(value)
        variances.append(variance)
        ends.append(end)
    last_observed = max((stamp for stamp in ends if stamp is not None), default=None)
    forecast_end = available_end or last_observed
    if forecast_end is not None:
        origin = min(origin, forecast_end)
        cutoff = min(cutoff, origin)
    sources = stats.get('sources', [])
    if used_reference:
        storage = Path(reference['_path'])
        sources = sources + [{'path': str(storage.resolve()), 'sha256': _digest(storage)}]
    if used_training:
        sources = sources + training_stats.get('sources', [])
    source_refs = [{key: source.get(key) for key in ('path', 'sha256')} for source in sources]
    rng = random.Random(42)
    kp_blocks, rows = {}, []
    smoke = reference.get('smoke') is True
    for h in range(64):
        start = origin + timedelta(minutes=30 * h)
        kp_start = floor_time(start, 180)
        values = []
        for j, last in enumerate(estimates):
            if last is None:
                values.append(None)
                continue
            if j != 8 or kp_start not in kp_blocks:
                # The Gaussian is centered before physical clipping.
                value = max(0.0, last + rng.gauss(0.0, math.sqrt(0.02 * variances[j])))
                if j == 8:
                    kp_blocks[kp_start] = min(9.0, value)
            values.append(kp_blocks[kp_start] if j == 8 else value)
        for j in range(0, 8, 2):
            if values[j] is not None and values[j + 1] is not None:
                values[j + 1] = max(values[j], values[j + 1])
        scales = levels(values[1], values[8], values[7])
        allowed, reasons = decision(scales, policy)
        reasons = ['fallback_last_observation_noise', f'inference_error_{type(error).__name__}'] + reasons
        if last_observed is None:
            reasons.append('fallback_no_observations')
        if smoke:
            allowed = None
            reasons.append('smoke_test_only')
        row = dict(start_utc=start.isoformat(), end_utc=(start + timedelta(minutes=30)).isoformat(),
                   kp_interval_start_utc=kp_start.isoformat(),
                   kp_interval_end_utc=(kp_start + timedelta(hours=3)).isoformat())
        for metric, value in zip(OUTPUTS, values):
            row.update({f'{metric}_pred': value, f'{metric}_lower': None, f'{metric}_upper': None})
        row.update(scales)
        for suffix in ('lower', 'upper'):
            row.update({f'{scale}_level_{suffix}': None for scale in 'sgr'})
        row.update(p_adverse=None, eva_allowed=allowed, reason=reasons, source_refs=source_refs)
        rows.append(row)
    return dict(model_type='sepnet_sgr', forecast_origin_utc=origin.isoformat(), issued_at_utc=now.isoformat(),
        data_cutoff_utc=cutoff.isoformat(), context_hours=(_finite(reference.get('context')) or 144) / 2, windows=rows,
        model_version=_digest(checkpoint_path), policy_version=policy['version'], policy=policy,
        policy_overridden=overridden, assessment_scope='space_weather_proxy',
        scale_method='estimated_noaa_threshold_categories', official_noaa_alert=False,
        decision_basis='fallback_last_observation_noise_s_g_r_levels',
        probability_definition='P(S>=1 or G>=1 or R>=1); independent of configured EVA block levels',
        interval_level=None, scale_sources=SOURCES, smoke=smoke, historical_mode='retrospective_csv',
        publication_times_verified=False, training_data_end_utc=reference.get('training_data_end_utc'),
        units={**{name: 'pfu' for name in OUTPUTS[:6]}, **{name: 'W/m2' for name in OUTPUTS[6:8]},
               'geomagnetic_kp': 'Kp (3-hour UTC index)'},
        sources=sources, training_sources=reference.get('sources', []),
        code_sha256=reference.get('code_sha256'), versions=reference.get('versions', {}),
        unavailable_input_targets=[TARGETS[j] for j, value in enumerate(estimates) if value is None],
        last_observed_window_end_utc=last_observed.isoformat() if last_observed is not None else None)


def forecast(checkpoint_path, data, origin=None, cutoff=None, policy_path=None):
    try:
        result = _model_forecast(checkpoint_path, data, origin, cutoff, policy_path)
        json.dumps(result, allow_nan=False)
        return result
    except Exception as error:
        print(f'Inference fallback ({type(error).__name__}): {error}', file=sys.stderr)
        return fallback_result(checkpoint_path, data, origin, cutoff, policy_path, error)


def fallback_result(checkpoint_path, data, origin, cutoff, policy_path, error):
    try:
        result = _fallback_forecast(checkpoint_path, data, origin, cutoff, policy_path, error)
        json.dumps(result, allow_nan=False)
        return result
    except Exception as fallback_error:
        print(f'Fallback data unavailable ({type(fallback_error).__name__}): {fallback_error}', file=sys.stderr)
        return _fallback_forecast(None, None, None, None, None, fallback_error)


def main():
    parser = argparse.ArgumentParser(description='Forecast 64 windows with SEPNET-SGR and S/G/R EVA rules.')
    parser.add_argument('--checkpoint', type=Path, required=True, help='Path to a SEPNET-SGR model.pt')
    parser.add_argument('--origin', required=True,
                        help='Forecast start on a half-hour boundary (ISO datetime; UTC if no timezone), or now')
    parser.add_argument('--data', type=Path, required=True, help='Measurement CSV file or directory of CSV files')
    parser.add_argument('--cutoff', help='Only measurements/indices completed by this UTC time')
    parser.add_argument('--policy', type=Path, help='Override research EVA block levels without retraining')
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    args = parser.parse_args()
    result = forecast(args.checkpoint, args.data, args.origin, args.cutoff, args.policy)
    requested = floor_time(_time(args.origin, parse_time(result['issued_at_utc'])))
    if parse_time(result['forecast_origin_utc']) < requested:
        print(f'Requested start {requested.isoformat()} is beyond available observations; '
              f'forecast starts at {result["forecast_origin_utc"]}.', file=sys.stderr)
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'forecast.json').write_text(json.dumps(result, indent=2, allow_nan=False))
        with (args.output / 'forecast.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result['windows'][0]))
            writer.writeheader()
            for row in result['windows']:
                writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value
                                 for key, value in row.items()})
        print(f'Saved 64 windows: {args.output / "forecast.json"} and forecast.csv')
    except Exception as error:
        print(f'Cannot save forecast ({type(error).__name__}): {error}; returning fallback JSON to stdout.',
              file=sys.stderr)
        result = fallback_result(args.checkpoint, args.data, args.origin, args.cutoff, args.policy, error)
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
