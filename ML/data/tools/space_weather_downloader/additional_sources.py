"""Additional public observations and event products, retaining immutable provenance.

No event timestamp is silently treated as a publication timestamp. Archive
observations are retrospective products, not evidence of historical availability.
"""
from __future__ import annotations
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

UTC = timezone.utc
SWPC = 'https://services.swpc.noaa.gov/'
OMNI = 'https://spdf.gsfc.nasa.gov/pub/data/omni/high_res_omni/'


def stamp(t):
    return t.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def timestamp(value):
    if not value:
        return None
    t = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def number(value, fill=None):
    try:
        n = float(value)
        return n if math.isfinite(n) and (fill is None or n != fill) else None
    except (ValueError, TypeError):
        return None


def provenance(item):
    return item['url'], item['sha256'], item['retrieved_at']


# OMNI's documented 49-column five-minute ASCII format (zero-based indices).
OMNI_COLUMNS = {
    13: ('B_magnitude', 'nT', 9999.99), 14: ('Bx_GSE', 'nT', 9999.99),
    17: ('By_GSM', 'nT', 9999.99), 18: ('Bz_GSM', 'nT', 9999.99),
    21: ('speed', 'km/s', 99999.9), 25: ('density', 'cm^-3', 999.99),
    26: ('temperature', 'K', 9999999.), 27: ('dynamic_pressure', 'nPa', 99.99),
    37: ('AE', 'nT', 99999.), 38: ('AL', 'nT', 99999.),
    39: ('AU', 'nT', 99999.), 41: ('SYM_H', 'nT', 99999.),
}


def read_omni(lines, item, start, end):
    """Stream rows; preserve fill values as NULL and mark retrospective provenance."""
    for line in lines:
        if not line.strip() or line.startswith('#'):
            continue
        values = line.split()
        if len(values) != 49:
            raise ValueError(f'Unexpected OMNI column count: {len(values)} (expected 49)')
        year, doy, hour, minute = map(int, values[:4])
        t = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=doy - 1, hours=hour, minutes=minute)
        if not start <= t < end:
            continue
        for index, (name, unit, fill) in OMNI_COLUMNS.items():
            value = number(values[index], fill)
            quality = None
            yield (stamp(t), 'omni_5min', name, value, unit, quality, *provenance(item))


def read_solar_wind(data, product, item, start, end):
    columns = {'mag': {'bx_gsm':'nT', 'by_gsm':'nT', 'bz_gsm':'nT', 'bt':'nT'},
               'wind': {'proton_density':'cm^-3', 'proton_speed':'km/s', 'proton_temperature':'K'}}[product]
    if not isinstance(data, list):
        raise ValueError('Expected SWPC RTSW array')
    for record in data:
        if not isinstance(record, dict) or 'time_tag' not in record or not set(columns).issubset(record):
            raise ValueError('Unexpected SWPC RTSW record')
        t = timestamp(record['time_tag'])
        if start <= t < end:
            quality = None
            provider = 'swpc_solar_wind:' + str(record.get('source', 'unknown'))
            for name, unit in columns.items():
                yield (stamp(t), provider, name, number(record[name]), unit, quality, *provenance(item))


def run(fetch, observations, args, start, end, now, error):
    selected = args.sources
    coverage = {}
    def handle(url, kind, parser, collection, ttl=86400, candidates=None, validator=None):
        try:
            item = fetch.get_candidates(candidates,kind,ttl,validator) if candidates else fetch.get(url, kind, ttl,validator=validator)
            observations.add(collection, parser(item))
            return True
        except Exception as exc:
            error(url, exc)
            return False
    if 'omni' in selected:
        for year in range(start.year, (end - timedelta(microseconds=1)).year + 1):
            url = OMNI + f'omni_5min{year}.asc'
            def parse(item):
                if 'path' not in item:
                    raise ValueError(item.get('error', item['status']))
                with (fetch.root / item['path']).open() as lines:
                    yield from read_omni(lines, item, start, end)
            def validate_omni(path):
                with path.open(encoding='utf-8-sig') as lines:
                    for _ in read_omni(lines, {'url':url,'sha256':'validation','retrieved_at':stamp(now)}, start, end):
                        pass
            handle(url, 'text', parse, 'measurements', 86400 if year == now.year else 86400 * 30,
                   validator=validate_omni)
        coverage['omni'] = {'note': 'Retrospective five-minute, time-shifted to Earth bow shock. Revisions possible; not historical publication evidence.'}
    if 'solar-wind' in selected:
        from source_health import json_records
        if end > now - timedelta(days=7):
            for product in ('mag', 'wind'):
                url = SWPC + f'json/rtsw/rtsw_{product}_1m.json'
                fields=['time_tag','source'] + (['bx_gsm','by_gsm','bz_gsm','bt'] if product=='mag' else ['proton_speed','proton_density','proton_temperature'])
                handle(url, 'json', lambda item, p=product: read_solar_wind(json.loads(fetch.text(item)), p, item, start, end),
                       'measurements',300,validator=json_records(fields))
        coverage['solar-wind'] = {'status': 'not_applicable' if end <= now - timedelta(days=7) else 'rolling_window', 'note': 'Rolling RTSW operational feed; finite retention is not guaranteed. Spacecraft remain separate. Historical OMNI has different time semantics.'}
    if 'xrs' in selected:
        coverage.update(download_xrs(fetch, observations, args, start, end, error))
    for (provider, quantity), records in observations.groups('measurements', 'provider', 'quantity'):
        times = [r['time'] for r in records]
        coverage[provider + ':' + quantity] = {'rows':len(records), 'non_null':sum(r['value'] is not None for r in records), 'first':min(times), 'last':max(times),
            'note':'Counts do not certify instrument quality; gaps are not interpolated.'}
    if 'omni' in selected:
        from download_space_weather import grid_coverage
        for quantity, _, _ in OMNI_COLUMNS.values():
            rows = observations.times('measurements', provider='omni_5min', quantity=quantity)
            coverage.setdefault('omni_5min:' + quantity, {}).update(grid_coverage(rows, start, end, 300))
    if 'solar-wind' in selected and end > now - timedelta(days=7):
        from download_space_weather import grid_coverage
        for quantity in ('bx_gsm', 'by_gsm', 'bz_gsm', 'bt', 'proton_density', 'proton_speed', 'proton_temperature'):
            rows = observations.times('measurements', prefix='swpc_solar_wind:', quantity=quantity, minute=True)
            coverage['solar-wind:' + quantity] = {**grid_coverage(rows, start, end, 60),
                'note': 'UTC minute bins, union of available spacecraft; raw timestamps preserved. Check flags/active status. Feed retention does not cover arbitrary history.'}
    if 'xrs' in selected:
        from download_space_weather import grid_coverage
        for quantity in ('xrsa_flux', 'xrsb_flux'):
            rows = observations.times('measurements', prefix='goes_xrs:', quantity=quantity)
            coverage['xrs:' + quantity] = {**grid_coverage(rows, start, end, 60),
                'note': 'Union of spacecraft non-null values; not an instrument-quality guarantee.'}
    from gfz_factors import run_gfz
    from live_factors import run_live
    coverage.update(run_gfz(fetch,observations,args,start,end,now,error))
    coverage.update(run_live(fetch,observations,args,start,end,now,error))
    return coverage


def read_xrs(path, item, start, end):
    import netCDF4
    import numpy as np
    with netCDF4.Dataset(path) as ds:
        if not all(name in ds.variables for name in ('time','xrsa_flux','xrsb_flux')):
            raise ValueError('XRS file lacks time/xrsa_flux/xrsb_flux')
        platform = str(getattr(ds, 'platform', 'unknown'))
        # Daily files bound the working set; masked fills become NULL.
        times = netCDF4.num2date(ds['time'][:], ds['time'].units,
                                only_use_cftime_datetimes=False, only_use_python_datetimes=True)
        for channel in ('xrsa', 'xrsb'):
            flux = ds[channel + '_flux'][:]
            for index, raw_time in enumerate(times):
                t = raw_time.replace(tzinfo=UTC)
                if not start <= t < end:
                    continue
                value = None if np.ma.is_masked(flux[index]) else number(flux[index])
                quality = None
                yield (stamp(t), 'goes_xrs:' + platform, channel + '_flux', value,
                       str(getattr(ds[channel + '_flux'], 'units', 'W m-2')), quality, *provenance(item))


def download_xrs(fetch, observations, args, start, end, error):
    base = 'https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/'
    # Discover published months first. Retired satellites have no new directories.
    available_months = set()
    for satellite in args.satellites:
        root = base + f'goes{satellite}/l2/data/xrsf-l2-avg1m/'
        for year_url in fetch.listing(root,optional=True):
            match = re.search(r'/(\d{4})/$',year_url)
            if match and start.year <= int(match[1]) <= (end-timedelta(microseconds=1)).year:
                available_months.update(fetch.listing(year_url))
    present = set()
    needed = set()
    day = start.date()
    while day < end.date() or (day == end.date() and end.hour + end.minute + end.second > 0):
        needed.add(day.isoformat())
        day += timedelta(days=1)
    month = start.date().replace(day=1)
    while month <= (end - timedelta(microseconds=1)).date():
        for satellite in args.satellites:
            url = base + f'goes{satellite}/l2/data/xrsf-l2-avg1m/{month:%Y/%m}/'
            if url not in available_months:
                continue
            chosen = {}
            for link in fetch.listing(url):
                match = re.search(r'/(?:dn|sci)_xrsf-l2-avg1m_g(\d+)_d(\d{8})_v(\d+)-(\d+)-(\d+)\.nc$', link)
                if not match:
                    continue
                _, raw_day, *version = match.groups()
                day = datetime.strptime(raw_day, '%Y%m%d').date().isoformat()
                if day not in needed:
                    continue
                rank = tuple(map(int, version))
                if day not in chosen or rank > chosen[day][0]:
                    chosen[day] = (rank, link)
            for day, (_, link) in sorted(chosen.items()):
                try:
                    def validate_xrs(path):
                        for _ in read_xrs(path, {'url':link,'sha256':'validation','retrieved_at':None}, start, end):
                            pass
                    item = fetch.get(link, 'netcdf', validator=validate_xrs)
                    if 'path' not in item:
                        raise ValueError(item.get('error', item['status']))
                    observations.add('measurements', read_xrs(fetch.root / item['path'], item, start, end))
                    present.add(day)
                except Exception as exc:
                    error(link, exc)
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    missing = sorted(needed - present)
    return {'xrs': {'status':'partial' if missing else 'files_downloaded',
                    'days_with_any_satellite_file':len(present),
                    'days_without_any_satellite_file':missing,
                    'note':'Latest available daily version; File presence is not full minute coverage or original publication evidence.'}}
