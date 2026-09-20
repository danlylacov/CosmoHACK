"""Minimal streaming JSON consumed by space_weather_model_data."""
import json
import math
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

METRICS = {'B_magnitude': 'magnetic_field_magnitude', 'bt': 'magnetic_field_magnitude', 'Bx_GSE': 'magnetic_field_x_gse', 'By_GSM': 'magnetic_field_y_gsm', 'Bz_GSM': 'magnetic_field_z_gsm', 'bx_gsm': 'magnetic_field_x_gsm', 'by_gsm': 'magnetic_field_y_gsm', 'bz_gsm': 'magnetic_field_z_gsm', 'speed': 'solar_wind_speed', 'proton_speed': 'solar_wind_speed', 'density': 'solar_wind_proton_density', 'proton_density': 'solar_wind_proton_density', 'temperature': 'solar_wind_proton_temperature', 'proton_temperature': 'solar_wind_proton_temperature', 'dynamic_pressure': 'solar_wind_dynamic_pressure', 'AE': 'auroral_electrojet_ae', 'AL': 'auroral_electrojet_al', 'AU': 'auroral_electrojet_au', 'SYM_H': 'symmetric_ring_current_sym_h', 'xrsa_flux': 'solar_xray_flux_short', 'xrsb_flux': 'solar_xray_flux_long'}
for wave in ('256','284','304','1175','1216','1335','1405'):
    METRICS['irr_' + wave] = 'solar_euv_irradiance_' + wave + '_angstrom'
METRICS.update(MgII_EXIS='solar_mgii_index_exis', MgII_standard='solar_mgii_index_standard')


def write_json(stream, value):
    """Compact JSON, streaming sample generators without rounding values."""
    if isinstance(value, dict):
        stream.write('{')
        for i, (key, item) in enumerate(value.items()):
            if i: stream.write(',')
            stream.write(json.dumps(key, ensure_ascii=False) + ':')
            write_json(stream, item)
        stream.write('}')
    elif isinstance(value, (list, tuple, Iterator)):
        stream.write('[')
        for i, item in enumerate(value):
            if i: stream.write(',')
            write_json(stream, item)
        stream.write(']')
    else:
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            write_json(stream, value)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def compact_sample(sample):
    point = {'time_utc':sample['time_utc'], 'value':sample['value']}
    quality = sample.get('quality')
    if isinstance(quality, dict):
        interval = {key:quality[key] for key in ('interval_seconds', 'interval_end_utc') if key in quality}
        if interval:
            point['quality'] = interval
    return point


def result_data(observations):
    def samples(records, column='value'):
        for row in sorted(records, key=lambda r:(r['time'],r['raw_sha256'])):
            point = {'time_utc':row['time'], 'value':row[column]}
            if column == 'value' and row['quality']:
                point['quality'] = json.loads(row['quality'])
            yield compact_sample(point)

    def series():
        # Keep original series boundaries: the converter gives each series equal weight.
        for (provider, role), records in observations.groups('particles','provider','role'):
            channels = ['E2_0'] if provider == 'swpc_electrons' else [f'P{e}' for e in (1,5,10,30,50,60,100,500)]
            if provider == 'iswa': channels.append('E2_0')
            for channel in channels:
                metric = 'electron_flux_above_2_mev' if channel == 'E2_0' else f'proton_flux_above_{channel[1:]}_mev'
                yield {'metric':metric, 'samples':samples(records, channel)}
        for (provider,), records in observations.groups('kp','provider'):
            for column in ('kp','ap'):
                yield {'metric':'geomagnetic_' + column, 'samples':samples(records,column)}
        from gfz_factors import INDICES
        for (provider,quantity,unit), records in observations.groups('measurements','provider','quantity','unit'):
            metric = INDICES[quantity][2] if provider == 'gfz_indices' and quantity in INDICES else METRICS.get(quantity,quantity)
            yield {'metric':metric, 'samples':samples(records)}

    return {'series':series()}


def export_chunks(destination, chunks, start, end):
    """Stream disjoint UTC chunks into one observations-only result."""
    def series():
        for data in chunks:
            yield from data['series']
    atomic_json(destination, {'period':{'start_utc':start,'available_until_exclusive_utc':end},
                              'series':series()})
