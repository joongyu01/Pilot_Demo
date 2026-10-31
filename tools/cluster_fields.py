"""Describe supported test fields from the exact upstream files being deployed."""
import ast
import json
from pathlib import Path
import re

TABLES = {'CLUSTER_TEST_FIELDS': 354, 'CLUSTER_TEST_FIELDS_161': 353,
          'CLUSTER_TEST_FIELDS_1EA': 490, 'CLUSTER_TEST_FIELDS_200': 512}
KEYS = ('ClusterLaneTest', 'ClusterLaneTestValue', 'ClusterLaneTestFrom', 'ClusterLaneTestTo')


def extract(upstream):
    root = Path(upstream)
    catalog = json.loads((root / 'openpilot/selfdrive/carrot_settings.json').read_text(encoding='utf-8'))
    settings = {p['name']: {k: p.get(k) for k in ('min', 'max', 'default')}
                for p in catalog['params'] if p.get('name') in KEYS}
    controller = root / 'opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py'
    dbc = root / 'opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd.dbc'
    if not controller.exists() or not dbc.exists():
        return {'settings': settings, 'fields': {}}
    signals, address = {}, None
    for line in dbc.read_text(encoding='utf-8').splitlines():
        msg = re.match(r'BO_ (\d+) \w+: (\d+) ', line)
        if msg:
            address, length = map(int, msg.groups())
        sig = re.match(r'\s*SG_ (\w+) : (\d+)\|(\d+)@(\d)([+-]) \(([^,]+),([^\)]+)\)', line)
        if sig and address in TABLES.values():
            name, start, size, endian, sign, factor, offset = sig.groups()
            signals[address, name] = dict(address=address, length=length, name=name, start=int(start),
                size=int(size), little=endian == '1', signed=sign == '-', factor=float(factor), offset=float(offset))
    fields = {}
    for node in ast.parse(controller.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in TABLES:
            address = TABLES[node.targets[0].id]
            for slot, name in ast.literal_eval(node.value).items():
                if (address, name) in signals:
                    fields[str(slot)] = signals[address, name]
    return {'settings': settings, 'fields': fields}
