#!/usr/bin/env python3
"""Download observed space weather; never interpolate gaps.
Dates are inclusive UTC calendar dates. See README.md for provenance and limits.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import math
import re
import tempfile
import sys
sys.dont_write_bytecode = True
from observations import Observations
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from itertools import islice
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class BoundedRetry(Retry):
    """An upstream Retry-After must not suspend the collector for hours."""
    def get_retry_after(self, response):
        delay = super().get_retry_after(response)
        if delay is not None and delay > 30:
            raise requests.exceptions.RetryError(f'Server requested Retry-After={delay}s; defer this source instead of retrying early')
        return delay

UTC = timezone.utc
MIN_DATE, MAX_DATE = date(2023, 1, 1), date(2026, 9, 19)
HAPI = 'https://iswa.gsfc.nasa.gov/IswaSystemWebApp/hapi/'
GOES = 'https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/'
SWPC = 'https://services.swpc.noaa.gov/'
KP_HISTORY = 'https://kp.gfz.de/app/files/Kp_ap_since_1932.txt'
KP_NOW = 'https://kp.gfz.de/fileadmin/files_for_gfz_cms/Kp_ap_nowcast.txt'
CHANNELS = ('P1', 'P5', 'P10', 'P30', 'P50', 'P60', 'P100', 'P500', 'E2_0')
def iso(t):
    return t.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_time(s):
    t = datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def midnight(d):
    return datetime.combine(d, datetime.min.time(), UTC)


def days(start, end):
    d = start
    while d < end:
        yield d
        d += timedelta(days=1)


def numeric(value):
    try:
        x = float(value)
        return x if math.isfinite(x) and x >= 0 else None
    except (ValueError, TypeError):
        return None


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href' and v)


def listing_links(text, base):
    parser = Links()
    parser.feed(text)
    result = []
    for href in parser.links:
        url = urljoin(base, href)
        # Do not follow parent directory links, arbitrary servers, or query sorts.
        if url.startswith(base) and url != base and not urlparse(url).query:
            result.append(url)
    return sorted(set(result))


def bounded_map(pool, function, iterable, limit=16):
    """Ordered executor map with a fixed number of queued/in-flight results."""
    iterator = iter(iterable)
    pending = deque(pool.submit(function, item) for item in islice(iterator, limit))
    while pending:
        yield pending.popleft().result()
        for item in islice(iterator, 1):
            pending.append(pool.submit(function, item))


def validate_payload(path, kind, size):
    """Reject transport error pages before they become reusable cache entries."""
    if not size:
        raise ValueError('Empty response is not a data product')
    with path.open('rb') as stream:
        prefix = stream.read(512).lstrip().lower()
    if kind != 'listing' and (prefix.startswith((b'<html', b'<!doctype html')) or b'<html' in prefix):
        raise ValueError('Received HTML instead of a data product')
    if kind == 'netcdf':
        with path.open('rb') as stream:
            magic = stream.read(8)
        if not (magic.startswith((b'CDF\x01', b'CDF\x02', b'CDF\x05')) or magic == b'\x89HDF\r\n\x1a\n'):
            raise ValueError('Response has no NetCDF/HDF5 signature')
    elif kind in ('json', 'hapi_info'):
        with path.open(encoding='utf-8-sig') as stream:
            json.load(stream)


class Fetcher:
    """Download validated files into a temporary workspace; reuse URLs within one run."""
    def __init__(self, root, timeout=90):
        self.root, self.timeout = root, timeout
        self.objects = root / 'objects'
        self.objects.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.local = threading.local()
        self.events = []
        self.recoveries = []
        self.verified_objects = set()
        self.cache = {}

    def prune(self):
        # Reuse annual OMNI/Kp and directory listings, release completed daily products.
        keep = {url:item for url,item in self.cache.items()
                if url in (KP_HISTORY,KP_NOW) or 'omni_5min' in url
                or item['kind'] in ('listing','hapi_info')}
        paths = {item['path'] for item in keep.values()}
        for item in self.cache.values():
            if item['path'] not in paths:
                (self.root/item['path']).unlink(missing_ok=True)
        self.cache = keep
        self.verified_objects.clear()
        self.events.clear()
        self.recoveries.clear()

    def session(self):
        if not hasattr(self.local, 'session'):
            session = requests.Session()
            retry = BoundedRetry(total=3, connect=3, read=3, backoff_factor=1,
                          status_forcelist=[429, 500, 502, 503, 504],
                          allowed_methods=['GET'], respect_retry_after_header=True)
            session.mount('https://', HTTPAdapter(max_retries=retry))
            session.headers['User-Agent'] = 'EVA-space-weather-research-downloader/1.0'
            self.local.session = session
        return self.local.session

    def event(self, record):
        with self.lock:
            if record['status'] in ('error', 'stale_cache') or (record['status']=='not_found' and not record.get('optional')):
                self.events.append(record)
        return record

    def get(self, url, kind, ttl=86400, optional=False, _attempt=0, validator=None):
        now = datetime.now(UTC)
        with self.lock:
            old = self.cache.get(url)
        record = {'url': url, 'kind': kind, 'checked_at': iso(now), 'optional':optional}
        if old:
            try:
                path = (self.root / old['path']).resolve()
                if self.objects.resolve() not in path.parents:
                    raise ValueError('Cache object outside objects directory')
                identity = (str(path), path.stat().st_mtime_ns, path.stat().st_size, old['sha256'])
                if identity not in self.verified_objects:
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        for block in iter(lambda:stream.read(1024*1024), b''): digest.update(block)
                    if digest.hexdigest() != old['sha256']:
                        raise ValueError('Cached object SHA-256 mismatch')
                    self.verified_objects.add(identity)
                parse_time(old['checked_at'])
                old['retrieved_at']
                if validator: validator(path)
            except Exception as exc:
                self.event({**record,'status':'error','error':f'Invalid cached object; will download again: {exc}'})
                old = None
        if old:
            if (now - parse_time(old['checked_at'])).total_seconds() < ttl:
                return self.event({**old, **record, 'status': 'cached'})
        else:
            old = None
        headers = {}
        if old:
            if old.get('etag'):
                headers['If-None-Match'] = old['etag']
            if old.get('last_modified'):
                headers['If-Modified-Since'] = old['last_modified']
        part = self.objects / (hashlib.sha256(url.encode()).hexdigest() + '.part')
        try:
            with self.session().get(url, headers=headers, timeout=(15, self.timeout), stream=True) as response:
                record['http_status'] = response.status_code
                if response.status_code == 304 and old:
                    item = {**old, **record, 'status': 'not_modified'}
                elif response.status_code in (404,410):
                    if old:
                        return self.event({**old, **record, 'status':'stale_cache',
                                           'error':f'HTTP {response.status_code}; keeping previously downloaded data'})
                    return self.event({**record, 'status': 'not_found'})
                else:
                    response.raise_for_status()
                    # HTTP 202 with an empty body is not a downloaded data product.
                    if response.status_code != 200:
                        raise ValueError(f'Expected HTTP 200, got {response.status_code}')
                    digest, size = hashlib.sha256(), 0
                    deadline = time.monotonic() + max(60, self.timeout * 4)
                    with part.open('wb') as f:
                        for chunk in response.iter_content(1024 * 256):
                            if time.monotonic() > deadline:
                                raise requests.exceptions.Timeout('Download exceeded transfer deadline')
                            if chunk:
                                f.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                    validate_payload(part, kind, size)
                    if validator: validator(part)
                    ext = '.nc' if kind == 'netcdf' else '.json' if kind in ('json','hapi_info') else '.html' if kind == 'listing' else '.txt'
                    sha = digest.hexdigest()
                    dest = self.objects / (sha + ext)
                    part.replace(dest)
                    item = {**record, 'status': 'downloaded', 'path': str(dest.relative_to(self.root)),
                            'sha256': sha, 'bytes': size, 'retrieved_at': iso(now),
                            'last_modified': response.headers.get('Last-Modified'),
                            'etag': response.headers.get('ETag')}
            with self.lock:
                self.cache[url] = item
            return self.event(item)
        except Exception as exc:
            part.unlink(missing_ok=True)
            if isinstance(exc,(requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                               requests.exceptions.ChunkedEncodingError)) and _attempt < 2:
                time.sleep(2 ** _attempt)
                return self.get(url,kind,ttl,optional,_attempt+1,validator)
            # Preserve cached data on transport errors, with an explicit stale marker.
            if old:
                return self.event({**old, **record, 'status': 'stale_cache', 'error': str(exc)})
            return self.event({**record, 'status': 'error', 'error': str(exc)})

    def get_candidates(self, urls, kind, ttl=86400, validator=None):
        """Only try documented equivalent endpoints; prefer live data over stale cache."""
        attempts = []
        for url in urls:
            item = self.get(url,kind,ttl,validator=validator)
            attempts.append(item)
            if 'path' in item and item['status'] != 'stale_cache':
                failed_urls = {attempt['url'] for attempt in attempts[:-1]}
                if failed_urls:
                    with self.lock:
                        self.events[:] = [e for e in self.events if e.get('url') not in failed_urls]
                        self.recoveries.append({'failed_urls':sorted(failed_urls),'replacement_url':item['url']})
                return item
        return next((item for item in attempts if 'path' in item),attempts[-1])

    def text(self, item):
        if 'path' not in item:
            raise ValueError(item.get('error', item['status']))
        return (self.root / item['path']).read_text(encoding='utf-8-sig')

    def listing(self, url, optional=False):
        item = self.get(url, 'listing', optional=optional)
        if 'path' not in item:
            return []
        text = self.text(item)
        if '<html' not in text.lower() and '<!doctype' not in text.lower():
            self.event({'url': url, 'kind': 'listing', 'status': 'error',
                        'error': 'Expected HTML directory listing', 'checked_at': iso(datetime.now(UTC))})
            return []
        return listing_links(text, url)


def particle_row(t, provider, role, values, item):
    return (iso(t), provider, role, values.get('satelliteProton'), values.get('satelliteElectron'),
            *[numeric(values.get(c)) for c in CHANNELS], item['url'], item['sha256'], item['retrieved_at'])


def read_hapi(text, parameters, role, item, start, end):
    if text.lstrip().startswith('{'):
        status = json.loads(text).get('status', {})
        if status.get('code') == 1201:
            return []
        raise ValueError('HAPI returned status instead of CSV: ' + str(status))
    names = [p['name'] for p in parameters]
    if not all(c in names for c in ('Time', 'P10', 'P50', 'P100', 'E2_0')):
        raise ValueError('Required HAPI channels missing from metadata')
    result = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        # Older records may lack the final satellite-ID fields. Never guess IDs.
        if not 12 <= len(row) <= len(names):
            raise ValueError(f'Unexpected HAPI column count: {len(row)}; metadata: {len(names)}')
        values = dict(zip(names, row))
        t = parse_time(values['Time'])
        if start <= t < end:
            result.append(particle_row(t, 'iswa', role, values, item))
    return result


def read_swpc(data, species, role, item, start, end):
    grouped = {}
    for row in data:
        t = parse_time(row['time_tag'])
        if not start <= t < end:
            continue
        energy = re.fullmatch(r'>=?\s*([\d.]+)\s*MeV', row['energy'])
        if not energy:
            continue
        threshold = float(energy[1])
        channel = 'E2_0' if species == 'electrons' and threshold == 2 else 'P' + str(int(threshold)) if species == 'protons' and threshold.is_integer() else None
        if channel in CHANNELS:
            values = grouped.setdefault(t, {})
            satellite_key = 'satelliteElectron' if species == 'electrons' else 'satelliteProton'
            satellite = 'GOES-' + str(row['satellite'])
            if satellite_key in values and values[satellite_key] != satellite:
                raise ValueError(f'Conflicting SWPC satellites at {iso(t)} for {species}')
            if channel in values and values[channel] != row['flux']:
                raise ValueError(f'Conflicting SWPC values at {iso(t)} for {channel}')
            values[channel] = row['flux']
            values[satellite_key] = satellite
    # Species-specific provider names prevent one download overwriting the other.
    return [particle_row(t, 'swpc_' + species, role, v, item) for t, v in grouped.items()]


def read_kp(text, provider, item, start, end):
    result = []
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) != 10:
            raise ValueError('Unexpected GFZ Kp/ap record: ' + line[:100])
        y, m, day = map(int, fields[:3])
        t = datetime(y, m, day, tzinfo=UTC) + timedelta(hours=float(fields[3]))
        if start <= t < end:
            result.append((iso(t), provider, numeric(fields[7]), numeric(fields[8]), int(fields[9]),
                           item['url'], item['sha256'], item['retrieved_at']))
    return result


def text_product_validator(parser):
    """Fully parse a candidate before it replaces the last usable cache object."""
    def validate(path):
        result = parser(path.read_text(encoding='utf-8-sig'))
        if result is not None and not isinstance(result, datetime):
            for _ in result:
                pass
    return validate


def compact_gaps(missing, seconds):
    result = []
    for t in sorted(missing):
        if result and t == result[-1][1]:
            result[-1][1] = t + timedelta(seconds=seconds)
            result[-1][2] += 1
        else:
            result.append([t, t + timedelta(seconds=seconds), 1])
    return [{'start': iso(a), 'end_exclusive': iso(b), 'missing_samples': n} for a,b,n in result]


def grid_coverage(rows, start, end, seconds):
    """Audit a sorted timestamp cursor without materializing the expected grid."""
    expected = max(0, math.ceil((end - start).total_seconds() / seconds))
    available, cursor, gaps = 0, 0, []
    def gap(a, b):
        if b > a:
            gaps.append({'start': iso(start + timedelta(seconds=a * seconds)),
                         'end_exclusive': iso(start + timedelta(seconds=b * seconds)),
                         'missing_samples': b - a})
    for row in rows:
        t = parse_time(row[0])
        offset = (t - start).total_seconds()
        index = int(offset // seconds)
        if offset % seconds or index < cursor or index >= expected:
            continue
        gap(cursor, index)
        available += 1
        cursor = index + 1
    gap(cursor, expected)
    return {'expected': expected, 'available': available, 'gaps': gaps}


def coverage(observations, start, end, sources=None):
    sources = {'particles', 'kp'} if sources is None else sources
    result = {}
    if 'particles' in sources:
        for role in ('primary', 'secondary'):
            for channel in ('P10','P50','P100','E2_0'):
                rows = observations.times('particles', role=role, value=channel)
                result[role + '_' + channel] = {**grid_coverage(rows, start, end, 300),
                    'scope': 'union of ISWA and SWPC availability, no interpolation; not an instrument-quality guarantee'}
    if 'kp' in sources:
        result['Kp'] = grid_coverage(observations.times('kp', value='kp'), start, end, 10800)
    return result


def collect(args, fetch, start, end, now):
    observations = Observations()
    errors, info_by_role = [], {}
    def error(context, exc):
        errors.append({'context':context,'error':str(exc)})
        print('WARNING:',context,str(exc),file=sys.stderr,flush=True)
    def insert_particles(rows):
        observations.add('particles', rows)
    if 'particles' in args.sources:
        from source_health import hapi_metadata, json_records
        # Metadata determines HAPI column order; never rely on a hard-coded CSV position.
        for role, dataset in [('primary','goesp_part_flux_P5M'),('secondary','goess_part_flux_P5M')]:
            try:
                item=fetch.get(HAPI+'info?'+urlencode({'id':dataset}),'hapi_info',3600,validator=hapi_metadata)
                info=json.loads(fetch.text(item))
                if info.get('status',{}).get('code') != 1200:
                    raise ValueError('HAPI metadata status: '+str(info.get('status')))
                info_by_role[role]=(dataset,info)
            except Exception as exc: error(role+' HAPI metadata',exc)
        tasks=[]
        for role,(dataset,info) in info_by_role.items():
            for day in days(start.date(),(end-timedelta(microseconds=1)).date()+timedelta(days=1)):
                a,b=midnight(day),min(midnight(day+timedelta(days=1)),end)
                if b<=a: continue
                url=HAPI+'data?'+urlencode({'id':dataset,'time.min':iso(a),'time.max':iso(b),'format':'csv'})
                tasks.append((role,info,url,3600 if (now-a).days<30 else 86400*30))
        print(f'Integral particle daily requests: {len(tasks)}',flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n,(task,item) in enumerate(zip(tasks,bounded_map(pool,lambda t:fetch.get(
                    t[2],'hapi_csv',t[3],validator=text_product_validator(
                        lambda text: read_hapi(text,t[1]['parameters'],t[0],
                            {'url':t[2],'sha256':'validation','retrieved_at':iso(now)},start,end))),tasks)),1):
                role,info,url,_=task
                try: insert_particles(read_hapi(fetch.text(item),info['parameters'],role,item,start,end))
                except Exception as exc: error(url,exc)
                if n%30==0: print(f'Particles: {n}/{len(tasks)}',flush=True)
        # A rolling window supplements archive ingestion lag. Keep it as a separate provider.
        if end > now-timedelta(days=7):
            for role in ('primary','secondary'):
                for species in ('protons','electrons'):
                    url=SWPC+f'json/goes/{role}/integral-{species}-7-day.json'
                    try:
                        def validate_swpc(path):
                            json_records(['time_tag','energy','flux','satellite'])(path)
                            read_swpc(json.loads(path.read_text(encoding='utf-8-sig')),species,role,
                                      {'url':url,'sha256':'validation','retrieved_at':iso(now)},start,end)
                        item=fetch.get(url,'json',300,validator=validate_swpc)
                        insert_particles(read_swpc(json.loads(fetch.text(item)),species,role,item,start,end))
                    except Exception as exc: error(url,exc)
    if 'kp' in args.sources:
        for provider,url,ttl in [('gfz_history',KP_HISTORY,86400),('gfz_nowcast',KP_NOW,3600)]:
            try:
                item=fetch.get(url,'text',ttl,validator=text_product_validator(
                    lambda text: read_kp(text,provider,{'url':url,'sha256':'validation','retrieved_at':iso(now)},start,end)))
                observations.add('kp', read_kp(fetch.text(item),provider,item,start,end))
            except Exception as exc:error(url,exc)
    from additional_sources import run as run_additional
    additional = run_additional(fetch,observations,args,start,end,now,error)
    from archive_factors import run_archives
    archive_coverage = run_archives(fetch,observations,args,start,end,error)
    additional.update(archive_coverage)
    result=coverage(observations,start,end,args.sources)
    required_gaps=any(result[k]['available']<result[k]['expected'] for k in ['primary_P10','primary_P50','primary_P100','primary_E2_0','Kp'] if k in result)
    additional_gaps = any(v.get('status') in ('partial', 'not_applicable') or v.get('available', 0) < v.get('expected', 0)
                          for v in additional.values())
    transport_errors = fetch.events
    partial = bool(required_gaps or additional_gaps or errors or transport_errors)
    for event in transport_errors:
        print('WARNING:',event['url'],event.get('error',event['status']),file=sys.stderr)
    return observations, partial


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, help='Один день UTC, например 2024-05-10; по умолчанию сегодня')
    parser.add_argument('--start', type=date.fromisoformat, help='Первый день UTC YYYY-MM-DD')
    parser.add_argument('--end', type=date.fromisoformat, help='Последний день включительно; по умолчанию равен --start')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent, help='Каталог единственного выходного файла results.json')
    parser.add_argument('--sources', default='all', help='Comma-separated: particles,kp,omni,solar-wind,xrs,hpo,solar-indices,live-spectra,live-xrs,euv; default all')
    parser.add_argument('--profile', choices=['full','light'], default='full', help='light omits XRS and EUV NetCDF archives')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=30, help='Read timeout per HTTP request, seconds')
    parser.add_argument('--skip-netcdf', action='store_true', help='Omit XRS and EUV NetCDF archives')
    parser.add_argument('--satellites', type=int, nargs='+', default=[16,17,18,19], choices=[16,17,18,19])
    parser.add_argument('--strict', action='store_true', help='Exit 2 if required observation coverage has gaps or downloads fail')
    parser.add_argument('--plan-only', action='store_true', help='Print scope without network access')
    args = parser.parse_args(argv)
    if args.timeout < 1: parser.error('--timeout must be positive')
    if args.date and (args.start or args.end):
        parser.error('--date нельзя сочетать с --start/--end')
    if args.end and not args.start:
        parser.error('Для --end укажите --start; для одного дня используйте --date')
    args.start = args.date or args.start or datetime.now(UTC).date()
    args.end = args.date or args.end or args.start
    available = {'particles','kp','omni','solar-wind','xrs','hpo','solar-indices','live-spectra','live-xrs','euv'}
    args.sources = available.copy() if args.sources == 'all' else set(args.sources.split(','))
    if not args.sources or args.sources - available:
        parser.error('Unknown --sources; choose from ' + ','.join(sorted(available)))
    # Live feeds have finite retention; select their archive companions explicitly.
    if 'live-xrs' in args.sources: args.sources.add('xrs')
    if args.profile == 'light' or args.skip_netcdf:
        args.sources -= {'xrs','euv'}
    if not args.sources:
        parser.error('No sources remain after applying --profile/--skip-netcdf')
    max_date = max(MAX_DATE, datetime.now(UTC).date())
    if not MIN_DATE <= args.start <= args.end <= max_date:
        parser.error(f'Require {MIN_DATE} <= start <= end <= {max_date}')
    if not 1 <= args.workers <= 8 or args.timeout < 1:
        parser.error('workers must be 1..8; timeout must be positive')
    start, requested_end = midnight(args.start), midnight(args.end + timedelta(days=1))
    now = datetime.now(UTC)
    end = min(requested_end, now)
    config = {'start':iso(start), 'effective_end_exclusive':iso(end), 'sources':sorted(args.sources)}
    if args.plan_only:
        print(json.dumps(config,indent=2)); return 0
    if end <= start:
        parser.error('Requested UTC interval has not started yet; no observations exist for it')
    destination = args.out.resolve() / 'results.json'
    workspace = tempfile.TemporaryDirectory(prefix='space-weather-')
    fetch = Fetcher(Path(workspace.name), args.timeout)
    partial = False
    def chunks():
        nonlocal partial
        from json_result import result_data
        cursor = start
        while cursor < end:
            stop = min(cursor + timedelta(days=7),end)
            print(f'Период: {iso(cursor)} — {iso(stop)}',flush=True)
            observations, incomplete = collect(args,fetch,cursor,stop,now)
            partial |= incomplete
            yield result_data(observations)
            del observations
            fetch.prune()
            cursor = stop
    try:
        from json_result import export_chunks
        export_chunks(destination,chunks(),iso(start),iso(end))
        print(f"Результат: {destination}\nСтатус: {'partial' if partial else 'complete_for_effective_interval'}",flush=True)
        return 2 if args.strict and partial else 0
    finally:
        workspace.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
