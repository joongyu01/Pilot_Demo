"""Bluetooth remotes without a radio, so the upstream Bluetooth dialog can be explored.

On the device the dialog talks to BlueZ over D-Bus (jeepney) and reads the status file
of the input daemon. The browser has neither, so the dialog only said "adapter
unavailable". Every upstream handler and the real Bluez class (snapshot, scan, pairing
state, prompt checks) still run. Only the D-Bus calls are answered by an in-memory
adapter whose scan finds sample devices, and the daemon status reads "parked". Nothing
here reaches a vehicle: in this demo no input daemon exists to turn a mapping into a
command.
"""
import asyncio
import os
import tempfile
import time
import types
import uuid
from pathlib import Path, PurePath

from openpilot.selfdrive.carrot.bluetooth import b1_monitor, bluez, model
from openpilot.selfdrive.carrot.server.features import bluetooth as feature

ADAPTER = '/org/bluez/hci0'
HID = '00001124-0000-1000-8000-00805f9b34fb'
# address: (name, UUIDs, RSSI, battery %, prompt that BlueZ sends to the pairing agent)
SAMPLES = {
  'C0:DE:00:00:00:01': ('Yiser-J6', [HID], -48, 80, None),
  'C0:DE:00:00:00:02': ('BT Keyboard', [HID], -61, None, 'RequestPasskey'),
  'C0:DE:00:00:00:03': ('Phone', ['0000110a-0000-1000-8000-00805f9b34fb'], -55, None, 'RequestConfirmation'),
  'C0:DE:00:00:00:B1': ('Carrot-B1', [b1_monitor.SERVICE], -42, None, None),
}


class _Done:
  """A finished `sudo` process."""
  returncode = 0

  async def communicate(self, *_args):
    return b'', b''

  async def wait(self):
    return 0

  def kill(self):
    pass


def _mac(path):
  return path.rsplit('/dev_', 1)[-1].replace('_', ':')


class DemoBluez(bluez.Bluez):
  def __init__(self, store):
    super().__init__()
    self.store = store
    saved = model.read_json(store, {})
    saved = saved if isinstance(saved, dict) else {}
    self.radio = saved.get('radio', True)
    self.devices = saved.get('devices') if isinstance(saved.get('devices'), dict) else {}
    self.discovering = False
    self.agent_registered = True  # _pair() raises the agent prompts; no D-Bus agent exists

  def save(self):
    # BlueZ keeps bonded devices across restarts and forgets the ones it only discovered.
    model.atomic_json(self.store, {'radio': self.radio,
                                   'devices': {mac: d for mac, d in self.devices.items() if d['Paired']}})

  async def ensure(self):
    pass

  async def objects(self):
    if not self.radio:
      raise ConnectionError('org.bluez is not running (Bluetooth is off)')
    tree = {ADAPTER: {bluez.ADAPTER: {'Address': ('s', 'C0:DE:00:00:00:00'), 'Powered': ('b', True),
                                      'Discovering': ('b', self.discovering)}}}
    for mac, device in self.devices.items():
      path = f"{ADAPTER}/dev_{mac.replace(':', '_')}"
      tree[path] = {bluez.DEVICE: {key: ('v', value) for key, value in device.items() if key != 'Battery'}}
      if device.get('Battery') is not None:
        tree[path]['org.bluez.Battery1'] = {'Percentage': ('y', device['Battery'])}
    return tree

  async def call(self, path, interface, method, signature=None, body=(), reply_timeout=10):
    await self.objects()  # radio off fails like a stopped bluetoothd
    if method == 'StartDiscovery':
      self.discovering = True
      for mac, (name, uuids, rssi, battery, _prompt) in SAMPLES.items():
        self.devices.setdefault(mac, {'Address': mac, 'Name': name, 'Alias': name, 'Paired': False, 'Connected': False,
                                      'Trusted': False, 'UUIDs': uuids, 'RSSI': rssi, 'Battery': battery})
    elif method == 'StopDiscovery':
      self.discovering = False
    elif method == 'RemoveDevice':
      self.devices.pop(_mac(body[0]), None)
    elif method == 'Pair':
      await self._pair(_mac(path))
    elif method == 'Set':
      self.devices[_mac(path)][body[1]] = body[2][1]
    elif method in ('Connect', 'Disconnect'):
      await asyncio.sleep(0.5)
      self.devices[_mac(path)]['Connected'] = method == 'Connect'
    self.save()
    return ()

  async def _pair(self, mac):
    kind = SAMPLES.get(mac, (None,) * 5)[4]
    if kind:
      # What Bluez.agent_reply() does when BlueZ asks: publish the prompt, wait for /answer.
      self.answer = asyncio.get_running_loop().create_future()
      self.prompt = {'id': uuid.uuid4().hex, 'kind': kind, 'value': '482913' if kind == 'RequestConfirmation' else ''}
      try:
        value = await asyncio.wait_for(self.answer, 60)
      finally:
        self.prompt = None
      if value is False:
        raise PermissionError('org.bluez.Error.AuthenticationRejected')
    await asyncio.sleep(1)
    self.devices[mac]['Paired'] = True

  async def close(self):
    await super().close()
    self.agent_registered = True

  async def sudo(self, *command, **_kwargs):
    """The radio switch's sudo commands, reduced to the ENABLED flag they manage."""
    if '/data/bluetooth/ENABLED' in command and ('touch' in command or 'rm' in command):
      self.radio = 'touch' in command
      if not self.radio:
        self.discovering = False
        for device in self.devices.values():
          device['Connected'] = False
      self.save()
    return _Done()


def _daemon_status(client, monitor):
  """The status file the input daemon writes every 0.2 s, for a parked, disengaged car."""
  now = time.monotonic()
  learning = model.read_json(feature.RUNTIME / 'learn.json', {})
  connected = {mac for mac, device in client.devices.items() if device['Connected']}
  grabbed = sorted(mac for mac, device in model.config()['devices'].items() if device['enabled'] and mac in connected)
  model.atomic_json(feature.RUNTIME / 'status.json', {
    'time': now, 'stationary': True, 'started': False, 'grabbed': grabbed, 'errors': {}, 'recent_events': [],
    'learning': learning if isinstance(learning, dict) and learning.get('until', 0) > now else {}})
  # The B1 monitor enrolls the selected board after its encrypted pairing; here, at once.
  setup = model.read_json(feature.SETUP, {})
  if isinstance(setup, dict) and setup.get('selected') and not model.read_json(feature.ENROLLMENT, None):
    model.atomic_json(feature.ENROLLMENT, {'address': setup['selected']})
  monitor.state, monitor.firmware_state = 'demo (no board)', 'current'


def install(app) -> None:
  # Device paths -> the demo's data dir (IndexedDB in the browser) and a temp runtime dir.
  moves = ((PurePath('/data/carrot'), Path(os.environ.get('CARROT_DATA_DIR', '/data/carrot'))),
           (PurePath('/dev/shm/carrot-bluetooth'), Path(tempfile.gettempdir()) / 'carrot-bluetooth'))
  for module in (model, b1_monitor, feature):
    for name, value in list(vars(module).items()):
      if isinstance(value, PurePath):
        for old, new in moves:
          if PurePath(value).is_relative_to(old):
            setattr(module, name, new / PurePath(value).relative_to(old))
            break
  model.config.__defaults__ = (model.CONFIG_PATH,)  # bound when model.py was imported

  client = DemoBluez(model.CONFIG_PATH.with_name('demo-bluez.json'))
  monitor = app[feature.B1]
  app[feature.CLIENT] = client

  real_runtime = feature.runtime

  def runtime():
    _daemon_status(client, monitor)
    return real_runtime()

  async def radio_enabled():
    return client.radio

  shim = types.ModuleType('asyncio')
  shim.__dict__.update(vars(asyncio))
  shim.create_subprocess_exec = client.sudo
  feature.runtime, feature.radio_enabled, feature.asyncio = runtime, radio_enabled, shim
