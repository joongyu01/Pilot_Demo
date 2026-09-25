/* Carrot Web demo backend: the upstream aiohttp server, running in Pyodide.
 * Requests arrive from boot.js as {method, path, headers, body}; each one is
 * handed to the real route handler by demo_backend.dispatch().
 * Module worker: some browsers refuse cross-origin importScripts() in workers,
 * while import() of pyodide.mjs works everywhere module workers do. */

const PYODIDE_VERSION = "314.0.7";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const DATA_DIR = "/data";

let pyodide = null;
let dispatch = null;
let syncing = null;
let syncAgain = false;

function progress(step, detail) {
  postMessage({ type: "progress", step, detail });
}

function syncfs(populate) {
  return new Promise((resolve, reject) => {
    pyodide.FS.syncfs(populate, (err) => (err ? reject(err) : resolve()));
  });
}

// Coalesce writes: one flush in flight, at most one queued behind it.
function persist() {
  if (syncing) {
    syncAgain = true;
    return syncing;
  }
  syncing = syncfs(false)
    .catch((err) => console.warn("[demo] persist failed", err))
    .finally(() => {
      syncing = null;
      if (syncAgain) {
        syncAgain = false;
        persist();
      }
    });
  return syncing;
}

async function boot({ base, version, reset }) {
  progress("runtime");
  const { loadPyodide } = await import(`${PYODIDE_URL}pyodide.mjs`);
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

  progress("packages");
  await pyodide.loadPackage(["aiohttp"], { messageCallback: () => {} });

  progress("storage");
  pyodide.FS.mkdirTree(DATA_DIR);
  pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, DATA_DIR);
  await syncfs(true);
  if (reset) {
    pyodide.runPython(`
import os, shutil
for name in os.listdir("${DATA_DIR}"):
  path = os.path.join("${DATA_DIR}", name)
  shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
`);
    await syncfs(false);
  }

  progress("backend");
  const res = await fetch(`${base}_demo/backend.zip?v=${encodeURIComponent(version)}`);
  if (!res.ok) throw new Error(`backend.zip HTTP ${res.status}`);
  pyodide.unpackArchive(await res.arrayBuffer(), "zip", { extractDir: "/app" });

  progress("server");
  pyodide.runPython("import sys; sys.path.insert(0, '/app/demo_backend')");
  const backend = pyodide.pyimport("demo_backend");
  const info = backend.boot().toJs({ dict_converter: Object.fromEntries });
  dispatch = backend.dispatch;
  await persist();
  return info;
}

async function handle({ method, path, headers, body }) {
  const pyHeaders = pyodide.toPy(headers || {});
  const pyBody = body ? pyodide.toPy(new Uint8Array(body)) : null;
  let result;
  try {
    result = await dispatch(method, path, pyHeaders, pyBody);
  } finally {
    pyHeaders.destroy();
    if (pyBody) pyBody.destroy();
  }
  const out = result.toJs({ dict_converter: Object.fromEntries });
  result.destroy();
  if (method !== "GET" && method !== "HEAD") persist();
  const bytes = out.body instanceof Uint8Array ? out.body : new Uint8Array(0);
  return { status: out.status, headers: out.headers, body: bytes.slice().buffer };
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  if (msg.type === "boot") {
    try {
      const info = await boot(msg);
      postMessage({ type: "ready", info });
    } catch (err) {
      postMessage({ type: "failed", error: String(err && err.stack || err) });
    }
    return;
  }
  if (msg.type === "request") {
    try {
      const res = await handle(msg);
      postMessage({ type: "response", id: msg.id, ...res }, [res.body]);
    } catch (err) {
      const body = new TextEncoder().encode(JSON.stringify({ ok: false, error: String(err) })).buffer;
      postMessage({ type: "response", id: msg.id, status: 500, headers: { "Content-Type": "application/json" }, body }, [body]);
    }
  }
};
