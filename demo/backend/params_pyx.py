"""Pure-Python stand-in for openpilot/common/params_pyx.pyx.

The device builds params_pyx from C++; the browser cannot. This module keeps
the same contract so upstream server code runs unchanged:

  * the key table (type + default) is parsed from the shipped params_keys.h,
  * values live one file per key under PARAMS_DIR, like common/params.cc,
  * get()/put() cast through the same PYTHON_2_CPP / CPP_2_PYTHON tables,
  * getBool/getInt/getFloat follow params.h (== "1", stoi, stof).
"""
import builtins
import datetime
import enum
import json
import os
import re

PARAMS_DIR = os.environ.get("DEMO_PARAMS_DIR", "/data/params/d")
KEYS_HEADER = os.environ.get("DEMO_PARAMS_KEYS", "/app/openpilot/common/params_keys.h")


class ParamKeyFlag(enum.IntFlag):
  PERSISTENT = 0x02
  CLEAR_ON_MANAGER_START = 0x04
  CLEAR_ON_ONROAD_TRANSITION = 0x08
  CLEAR_ON_OFFROAD_TRANSITION = 0x10
  DONT_LOG = 0x20
  DEVELOPMENT_ONLY = 0x40
  CLEAR_ON_IGNITION_ON = 0x80
  ALL = 0xFFFFFFFF


class ParamKeyType(enum.IntEnum):
  STRING = 0
  BOOL = 1
  INT = 2
  FLOAT = 3
  TIME = 4
  JSON = 5
  BYTES = 6


class UnknownKeyName(Exception):
  pass


_ENTRY = re.compile(r'\{\s*"(?P<key>[^"]+)"\s*,\s*\{(?P<flags>[^,}]+),\s*(?P<type>[A-Z]+)\s*(?:,\s*"(?P<default>(?:[^"\\]|\\.)*)")?\s*\}\s*\}')


def _load_keys(path):
  keys = {}
  with open(path, encoding="utf-8") as f:
    for m in _ENTRY.finditer(f.read()):
      default = m.group("default")
      if default is not None:
        default = bytes(default, "utf-8").decode("unicode_escape").encode("utf-8")
      keys[m.group("key")] = (ParamKeyType[m.group("type")], default)
  return keys


KEYS = _load_keys(KEYS_HEADER)

PYTHON_2_CPP = {
  (str, ParamKeyType.STRING): lambda v: v,
  (builtins.bool, ParamKeyType.BOOL): lambda v: "1" if v else "0",
  (int, ParamKeyType.INT): str,
  (float, ParamKeyType.FLOAT): str,
  (datetime.datetime, ParamKeyType.TIME): lambda v: v.isoformat(),
  (dict, ParamKeyType.JSON): json.dumps,
  (list, ParamKeyType.JSON): json.dumps,
  (bytes, ParamKeyType.BYTES): lambda v: v,
}

CPP_2_PYTHON = {
  ParamKeyType.STRING: lambda v: v.decode("utf-8"),
  ParamKeyType.BOOL: lambda v: v == b"1",
  ParamKeyType.INT: int,
  ParamKeyType.FLOAT: float,
  ParamKeyType.TIME: lambda v: datetime.datetime.fromisoformat(v.decode("utf-8")),
  ParamKeyType.JSON: json.loads,
  ParamKeyType.BYTES: lambda v: v,
}

_NUMBER_PREFIX = re.compile(rb"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


def ensure_bytes(v):
  return v.encode() if isinstance(v, str) else v


def _stoi(raw):
  m = re.match(rb"^\s*[+-]?\d+", raw)
  return int(m.group(0)) if m else 0


def _stof(raw):
  m = _NUMBER_PREFIX.match(raw)
  return float(m.group(0)) if m else 0.0


def write_manager_defaults():
  """system/manager/manager.py writes every unset key's default on each start."""
  params = Params()
  for key, (_t, default) in KEYS.items():
    if default is not None and params._read(key) == b"":
      params._write(key, default)


class Params:
  def __init__(self, d=""):
    self.d = d
    os.makedirs(PARAMS_DIR, exist_ok=True)

  def __reduce__(self):
    return (type(self), (self.d,))

  def _path(self, key):
    return os.path.join(PARAMS_DIR, key if isinstance(key, str) else key.decode())

  def _read(self, key):
    try:
      with open(self._path(key), "rb") as f:
        return f.read()
    except OSError:
      return b""

  def _write(self, key, data):
    with open(self._path(key), "wb") as f:
      f.write(ensure_bytes(data))
    return 0

  def _key(self, key):
    return key.decode() if isinstance(key, bytes) else key

  def clear_all(self, tx_flag=ParamKeyFlag.ALL):
    for key in list(KEYS):
      self.remove(key)

  def check_key(self, key):
    if self._key(key) not in KEYS:
      raise UnknownKeyName(ensure_bytes(key))
    return ensure_bytes(key)

  def python2cpp(self, proposed_type, expected_type, value, key):
    cast = PYTHON_2_CPP.get((proposed_type, expected_type))
    if cast:
      return cast(value)
    raise TypeError(f"Type mismatch while writing param {key}: {proposed_type=} {expected_type=} {value=}")

  def _cpp2python(self, t, value, default, key):
    if value is None:
      return None
    try:
      return CPP_2_PYTHON[t](value)
    except (KeyError, TypeError, ValueError):
      return self._cpp2python(t, default, None, key)

  def get(self, key, block=False, return_default=False):
    self.check_key(key)
    t, default = KEYS[self._key(key)]
    val = self._read(key)
    default_val = default if return_default else None
    if val == b"":
      return self._cpp2python(t, default_val, None, key)
    return self._cpp2python(t, val, default_val, key)

  def get_bool(self, key, block=False):
    self.check_key(key)
    return self._read(key) == b"1"

  def get_int(self, key, block=False):
    self.check_key(key)
    return _stoi(self._read(key))

  def get_float(self, key, block=False):
    self.check_key(key)
    return _stof(self._read(key))

  def _put_cast(self, key, dat):
    self.check_key(key)
    t, _ = KEYS[self._key(key)]
    return ensure_bytes(self.python2cpp(type(dat), t, dat, key))

  def put(self, key, dat):
    self.check_key(key)
    self._write(key, self._put_cast(key, dat))

  def put_bool(self, key, val):
    self.check_key(key)
    self._write(key, b"1" if val else b"0")

  def put_int(self, key, val):
    self.check_key(key)
    self._write(key, str(int(val)))

  def put_float(self, key, val):
    self.check_key(key)
    self._write(key, str(float(val)))

  put_nonblocking = put
  put_bool_nonblocking = put_bool
  put_int_nonblocking = put_int
  put_float_nonblocking = put_float

  def remove(self, key):
    self.check_key(key)
    try:
      os.remove(self._path(key))
    except OSError:
      pass

  def get_param_path(self, key=""):
    key = self._key(key)
    return os.path.join(PARAMS_DIR, key) if key else PARAMS_DIR

  def get_type(self, key):
    self.check_key(key)
    return KEYS[self._key(key)][0]

  def all_keys(self):
    return [k.encode() for k in KEYS]

  def get_default_value(self, key):
    self.check_key(key)
    t, default = KEYS[self._key(key)]
    return self._cpp2python(t, default, None, key) if default is not None else None

  def cpp2python(self, key, value):
    self.check_key(key)
    t, _ = KEYS[self._key(key)]
    return self._cpp2python(t, value, None, key)
