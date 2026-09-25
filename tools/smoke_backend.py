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

  if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
  print("\nall backend checks passed")


asyncio.run(main())
