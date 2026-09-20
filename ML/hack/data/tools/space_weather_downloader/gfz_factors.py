"""GFZ high-cadence and daily indices, without filling gaps or backdating availability."""
import json
from datetime import timedelta
from urllib.parse import urlencode

from additional_sources import number, provenance, stamp, timestamp

GFZ_API = 'https://kp.gfz.de/app/json/'
# ap30/ap60 are unitless in the current Hpo V3 format specification.
INDICES = {
    'Hp30': (1800, 'dimensionless', 'geomagnetic_hp30'),
    'ap30': (1800, 'dimensionless', 'geomagnetic_ap30'),
    'Hp60': (3600, 'dimensionless', 'geomagnetic_hp60'),
    'ap60': (3600, 'dimensionless', 'geomagnetic_ap60'),
    'Ap': (86400, '2 nT', 'geomagnetic_daily_ap'),
    'Cp': (86400, 'dimensionless', 'geomagnetic_daily_cp'),
    'C9': (86400, 'dimensionless', 'geomagnetic_daily_c9'),
    'SN': (86400, 'dimensionless', 'sunspot_number'),
    'Fobs': (86400, 'sfu', 'solar_radio_flux_f107_observed'),
    'Fadj': (86400, 'sfu', 'solar_radio_flux_f107_adjusted'),
}


def validate_gfz(data, index):
    if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('datetime', index)):
        raise ValueError('GFZ response lacks datetime/' + index + ' arrays')
    times, values = data['datetime'], data[index]
    if len(times) != len(values):
        raise ValueError('GFZ time/value array lengths differ')
    for key in ('status', index + 'status'):
        if key in data and (not isinstance(data[key], list) or len(data[key]) != len(times)):
            raise ValueError('GFZ status array length differs')
    for t, value in zip(times, values):
        if timestamp(t) is None:
            raise ValueError('GFZ timestamp missing')
        if value is not None and (isinstance(value, bool) or number(value) is None):
            raise ValueError('GFZ nonnumeric index value')


def gfz_validator(index):
    def validate(path):
        validate_gfz(json.loads(path.read_text(encoding='utf-8-sig')), index)
    return validate


def read_gfz(data, index, item, start, end):
    validate_gfz(data, index)
    seconds, unit = INDICES[index][:2]
    for raw_time, raw_value in zip(data['datetime'], data[index]):
        t = timestamp(raw_time)
        if not start <= t < end:
            continue
        value = number(raw_value)
        if value is not None and value < 0:
            value = None  # GFZ missing sentinel -1; these indices cannot be negative.
        quality = {'interval_seconds':seconds,'interval_end_utc':stamp(t + timedelta(seconds=seconds))}
        yield (stamp(t), 'gfz_indices', index, value, unit, json.dumps(quality, ensure_ascii=False), *provenance(item))


def run_gfz(fetch, observations, args, start, end, now, error):
    from download_space_weather import grid_coverage
    indices = []
    if 'hpo' in args.sources:
        indices.extend(('Hp30', 'ap30', 'Hp60', 'ap60'))
    if 'solar-indices' in args.sources:
        indices.extend(('Ap', 'Cp', 'C9', 'SN', 'Fobs', 'Fadj'))
    coverage = {}
    for index in indices:
        cursor = start
        while cursor < end:
            stop = min(cursor + timedelta(days=30), end)
            url = GFZ_API + '?' + urlencode({'start': stamp(cursor), 'end': stamp(stop-timedelta(seconds=1)), 'index': index})
            try:
                item = fetch.get(url, 'json', ttl=1800 if stop > now-timedelta(days=3) else 86400*30,
                                 validator=gfz_validator(index))
                data = json.loads(fetch.text(item))
                observations.add('measurements', read_gfz(data,index,item,start,end))
            except Exception as exc:
                error(url, exc)
            cursor = stop
        rows = observations.times('measurements', provider='gfz_indices', quantity=index)
        check = grid_coverage(rows, start, end, INDICES[index][0])
        coverage['gfz:' + index] = {**check, 'status': 'partial' if check['available'] < check['expected'] else 'complete',
            'note': 'Coverage counts non-null indices, not original publication availability. No forward-fill; current intervals may be preliminary or absent.'}
    return coverage
