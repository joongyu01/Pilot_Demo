import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from cluster_fields import extract

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    def write(path, text):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    write('openpilot/selfdrive/carrot_settings.json', json.dumps({'params': [
        {'name': 'ClusterLaneTest', 'min': -1, 'max': 173, 'default': 0},
        {'name': 'OtherSetting', 'default': 99}]}))
    assert extract(root) == {'settings': {'ClusterLaneTest': {'min': -1, 'max': 173, 'default': 0}}, 'fields': {}}
    write('opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py',
          'CLUSTER_TEST_FIELDS_161 = {45: "LANELINE_LEFT", 46: "MISSING"}\nraise RuntimeError("must not execute upstream")')
    write('opendbc_repo/opendbc/dbc/generator/hyundai/hyundai_canfd.dbc',
          'BO_ 353 LFAHDA_CLUSTER: 16 XXX\n SG_ LANELINE_LEFT : 17|4@1+ (1,0) [0|15] "" XXX\n')
    result = extract(root)
    assert result['fields'] == {'45': dict(address=353, length=16, name='LANELINE_LEFT', start=17,
        size=4, little=True, signed=False, factor=1.0, offset=0.0)}
print('PASS: deployed source mapping, DBC signature, missing fields, settings whitelist and no upstream execution')
