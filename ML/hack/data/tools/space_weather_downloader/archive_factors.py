"""Read only EUVS channels used by model time series."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from additional_sources import UTC, number, provenance, stamp

BASE = 'https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/'
PRODUCTS = {
    'euv': ('l2/data/euvs-l2-avg1m/', 'l2/data/euvs-l2-avg1m_science/'),
}
EUV_CHANNELS = tuple('irr_' + w for w in ('256', '284', '304', '1175', '1216', '1335', '1405')) + ('MgII_EXIS', 'MgII_standard')


def wanted_days(start, end):
    day = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)


def discover(fetch, satellite, product, needed):
    """Visit only published years/months; prefer science, then numeric version."""
    chosen = {}
    for directory in PRODUCTS[product]:
        root = BASE + f'goes{satellite}/' + directory
        for year_url in fetch.listing(root, optional=True):
            year = re.search(r'/(\d{4})/$', year_url)
            if not year or not any(d.startswith(year[1]) for d in needed):
                continue
            for month_url in fetch.listing(year_url):
                month = re.search(r'/(\d{4})/(\d{2})/$', month_url)
                if not month or not any(d.startswith(month[1] + '-' + month[2]) for d in needed):
                    continue
                for url in fetch.listing(month_url):
                    match = re.search(r'_g(\d+)_d(\d{8})_v(\d+)-(\d+)-(\d+)\.nc$', url)
                    if not match or int(match[1]) != int(satellite):
                        continue
                    day = datetime.strptime(match[2], '%Y%m%d').date().isoformat()
                    if day not in needed:
                        continue
                    rank = ('science/' in url or '/sci_' in url, *map(int, match.groups()[2:]))
                    if day not in chosen or rank > chosen[day][0]:
                        chosen[day] = (rank, url)
    return {day: entry[1] for day, entry in chosen.items()}


def dataset_times(ds):
    import netCDF4
    if 'time' not in ds.variables:
        raise ValueError('EUV file lacks time')
    var = ds['time']
    times = netCDF4.num2date(var[:],var.units,only_use_cftime_datetimes=False,only_use_python_datetimes=True)
    return [t.replace(tzinfo=UTC) for t in times]


def scalar(value):
    import numpy as np
    return None if np.ma.is_masked(value) else number(value)


def read_euv(ds, item, times, start, end):
    """Preserve values, native timestamps and separate spacecraft."""
    platform = str(getattr(ds, 'platform', getattr(ds, 'platform_ID', 'unknown')))
    for channel in EUV_CHANNELS:
        var = ds[channel]
        values = var[:]
        unit = str(getattr(var, 'units', '')).strip() or 'dimensionless'
        for index, t in enumerate(times):
            if start <= t < end:
                value = scalar(values[index])
                yield (stamp(t), 'goes_euv:' + platform, channel, value, unit,
                       None, *provenance(item))


def run_archives(fetch, observations, args, start, end, error):
    if 'euv' not in args.sources:
        return {}
    import netCDF4
    from download_space_weather import grid_coverage

    needed = set(wanted_days(start,end))
    present = set()
    for satellite in args.satellites:
        try:
            candidates = discover(fetch,satellite,'euv',needed)
        except Exception as exc:
            error(BASE + f'goes{satellite}/',exc)
            continue
        for day,url in sorted(candidates.items()):
            try:
                def validate(path):
                    with netCDF4.Dataset(path) as ds:
                        if not all(name in ds.variables for name in EUV_CHANNELS):
                            raise ValueError('EUV file lacks required channels')
                        times = dataset_times(ds)
                        if not times:
                            raise ValueError('Empty EUV time axis')
                        for name in EUV_CHANNELS:
                            if ds[name].shape != (len(times),):
                                raise ValueError('EUV channel/time length differs: ' + name)
                item = fetch.get(url,'netcdf',validator=validate)
                if 'path' not in item:
                    raise ValueError(item.get('error',item['status']))
                with netCDF4.Dataset(fetch.root/item['path']) as ds:
                    times = dataset_times(ds)
                    if not any(start <= t < end for t in times):
                        raise ValueError('EUV file has no observations in requested interval')
                    observations.add('measurements',read_euv(ds,item,times,start,end))
                present.add(day)
            except Exception as exc:
                error(url,exc)
    missing = sorted(needed-present)
    coverage = {'euv':{'status':'partial' if missing else 'files_downloaded'}}
    for channel in EUV_CHANNELS:
        coverage['euv:' + channel] = grid_coverage(
            observations.times('measurements',prefix='goes_euv:',quantity=channel),start,end,60)
    return coverage
