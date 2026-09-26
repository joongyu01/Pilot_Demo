// End-to-end check of the built site in a real Chromium, served the way
// GitHub Pages serves it (under /<repo>/, nothing at the root).
//
//   node tests/e2e.mjs [_site]
//
// CI deploys only when this passes, so an upstream change that breaks the
// demo leaves the previous good build online.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { chromium } from "playwright";

const SITE = resolve(process.argv[2] || "_site");
const PREFIX = process.env.DEMO_TEST_PREFIX || "/Pilot_Demo/";
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".json": "application/json", ".zip": "application/zip", ".svg": "image/svg+xml",
  ".png": "image/png", ".gif": "image/gif", ".jpg": "image/jpeg", ".webp": "image/webp",
  ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
};

const server = createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (!path.startsWith(PREFIX)) return void res.writeHead(404).end();
  let file = normalize(join(SITE, path.slice(PREFIX.length)));
  if (!file.startsWith(SITE)) return void res.writeHead(403).end();
  try {
    if ((await stat(file)).isDirectory()) file = join(file, "index.html");
    res.writeHead(200, { "Content-Type": TYPES[extname(file)] || "application/octet-stream" });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;

const failures = [];
function check(cond, label) {
  console.log(`${cond ? "ok  " : "FAIL"} ${label}`);
  if (!cond) failures.push(label);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 700 }, locale: "ko-KR" });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
const leaked = [];
page.on("request", (req) => {
  const url = new URL(req.url());
  if (url.origin === origin && !url.pathname.startsWith(PREFIX)) leaked.push(url.pathname);
});

async function boot() {
  const t0 = Date.now();
  await page.goto(origin + PREFIX);
  await page.waitForSelector("#carrotDemoBadge", { timeout: 120_000 });
  return Date.now() - t0;
}

try {
  const ms = await boot();
  check(true, `demo boots (${(ms / 1000).toFixed(1)}s)`);
  check(await page.title() === "CarrotPilot", "upstream app shell is rendered");
  await page.waitForSelector(".intro-logo", { timeout: 15_000 }).catch(() => {});
  check(await page.locator(".intro-logo").count() > 0, "fresh visitor gets the upstream intro");
  await page.waitForFunction(() => {
    const logo = document.querySelector(".intro-logo");
    return logo?.complete && logo.naturalWidth > 0;
  }, null, { timeout: 15_000 });
  check(true, "intro logo asset loads at the configured base path");

  const api = (path, body) => page.evaluate(async ([p, b]) => {
    const r = await fetch(p, b === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b),
    });
    return { status: r.status, json: await r.json().catch(() => null) };
  }, [path, body]);

  const snap = await api("/api/settings/snapshot");
  const items = Object.values(snap.json?.settings?.items_by_group || {}).flat();
  check(snap.status === 200 && items.length > 100, `settings snapshot through the page (${items.length})`);

  await api("/api/intro/complete", {});
  await api("/api/param_set", { name: "AutoEngage", value: 2, source: "e2e" });
  await boot();
  await page.click("#btnSetting");
  await page.waitForFunction(() => document.body.innerText.includes("AutoEngage"), null, { timeout: 30_000 });
  check(true, "settings page lists upstream params");

  const persisted = await api("/api/params_bulk?names=AutoEngage");
  check(persisted.json?.values?.AutoEngage === 2, "values persist across reloads (IndexedDB)");

  // A backend file download must stay on the page and produce a file.
  const download = page.waitForEvent("download", { timeout: 20_000 }).catch(() => null);
  await page.evaluate(() => { window.location.href = "/download/nope.json"; });
  await page.waitForTimeout(500);
  check(new URL(page.url()).pathname === PREFIX, "script navigation to a backend file stays in the demo");
  await download;

  // Shared settings: schema + index load, and a preset applies through the
  // upstream restore endpoints.
  await page.click('#carrotDemoBadge [data-act="load"]');
  await page.waitForSelector(".cds-list", { timeout: 15_000 });
  check(true, "설정 불러오기(web) opens");
  const shared = await page.evaluate(async () => {
    const schema = await (await fetch("shared/schema.json")).json();
    const key = Object.keys(schema.settings).find((k) => typeof schema.settings[k].max === "number"
      && schema.settings[k].max > schema.settings[k].default);
    const values = { [key]: String(schema.settings[key].max) };
    const r = await fetch("/api/params_restore_json", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }),
    });
    const bulk = await (await fetch(`/api/params_bulk?names=${key}`)).json();
    return { settings: Object.keys(schema.settings).length, cars: schema.cars.length, key, ok: r.ok,
             applied: String(bulk.values[key]) === values[key] };
  });
  check(shared.settings > 150 && shared.cars > 100, `share schema (${shared.settings} settings, ${shared.cars} cars)`);
  check(shared.ok && shared.applied, `preset values apply through params_restore_json (${shared.key})`);
  await page.keyboard.press("Escape");

  const outbound = [];
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (url.origin !== origin && !url.hostname.endsWith("jsdelivr.net")) outbound.push(url.href);
  });
  await page.click("#btnTools");
  await page.waitForTimeout(1500);
  check(outbound.length === 0, `no third-party requests (${outbound.slice(0, 3).join(", ")})`);
} catch (err) {
  check(false, `unexpected: ${err.message}`);
} finally {
  const unique = [...new Set(leaked)];
  check(unique.length === 0, `no requests escape the site path (${unique.slice(0, 5).join(", ")})`);
  const errs = pageErrors.filter((e) => !/ResizeObserver loop/.test(e));
  check(errs.length === 0, `no uncaught page errors${errs.length ? ": " + errs.slice(0, 3).join(" | ") : ""}`);
  await browser.close();
  server.close();
}

if (failures.length) {
  console.log(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nall e2e checks passed");
