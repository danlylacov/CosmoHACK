"""Operational GOES feeds; archive observations remain separate products.

SWPC differential flux is directional flux per keV, never integral pfu.
https://www.spaceweather.gov/products/goes-proton-flux
"""
import json
from datetime import timedelta

from additional_sources import SWPC, number, provenance, stamp, timestamp

XR_CHANNELS = {'0.05-0.4nm': 'xrsa_flux', '0.1-0.8nm': 'xrsb_flux'}
SPECIES = {'electrons': 'электронов', 'protons': 'протонов', 'alphas': 'альфа-частиц'}


def quantity_for(record, product):
    energy = record['energy']
    if product == 'xrays':
        return XR_CHANNELS[energy]
    channel = str(record.get('channel', ''))
    return product.replace('-', '_') + ':' + energy + (':' + channel if channel else '')


def validate_records(records, product):
    if not isinstance(records, list) or not records:
        raise ValueError('GOES feed is empty or is not a JSON array')
    for record in records:
        if not isinstance(record, dict) or not {'time_tag', 'satellite', 'energy', 'flux'}.issubset(record):
            raise ValueError('GOES record lacks time_tag/satellite/energy/flux')
        if timestamp(record['time_tag']) is None or not isinstance(record['satellite'], int):
            raise ValueError('GOES record lacks a valid timestamp/satellite')
        if record['flux'] is not None:
            try:
                float(record['flux'])
            except (TypeError, ValueError):
                raise ValueError('GOES flux must be numeric or null') from None
        energy = record['energy']
        if not isinstance(energy, str) or not energy:
            raise ValueError('GOES energy channel is empty')
        if product == 'xrays' and energy not in XR_CHANNELS:
            raise ValueError('Unexpected XRS energy channel: ' + energy)
        if product != 'xrays' and not energy.endswith(' keV'):
            raise ValueError('Unexpected differential energy unit: ' + energy)


def validator(product):
    def validate(path):
        validate_records(json.loads(path.read_text(encoding='utf-8-sig')), product)
    return validate


def read_live(records, product, role, item, start, end):
    validate_records(records, product)
    family = 'swpc_xrs' if product == 'xrays' else 'swpc_' + product.replace('-', '_')
    unit = 'W/m2' if product == 'xrays' else 'cm^-2 s^-1 sr^-1 keV^-1'
    for record in records:
        t = timestamp(record['time_tag'])
        if not start <= t < end:
            continue
        value = number(record['flux'])
        if value is None or value < 0:
            value = None
        provider = f'{family}:{role}:g{record["satellite"]}'
        yield (stamp(t), provider, quantity_for(record, product), value, unit,
               None, *provenance(item))


def run_live(fetch, observations, args, start, end, now, error):
    """Download each role/product independently and commit only complete parses."""
    from download_space_weather import grid_coverage
    coverage = {}
    products = []
    for source, group, archive in (
        ('live-spectra', ['differential-' + p for p in SPECIES], None),
        ('live-xrs', ['xrays'], 'xrs'),
    ):
        if source not in args.sources:
            continue
        if end <= now - timedelta(days=7):
            coverage[source] = {'status': 'archive_only' if archive in args.sources else 'partial',
                                'archive_source': archive, 'archive_selected': archive in args.sources,
                                'note': ('Requested period is outside rolling retention; using selected archive product.'
                                         if archive in args.sources else
                                         'Requested period is outside rolling retention and archive is disabled; data unavailable.')}
        else:
            products.extend(group)
    for product in products:
        cadence = 60 if product == 'xrays' else 300
        family = 'swpc_xrs' if product == 'xrays' else 'swpc_' + product.replace('-', '_')
        for role in ('primary', 'secondary'):
            url = SWPC + f'json/goes/{role}/{product}-7-day.json'
            key = f'{family}:{role}'
            try:
                urls = [SWPC + f'json/goes/{role}/{product}-{days}-day.json' for days in (7, 3, 1)]
                item = fetch.get_candidates(urls, 'json', 300, validator=validator(product))
                records = json.loads(fetch.text(item))
                # Validate cached as well as downloaded responses.
                validate_records(records, product)
                observations.add('measurements', read_live(records, product, role, item, start, end))
                quantities = sorted(XR_CHANNELS.values() if product == 'xrays' else
                                    {quantity_for(r, product) for r in records})
                del records
                partial = False
                for quantity in quantities:
                    rows = observations.times('measurements', prefix=key + ':g', quantity=quantity)
                    audit = grid_coverage(rows, start, end, cadence)
                    records = observations.rows('measurements', prefix=key + ':g', quantity=quantity)
                    count, valid = len(records), sum(r['value'] is not None for r in records)
                    audit.update(rows=count, non_null=valid, status='ok' if audit['available'] == audit['expected'] else 'partial',
                                 note='UTC grid, union within role across spacecraft; no interpolation or quality certification. Satellite and channel remain distinct in series.')
                    coverage[key + ':' + quantity] = audit
                    partial |= audit['status'] == 'partial'
                coverage[key] = {'status': 'partial' if partial else 'ok', 'cadence_seconds': cadence,
                                 'note': 'Rolling operational feed; measurement time is not publication time.'}
            except Exception as exc:
                error(url, exc)
                coverage[key] = {'status': 'partial', 'error': str(exc), 'source_url': url}
    return coverage
