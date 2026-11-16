"""Boot backend.zip natively, the way the browser does, and check the endpoints
the demo depends on. Exits non-zero on failure so CI keeps the last good site.

  python tools/smoke_backend.py _site/_demo/backend.zip
"""
import asyncio
import json
import os
import sys
import tempfile
import zipfile

zip_path = sys.argv[1]
root = tempfile.mkdtemp(prefix="demoapp_")
zipfile.ZipFile(zip_path).extractall(root)
data = tempfile.mkdtemp(prefix="demodata_")
os.environ.update(
  DEMO_APP_ROOT=root,
  DEMO_PARAMS_DIR=os.path.join(data, "params", "d"),
  CARROT_DATA_DIR=os.path.join(data, "carrot"),
  DEMO_PARAMS_KEYS=os.path.join(root, "openpilot/common/params_keys.h"),
)
sys.path.insert(0, os.path.join(root, "demo_backend"))
sys.modules["aiohttp.test_utils"] = None  # Pyodide's aiohttp has no test_utils

import demo_backend  # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str) -> None:
  print(("ok   " if cond else "FAIL ") + label)
  if not cond:
    failures.append(label)


async def call(method: str, path: str, body=None):
  raw = json.dumps(body).encode() if body is not None else None
  r = await demo_backend.dispatch(method, path, {"Content-Type": "application/json"}, raw)
  text = r["body"].decode("utf-8", "replace")
  try:
    payload = json.loads(text)
  except ValueError:
    payload = None
  return r["status"], payload, text


def soft(cond: bool, label: str) -> None:
  # The Bluetooth dialog is an extra: if upstream changes it, warn in the run summary
  # but keep deploying the rest of the latest code.
  print(("ok   " if cond else "::warning::bluetooth demo: ") + label)


async def bluetooth() -> None:
  remote, phone, board = "C0:DE:00:00:00:01", "C0:DE:00:00:00:03", "C0:DE:00:00:00:B1"

  async def state():
    return (await call("GET", "/api/bluetooth"))[1] or {}

  async def until(pred, seconds=5.0):
    for _ in range(int(seconds / 0.1)):
      s = await state()
      if pred(s):
        return s
      await asyncio.sleep(0.1)
    return await state()

  def device(s, mac):
    return next((d for d in s.get("devices", []) if d["address"] == mac), {})

  s = await state()
  soft(s.get("available") and s.get("radioEnabled") and s["runtime"]["stationary"], "dialog is ready to configure")
  status, _, text = await call("POST", "/api/bluetooth/scan", {})
  s = await state()
  soft(status == 200 and {"Yiser-J6", "Carrot-B1"} <= {d["name"] for d in s.get("devices", [])}, f"scan lists sample devices {text[:80]}")

  await call("POST", "/api/bluetooth/pair", {"address": remote})
  s = await until(lambda s: device(s, remote).get("connected"))
  soft(device(s, remote).get("paired") and device(s, remote).get("connected"), "remote pairs and connects")
  mapping = s.get("defaults", {})
  status, _, text = await call("POST", "/api/bluetooth/device-config", {"address": remote, "device": {
    "name": "Yiser-J6", "profile": "yiser-j6", "enabled": True, "mapping": mapping}})
  s = await state()
  soft(status == 200 and s["config"]["devices"].get(remote, {}).get("mapping") == mapping, f"mapping saves {text[:80]}")
  soft(remote in s["runtime"].get("grabbed", []), "enabled mapping shows the remote as captured")
  status, _, _ = await call("POST", "/api/bluetooth/learn", {"address": remote, "enabled": True})
  soft(status == 200 and (await state())["runtime"].get("learning", {}).get("address") == remote, "key test mode starts")

  await call("POST", "/api/bluetooth/pair", {"address": phone})
  s = await until(lambda s: s.get("prompt"))
  prompt = s.get("prompt") or {}
  soft(prompt.get("kind") == "RequestConfirmation", "phone asks for a pairing confirmation")
  await call("POST", "/api/bluetooth/answer", {"id": prompt.get("id"), "value": True})
  s = await until(lambda s: device(s, phone).get("paired"))
  soft(device(s, phone).get("paired") and not s.get("prompt"), "confirming the prompt pairs the phone")

  status, _, _ = await call("POST", "/api/bluetooth/b1-register", {"address": board})
  s = await state()
  soft(status == 200 and (s.get("b1") or {}).get("address") == board, "Carrot-B1 registers for auto-connect")

  await call("POST", "/api/bluetooth/radio", {"enabled": False})
  s = await state()
  soft(not s.get("radioEnabled") and not s.get("available"), "Bluetooth off makes the adapter unavailable")
  await call("POST", "/api/bluetooth/radio", {"enabled": True})
  s = await state()
  soft(s.get("radioEnabled") and device(s, remote).get("paired"), "Bluetooth on keeps paired devices")


async def main() -> None:
  info = demo_backend.boot()
  print("stubbed:", ", ".join(info["stubbed"]))
  check(info["has_params"], "Params backend active")

  status, _, html = await call("GET", "/")
  check(status == 200 and "__CARROT_BOOTSTRAP__" in html and "carrotAssetManifest" in html, "GET / renders the app shell")

  status, snap, _ = await call("GET", "/api/settings/snapshot")
  groups = (snap or {}).get("settings", {}).get("items_by_group", {})
  items = sum(len(v) for v in groups.values())
  check(status == 200 and items > 100, f"settings snapshot ({items} items)")

  status, cars, _ = await call("GET", "/api/cars")
  n_cars = sum(len(v) for v in (cars or {}).get("makers", {}).values())
  check(status == 200 and n_cars > 100, f"car list ({n_cars} cars)")

  status, intro, _ = await call("GET", "/api/intro/state")
  check(status == 200 and (intro or {}).get("shouldShow") is True, "fresh device shows the intro")

  name = next(i["name"] for items_ in groups.values() for i in items_
              if isinstance(i.get("min"), int) and isinstance(i.get("max"), int) and i["max"] > i["min"] + 1)
  status, res, _ = await call("POST", "/api/param_set", {"name": name, "value": 10 ** 6, "source": "smoke"})
  status2, bulk, _ = await call("GET", f"/api/params_bulk?names={name}")
  meta = next(i for items_ in groups.values() for i in items_ if i["name"] == name)
  check(status == 200 and (bulk or {}).get("values", {}).get(name) == meta["max"], f"param_set clamps {name} to max")

  status, changes, _ = await call("GET", "/api/param_changes")
  check(status == 200 and any(c.get("name") == name for c in (changes or {}).get("changes", [])), "change history records the write")

  status, prof, _ = await call("POST", "/api/setting_profiles", {"name": "smoke"})
  check(status == 200 and (prof or {}).get("profile", {}).get("id"), "profile create")

  status, _, _ = await call("GET", "/api/nope")
  check(status == 404, "unknown route is 404")

  print("bluetooth:", info.get("bluetooth"))
  try:
    await bluetooth()
  except Exception as e:
    soft(False, f"bluetooth flow raised {type(e).__name__}: {e}")

  if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
  print("\nall backend checks passed")


asyncio.run(main())
