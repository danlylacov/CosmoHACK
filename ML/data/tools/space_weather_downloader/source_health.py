"""Source endpoint selection and payload validators."""
import json
def json_records(required):
    def validate(path):
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,list) or any(not isinstance(row,dict) or not set(required).issubset(row) for row in data):
            raise ValueError('Unexpected JSON records; required fields: ' + ', '.join(required))
    return validate


def hapi_metadata(path):
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('status',{}).get('code')!=1200:
        raise ValueError('Invalid HAPI metadata status')
    names={p.get('name') for p in data.get('parameters',[]) if isinstance(p,dict)}
    if not {'Time','P10','P50','P100','E2_0'}.issubset(names):
        raise ValueError('HAPI metadata lacks required channels')


