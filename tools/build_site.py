"""Assemble the GitHub Pages site from an upstream carrot-wip checkout.

  python tools/build_site.py --upstream <checkout> --out _site

The checkout must already have `npm run build` done in
openpilot/selfdrive/carrot/web. Only two things are precomputed here with the
real upstream code, because they need native modules the browser lacks:
the index shell (asset fingerprints) and the car list (opendbc + capnp).
Everything else runs live in the browser from backend.zip.
"""
import argparse
import ast
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEMO_SRC = os.path.join(REPO, "demo")

WEB_REL = "openpilot/selfdrive/carrot/web"
WEB_EXCLUDE = {"node_modules", "tests", "package.json", "package-lock.json", "build.mjs"}
BACKEND_DATA = (
  "openpilot/selfdrive/carrot_settings.json",
  "openpilot/selfdrive/ui/translations/languages.json",
  "openpilot/common/params_keys.h",
)
# Read by upstream if present, with a safe fallback when it is not.
BACKEND_OPTIONAL_DATA = (
  "openpilot/selfdrive/carrot/web/src/features/drive/core/content_catalog.json",
)
SERVER_PKG = "openpilot/selfdrive/carrot/server"
# selfdrive/assets is served by the device as /shared-assets/ and /sound-assets/.
# The web only draws images from it; fonts and training slides are 55 MB.
ASSETS_REL = "openpilot/selfdrive/assets"
ASSET_EXTS = (".png", ".svg", ".gif", ".jpg", ".jpeg", ".webp")
ASSET_SKIP_DIRS = ("training", "fonts")
CSS_URL = re.compile(r"""url\(\s*(['"]?)/(?!/)""")


def copy_web(upstream: str, out: str) -> None:
  web = os.path.join(upstream, WEB_REL)
  if not os.path.exists(os.path.join(web, "js", "generated", "app.js")):
    sys.exit(f"web is not built: run `npm ci && npm run build` in {web}")
  shutil.copytree(web, out, ignore=lambda d, names: [n for n in names if n in WEB_EXCLUDE and d == web])
  os.remove(os.path.join(out, "index.html"))  # served by the backend as the app shell


def copy_shared_assets(upstream: str, out: str) -> int:
  src_root = os.path.join(upstream, ASSETS_REL)
  n = 0
  for dirpath, dirs, files in os.walk(src_root):
    rel_dir = os.path.relpath(dirpath, src_root)
    if rel_dir.split(os.sep)[0] in ASSET_SKIP_DIRS:
      dirs[:] = []
      continue
    for name in files:
      if name.lower().endswith(ASSET_EXTS):
        dest = os.path.join(out, "shared-assets", rel_dir, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(dirpath, name), dest)
        n += 1
  return n


def relativize_css(out: str) -> int:
  # Pages serves this site under /<repo>/, so root-absolute url(/x) in CSS
  # would leave the site. Rewrite them relative to each stylesheet.
  count = 0
  for dirpath, _dirs, files in os.walk(out):
    for name in files:
      if not name.endswith(".css"):
        continue
      path = os.path.join(dirpath, name)
      prefix = os.path.relpath(out, dirpath).replace(os.sep, "/") + "/"
      with open(path, encoding="utf-8") as f:
        text = f.read()
      new, n = CSS_URL.subn(lambda m: f"url({m.group(1)}{prefix}", text)
      if n:
        count += n
        with open(path, "w", encoding="utf-8", newline="") as f:
          f.write(new)
  return count


def render_native(upstream: str, stage: str) -> dict:
  """Run upstream code natively for the shell and car list (subprocess keeps
  the stubs and fake Params out of this process)."""
  code = r"""
import json, os, sys, tempfile
upstream, backend, stage = sys.argv[1:4]
os.environ["DEMO_APP_ROOT"] = upstream
os.environ["DEMO_PARAMS_KEYS"] = os.path.join(upstream, "openpilot/common/params_keys.h")
os.environ["DEMO_PARAMS_DIR"] = tempfile.mkdtemp()
os.environ["CARROT_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(upstream, "opendbc_repo"))
sys.path.insert(0, backend)
import demo_backend
demo_backend._install_native_fakes()
demo_backend._import_features()
from openpilot.selfdrive.carrot.server.features import cars, static
with open(os.path.join(stage, "app-shell.html"), "w", encoding="utf-8", newline="") as f:
  f.write(static._load_index_html())
sources, makers = cars.load_supported_cars()
with open(os.path.join(stage, "cars.json"), "w", encoding="utf-8") as f:
  json.dump({"sources": [], "makers": makers}, f, ensure_ascii=False)
print(json.dumps({"makers": len(makers), "cars": sum(len(v) for v in makers.values()), "stubbed": demo_backend.STUBBED}))
"""
  res = subprocess.run(
    [sys.executable, "-c", code, upstream, os.path.join(DEMO_SRC, "backend"), stage],
    check=True, capture_output=True, text=True, encoding="utf-8",
  )
  return json.loads(res.stdout.strip().splitlines()[-1])


def _module_file(upstream: str, module: str) -> str | None:
  base = os.path.join(upstream, *module.split("."))
  for candidate in (base + ".py", os.path.join(base, "__init__.py")):
    if os.path.isfile(candidate):
      return os.path.normpath(candidate)
  return None


def _module_name(upstream: str, path: str) -> str:
  rel = os.path.relpath(path, upstream).replace(os.sep, "/")[:-3]
  return rel[:-len("/__init__")].replace("/", ".") if rel.endswith("/__init__") else rel.replace("/", ".")


def import_closure(upstream: str) -> list[str]:
  """Every upstream file the server package can import, including imports
  inside functions (handlers import lazily), found by walking the AST."""
  seeds = []
  for dirpath, _dirs, files in os.walk(os.path.join(upstream, SERVER_PKG)):
    if os.sep + "tests" in dirpath:
      continue
    seeds += [os.path.normpath(os.path.join(dirpath, f)) for f in files if f.endswith(".py")]

  seen: set[str] = set()
  queue = list(seeds)
  while queue:
    path = queue.pop()
    if path in seen:
      continue
    seen.add(path)
    name = _module_name(upstream, path)
    package = name if path.endswith("__init__.py") else name.rpartition(".")[0]
    # parent packages must ship too, or the import system cannot reach us
    parts = name.split(".")
    for i in range(1, len(parts)):
      parent = _module_file(upstream, ".".join(parts[:i]))
      if parent:
        queue.append(parent)
    with open(path, encoding="utf-8") as f:
      tree = ast.parse(f.read(), path)
    for node in ast.walk(tree):
      targets = []
      if isinstance(node, ast.Import):
        targets = [a.name for a in node.names]
      elif isinstance(node, ast.ImportFrom):
        if node.level:
          anchor = package.split(".")
          anchor = anchor[:len(anchor) - (node.level - 1)]
          mod = ".".join(anchor + ([node.module] if node.module else []))
        else:
          mod = node.module or ""
        targets = [mod] + [f"{mod}.{a.name}" for a in node.names]
      for target in targets:
        if not target.startswith("openpilot."):
          continue
        found = _module_file(upstream, target)
        if found and found not in seen:
          queue.append(found)
  return sorted(seen)


def build_backend_zip(upstream: str, stage: str, dest: str) -> int:
  n = 0
  with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    def add(src, arc):
      nonlocal n
      z.write(src, arc)
      n += 1
    for rel in BACKEND_DATA:
      add(os.path.join(upstream, rel), rel)
    for rel in BACKEND_OPTIONAL_DATA:
      if os.path.isfile(os.path.join(upstream, rel)):
        add(os.path.join(upstream, rel), rel)
    for path in import_closure(upstream):
      add(path, os.path.relpath(path, upstream).replace(os.sep, "/"))
    for name in os.listdir(os.path.join(DEMO_SRC, "backend")):
      if name.endswith(".py"):
        add(os.path.join(DEMO_SRC, "backend", name), f"demo_backend/{name}")
    for name in ("app-shell.html", "cars.json"):
      add(os.path.join(stage, name), f"demo/{name}")
  return n


def git_meta(upstream: str) -> dict:
  def git(*args):
    try:
      return subprocess.run(["git", "-C", upstream, *args], check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
      return ""
  return {
    "commit": os.environ.get("UPSTREAM_SHA") or git("rev-parse", "HEAD"),
    "commitDate": os.environ.get("UPSTREAM_DATE") or git("log", "-1", "--format=%cI"),
    "subject": os.environ.get("UPSTREAM_SUBJECT") or git("log", "-1", "--format=%s"),
    "repo": os.environ.get("UPSTREAM_REPO", "ajouatom/openpilot"),
    "branch": os.environ.get("UPSTREAM_BRANCH", "carrot-wip"),
  }


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--upstream", required=True)
  ap.add_argument("--out", required=True)
  args = ap.parse_args()
  upstream, out = os.path.abspath(args.upstream), os.path.abspath(args.out)

  if os.path.exists(out):
    shutil.rmtree(out)
  copy_web(upstream, out)
  css_rewrites = relativize_css(out)
  shared_assets = copy_shared_assets(upstream, out)

  demo_out = os.path.join(out, "_demo")
  os.makedirs(demo_out)
  native = render_native(upstream, demo_out)
  files = build_backend_zip(upstream, demo_out, os.path.join(demo_out, "backend.zip"))
  for name in ("app-shell.html", "cars.json"):
    os.remove(os.path.join(demo_out, name))

  meta = git_meta(upstream)
  meta["builtAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
  meta["demoCommit"] = os.environ.get("DEMO_SHA", "")
  meta["demoRepo"] = os.environ.get("GITHUB_REPOSITORY", "")
  with open(os.path.join(demo_out, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

  for name in ("boot.js", "backend-worker.js", "demo.css"):
    shutil.copy(os.path.join(DEMO_SRC, name), os.path.join(demo_out, name))
  with open(os.path.join(DEMO_SRC, "loader.html"), encoding="utf-8") as f:
    loader = f.read()
  with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="") as f:
    f.write(loader
            .replace("__DEMO_META__", json.dumps(meta, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__DEMO_VERSION__", (meta["commit"] or "dev")[:12]))
  shutil.copy(os.path.join(DEMO_SRC, "404.html"), os.path.join(out, "404.html"))
  open(os.path.join(out, ".nojekyll"), "w").close()
  if os.path.isfile(os.path.join(upstream, "LICENSE")):
    shutil.copy(os.path.join(upstream, "LICENSE"), os.path.join(out, "LICENSE-openpilot.txt"))

  print(json.dumps({"commit": meta["commit"][:8], "css_url_rewrites": css_rewrites, "shared_assets": shared_assets,
                    "backend_files": files, **native}, ensure_ascii=False))


if __name__ == "__main__":
  main()
