"""Runs the upstream Carrot Web server inside Pyodide.

Nothing here reimplements an endpoint. The upstream aiohttp routes are
registered exactly as carrot_server.py does, and each browser request is
dispatched to the real handler. This file only supplies what a browser cannot:
native Params, device hardware, threads, and it cuts every outbound network
path so a demo visitor's values never leave the browser.
"""
import asyncio
import importlib
import json
import os
import sys
import types
from unittest import mock

APP_ROOT = os.environ.get("DEMO_APP_ROOT", "/app")
DEMO_DIR = os.path.join(APP_ROOT, "demo")
os.environ.setdefault("CARROT_DATA_DIR", "/data/carrot")
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEVICE_TYPE = "tizi"
STUBBED: list[str] = []


# ── native replacements ─────────────────────────────────────
def _install_native_fakes() -> None:
  import params_pyx
  sys.modules["openpilot.common.params_pyx"] = params_pyx

  hardware = types.ModuleType("openpilot.system.hardware")
  hardware.HARDWARE = types.SimpleNamespace(get_device_type=lambda: DEVICE_TYPE)
  hardware.PC = False
  hardware.TICI = True
  hardware.AGNOS = True
  sys.modules["openpilot.system.hardware"] = hardware
  system = sys.modules.get("openpilot.system") or types.ModuleType("openpilot.system")
  system.__path__ = []
  system.hardware = hardware
  sys.modules["openpilot.system"] = system

  async def to_thread(func, /, *args, **kwargs):
    # Pyodide has no threads; the handlers only use this to keep file IO off
    # the aiohttp loop, so running inline is the same result.
    return func(*args, **kwargs)
  asyncio.to_thread = to_thread


def _block_network() -> None:
  class _Offline(OSError):
    pass

  def refuse(*_args, **_kwargs):
    raise _Offline("network is disabled in the Carrot Web demo")

  import urllib.request
  urllib.request.urlopen = refuse
  import socket
  socket.create_connection = refuse
  try:
    import aiohttp
    aiohttp.ClientSession._request = lambda self, *a, **k: refuse()
  except Exception:
    pass
  try:
    import pyodide.http as pyhttp
    pyhttp.pyfetch = refuse
    pyhttp.open_url = refuse
  except Exception:
    pass
  import subprocess
  subprocess.Popen = refuse
  subprocess.run = refuse
  subprocess.check_output = refuse
  subprocess.check_call = refuse
  subprocess.call = refuse


def _stub_module(name: str) -> None:
  parts = name.split(".")
  for i in range(1, len(parts) + 1):
    mod_name = ".".join(parts[:i])
    if mod_name not in sys.modules:
      stub = mock.MagicMock(name=mod_name)
      stub.__path__ = []
      stub.__spec__ = None
      sys.modules[mod_name] = stub
  STUBBED.append(name)


def _import_features():
  # Device-only dependencies (cereal/msgq, modeld, jeepney, ...) are stubbed
  # only when their absence actually breaks the import chain, so optional
  # `try: import x` fallbacks in upstream still see the real absence.
  for _ in range(200):
    try:
      return importlib.import_module("openpilot.selfdrive.carrot.server.features")
    except ModuleNotFoundError as e:
      if not e.name or e.name in STUBBED:
        raise
      _stub_module(e.name)
  raise RuntimeError("too many missing modules")


# ── demo overrides (data the browser cannot compute) ────────
def _apply_overrides(features) -> None:
  from openpilot.selfdrive.carrot.server.features import cars, static
  from openpilot.selfdrive.carrot.server.services import popular_values

  with open(os.path.join(DEMO_DIR, "cars.json"), encoding="utf-8") as f:
    snapshot = json.load(f)
  cars.load_supported_cars = lambda: (snapshot["sources"], snapshot["makers"])

  shell_path = os.path.join(DEMO_DIR, "app-shell.html")
  def load_shell() -> str:
    with open(shell_path, encoding="utf-8") as f:
      return f.read()
  static._load_index_html = load_shell

  # Popular values come from the Carrot server; the demo stays offline.
  popular_values.schedule_popular_value_refresh = lambda *_a, **_k: None
  for mod in list(sys.modules.values()):
    if getattr(mod, "schedule_popular_value_refresh", None) is not None and \
       getattr(mod, "__name__", "").startswith("openpilot.selfdrive.carrot.server."):
      mod.schedule_popular_value_refresh = popular_values.schedule_popular_value_refresh


APP = None


def boot() -> dict:
  global APP
  _install_native_fakes()
  _block_network()

  import params_pyx
  params_pyx.write_manager_defaults()

  features = _import_features()
  _apply_overrides(features)

  from aiohttp import web
  from openpilot.selfdrive.carrot.server.services.params import HAS_PARAMS, Params
  app = web.Application()
  features.register_all(app)
  app["params"] = Params() if HAS_PARAMS else None
  app["hb_last"] = {"ok": None, "msg": "demo", "ts": 0}
  app["realtime_broker"] = None
  app["realtime_broker_error"] = "demo"
  app["realtime_broker_poll_lock"] = asyncio.Lock()
  app["realtime_camera_hub"] = None
  app["realtime_raw_hub"] = None
  app.freeze()
  APP = app
  return {"stubbed": STUBBED, "has_params": HAS_PARAMS}


def _make_request(method: str, path: str, headers: dict, payload):
  """A real aiohttp web.Request, built the way aiohttp.test_utils does it
  (Pyodide's aiohttp ships without test_utils)."""
  from aiohttp import web
  from aiohttp.http import HttpVersion, RawRequestMessage
  from multidict import CIMultiDict, CIMultiDictProxy
  from yarl import URL

  hdrs = CIMultiDictProxy(CIMultiDict(headers or {}))
  message = RawRequestMessage(
    method=method.upper(), path=path, version=HttpVersion(1, 1), headers=hdrs,
    raw_headers=tuple((k.encode(), v.encode()) for k, v in hdrs.items()),
    should_close=False, compression=None, upgrade=False, chunked=False, url=URL(path),
  )
  transport = mock.Mock()
  transport.get_extra_info.side_effect = lambda key, default=None: {
    "peername": ("127.0.0.1", 0), "sockname": ("127.0.0.1", 7000),
  }.get(key, default)
  transport.is_closing.return_value = False
  protocol = mock.Mock(transport=transport, max_field_size=8190, max_line_length=8190, max_headers=128)
  type(protocol).peername = mock.PropertyMock(return_value=("127.0.0.1", 0))
  type(protocol).ssl_context = mock.PropertyMock(return_value=None)
  writer = mock.Mock(transport=transport)
  protocol.writer = writer
  return web.Request(message, payload, protocol, writer, mock.Mock(), asyncio.get_event_loop(),
                     client_max_size=64 * 1024 * 1024)


async def dispatch(method: str, path: str, headers: dict, body: bytes | None) -> dict:
  from aiohttp import streams, web

  loop = asyncio.get_event_loop()
  payload = streams.StreamReader(mock.Mock(_reading_paused=False), 2 ** 16, loop=loop)
  if body:
    payload.feed_data(bytes(body))
  payload.feed_eof()
  request = _make_request(method, path, dict(headers or {}), payload)
  match_info = await APP.router.resolve(request)
  match_info.add_app(APP)
  match_info.freeze()
  request._match_info = match_info

  try:
    response = await match_info.handler(request)
  except web.HTTPException as exc:
    response = exc
  except Exception as exc:  # the real server's middleware would 500 too
    return _pack(500, {"Content-Type": "application/json"},
                 json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode())

  if isinstance(response, web.FileResponse):
    file_path = getattr(response, "_path", None)
    try:
      with open(file_path, "rb") as f:
        data = f.read()
    except Exception:
      return _pack(404, {"Content-Type": "application/json"}, b'{"ok": false, "error": "file not found"}')
    return _pack(response.status, dict(response.headers), data)

  if isinstance(response, web.Response):
    data = response.body
    if data is None:
      data = b""
    elif not isinstance(data, (bytes, bytearray)):
      data = getattr(data, "_value", b"") or b""
      if isinstance(data, str):
        data = data.encode()
    headers_out = dict(response.headers)
    if "Content-Type" not in headers_out and response.content_type:
      charset = f"; charset={response.charset}" if response.charset else ""
      headers_out["Content-Type"] = response.content_type + charset
    headers_out.pop("Content-Encoding", None)
    headers_out.pop("Content-Length", None)
    return _pack(response.status, headers_out, bytes(data))

  return _pack(501, {"Content-Type": "application/json"},
               b'{"ok": false, "error": "streaming responses are not available in the demo"}')


def _pack(status: int, headers: dict, body: bytes) -> dict:
  return {"status": int(status), "headers": {str(k): str(v) for k, v in headers.items()}, "body": body}
