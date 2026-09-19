"""Plain Python records with atomic batches and explicit deduplication keys."""

PROVENANCE = ('source_url', 'raw_sha256', 'retrieved_at')
FIELDS = {
    'particles': ('time','provider','role','satellite_proton','satellite_electron',
                  'P1','P5','P10','P30','P50','P60','P100','P500','E2_0', *PROVENANCE),
    'kp': ('time','provider','kp','ap','definitive', *PROVENANCE),
    'measurements': ('time','provider','quantity','value','unit','quality', *PROVENANCE),
}
KEYS = {
    'particles': ('time','provider','role'),
    'kp': ('time','provider'),
    'measurements': ('time','provider','quantity','raw_sha256'),
}


class Observations:
    def __init__(self):
        self.records = {kind: {} for kind in FIELDS}

    def add(self, kind, rows):
        # Parse the entire source batch before publishing any of its records.
        batch = {}
        fields = FIELDS[kind]
        for values in rows:
            if isinstance(values, dict):
                row = dict(values)
                if set(row) != set(fields):
                    raise ValueError(f'Unexpected fields for {kind}')
            else:
                if len(values) != len(fields):
                    raise ValueError(f'Unexpected field count for {kind}')
                row = dict(zip(fields, values))
            key = tuple(row[field] for field in KEYS[kind])
            batch[key] = row
        self.records[kind].update(batch)

    def rows(self, kind, *, prefix=None, **filters):
        return [row for row in self.records[kind].values()
                if all(row[key] == value for key, value in filters.items())
                and (prefix is None or row['provider'].startswith(prefix))]

    def groups(self, kind, *fields):
        groups = {}
        for row in self.records[kind].values():
            groups.setdefault(tuple(row[field] for field in fields), []).append(row)
        return sorted(groups.items())

    def times(self, kind, *, value='value', minute=False, **filters):
        times = {row['time'][:16] + ':00Z' if minute else row['time']
                 for row in self.rows(kind, **filters) if row[value] is not None}
        return [(t,) for t in sorted(times)]

