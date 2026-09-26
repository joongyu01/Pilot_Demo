/* Carrot Web live demo — page side.
 *
 * 1. Starts the backend worker (upstream server code in Pyodide).
 * 2. Routes the app's HTTP calls: /api/* and /download/* go to the worker,
 *    everything else is a static file under this site's base path.
 * 3. Asks the backend for "/" (the real index handler) and renders it.
 *
 * Carrot Web was written for a device serving it at "/", so root-absolute
 * URLs are rewritten here to the GitHub Pages project path.
 */
(() => {
  "use strict";

  const META = window.__CARROT_DEMO_META__ || {};
  const SCRIPT_URL = new URL(document.currentScript.src, location.href);
  const BASE = SCRIPT_URL.pathname.replace(/_demo\/boot\.js$/, "");
  const ORIGIN = location.origin;
  const VERSION = (META.commit || "dev").slice(0, 12);
  const BACKEND_PREFIXES = ["/api/", "/download/"];
  const RESET_FLAG = "carrotDemoReset";

  const nativeFetch = window.fetch.bind(window);
  const NativeWorker = window.Worker;
  const NativeWebSocket = window.WebSocket;

  /* ── URL routing ─────────────────────────────────────── */
  // Returns the app-relative path ("/api/x?y") of a same-origin URL, or null.
  function appPath(raw) {
    let url;
    try {
      url = new URL(String(raw), location.href);
    } catch {
      return null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.origin !== ORIGIN) return null;
    const path = url.pathname.startsWith(BASE) ? "/" + url.pathname.slice(BASE.length) : url.pathname;
    return path + url.search + url.hash;
  }

  function isBackend(path) {
    return BACKEND_PREFIXES.some((p) => path.startsWith(p));
  }

  // The device serves selfdrive/assets under both names; the site has one copy.
  const PATH_ALIASES = [["/sound-assets/", "/shared-assets/"]];

  function siteUrl(raw) {
    let path = appPath(raw);
    if (path === null) return String(raw);
    for (const [from, to] of PATH_ALIASES) {
      if (path.startsWith(from)) path = to + path.slice(from.length);
    }
    return BASE + path.replace(/^\//, "");
  }

  /* ── backend worker channel ──────────────────────────── */
  const pending = new Map();
  let nextId = 1;
  let worker = null;
  let resolveReady;
  let rejectReady;
  const ready = new Promise((res, rej) => {
    resolveReady = res;
    rejectReady = rej;
  });

  async function backendFetch(method, path, headers, body) {
    await ready;
    const id = nextId++;
    return new Promise((resolve) => {
      pending.set(id, resolve);
      const transfer = body ? [body] : [];
      worker.postMessage({ type: "request", id, method, path, headers, body }, transfer);
    });
  }

  function toResponse(msg) {
    const noBody = msg.status === 204 || msg.status === 304 || (msg.status >= 100 && msg.status < 200);
    return new Response(noBody ? null : msg.body, { status: msg.status, headers: msg.headers });
  }

  /* ── fetch / XHR / WebSocket / Worker shims ──────────── */
  window.fetch = async function demoFetch(input, init) {
    const request = new Request(input, init);
    const path = appPath(request.url);
    if (path !== null && isBackend(path)) {
      const headers = {};
      request.headers.forEach((v, k) => { headers[k] = v; });
      const body = request.method === "GET" || request.method === "HEAD" ? null : await request.arrayBuffer();
      return toResponse(await backendFetch(request.method, path.split("#")[0], headers, body));
    }
    if (path === null) return nativeFetch(request);
    return nativeFetch(new Request(siteUrl(request.url), request));
  };

  const xhrOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function open(method, url, ...rest) {
    return xhrOpen.call(this, method, siteUrl(url), ...rest);
  };

  // No device to stream from: sockets stay "connecting", then close like an
  // unreachable host, so the app's own offline handling takes over.
  class DemoWebSocket extends EventTarget {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url, protocols) {
      super();
      const target = new URL(String(url), location.href);
      if (target.origin.replace(/^http/, "ws") !== ORIGIN.replace(/^http/, "ws")) {
        return new NativeWebSocket(url, protocols);
      }
      this.url = target.href;
      this.readyState = DemoWebSocket.CONNECTING;
      this.protocol = "";
      this.extensions = "";
      this.bufferedAmount = 0;
      this.binaryType = "blob";
      this.onopen = this.onmessage = this.onerror = this.onclose = null;
      this._timer = setTimeout(() => this._fail(), 3000);
    }

    _emit(type, init) {
      const event = type === "close" ? new CloseEvent("close", init) : new Event(type);
      const handler = this["on" + type];
      if (typeof handler === "function") handler.call(this, event);
      this.dispatchEvent(event);
    }

    _fail() {
      if (this.readyState === DemoWebSocket.CLOSED) return;
      this.readyState = DemoWebSocket.CLOSED;
      this._emit("error");
      this._emit("close", { code: 1006, reason: "demo", wasClean: false });
    }

    send() {}

    close() {
      clearTimeout(this._timer);
      if (this.readyState === DemoWebSocket.CLOSED) return;
      this.readyState = DemoWebSocket.CLOSED;
      this._emit("close", { code: 1000, reason: "", wasClean: true });
    }
  }
  window.WebSocket = DemoWebSocket;

  /* ── downloads (location.href = "/download/…", window.open) ── */
  function filenameFrom(headers, path) {
    const cd = headers["Content-Disposition"] || headers["content-disposition"] || "";
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    return m ? decodeURIComponent(m[1]) : path.split("?")[0].split("/").pop() || "download";
  }

  async function downloadFromBackend(path) {
    const msg = await backendFetch("GET", path.split("#")[0], {}, null);
    if (msg.status !== 200) {
      alert(`데모 서버: ${msg.status} ${new TextDecoder().decode(msg.body).slice(0, 200)}`);
      return;
    }
    const type = msg.headers["Content-Type"] || msg.headers["content-type"] || "application/octet-stream";
    const url = URL.createObjectURL(new Blob([msg.body], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filenameFrom(msg.headers, path);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  // Script navigations to backend files would leave the demo (and 404 on
  // Pages). The Navigation API lets us turn them into in-page downloads.
  if (window.navigation && typeof window.navigation.addEventListener === "function") {
    window.navigation.addEventListener("navigate", (event) => {
      const path = appPath(event.destination.url);
      if (path === null || !isBackend(path) || !event.cancelable || event.hashChange) return;
      event.preventDefault();
      downloadFromBackend(path);
    });
  }

  const nativeOpen = window.open.bind(window);
  window.open = function demoOpen(url, ...rest) {
    const path = url == null ? null : appPath(url);
    if (path !== null && isBackend(path)) {
      downloadFromBackend(path);
      return null;
    }
    return nativeOpen(path === null ? url : siteUrl(url), ...rest);
  };

  window.Worker = function DemoWorker(url, options) {
    return new NativeWorker(siteUrl(url), options);
  };
  window.Worker.prototype = NativeWorker.prototype;

  /* ── DOM URL attributes ──────────────────────────────── */
  const URL_ATTRS = { IMG: "src", SCRIPT: "src", SOURCE: "src", VIDEO: "src", AUDIO: "src", IFRAME: "src", LINK: "href", A: "href", IMAGE: "href" };

  function needsRewrite(value) {
    return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && siteUrl(value) !== value;
  }

  function urlAttr(el) {
    return URL_ATTRS[String(el.tagName).toUpperCase()];
  }

  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function demoSetAttribute(name, value) {
    const attr = urlAttr(this);
    const lower = String(name).toLowerCase();
    if (attr && lower === attr && needsRewrite(value)) value = siteUrl(value);
    else if (lower === "style") value = rewriteMarkup(value);
    return setAttribute.call(this, name, value);
  };
  // SVG <image> takes href through the xlink namespace as well.
  const setAttributeNS = Element.prototype.setAttributeNS;
  Element.prototype.setAttributeNS = function demoSetAttributeNS(ns, name, value) {
    const attr = urlAttr(this);
    if (attr && String(name).toLowerCase().replace(/^xlink:/, "") === attr && needsRewrite(value)) value = siteUrl(value);
    return setAttributeNS.call(this, ns, name, value);
  };

  for (const [ctor, prop] of [
    [window.HTMLImageElement, "src"], [window.HTMLScriptElement, "src"], [window.HTMLSourceElement, "src"],
    [window.HTMLMediaElement, "src"], [window.HTMLIFrameElement, "src"], [window.HTMLLinkElement, "href"],
    [window.HTMLAnchorElement, "href"],
  ]) {
    const desc = ctor && Object.getOwnPropertyDescriptor(ctor.prototype, prop);
    if (!desc || !desc.set) continue;
    Object.defineProperty(ctor.prototype, prop, {
      ...desc,
      set(value) { desc.set.call(this, needsRewrite(value) ? siteUrl(value) : value); },
    });
  }

  // Markup strings are rewritten before the parser sees them; otherwise the
  // browser has already requested the root-absolute URL by the time a
  // MutationObserver could fix it.
  const MARKUP_URL = /(\s(?:src|href|xlink:href|poster)\s*=\s*["'])(\/(?!\/)[^"']*)/gi;
  const CSS_URL = /(url\(\s*["']?)(\/(?!\/)[^"')]*)/gi;

  function rewriteMarkup(text) {
    if (typeof text !== "string" || text.indexOf("/") < 0) return text;
    return text
      .replace(MARKUP_URL, (_m, prefix, url) => (needsRewrite(url) ? prefix + siteUrl(url) : prefix + url))
      .replace(CSS_URL, (_m, prefix, url) => (needsRewrite(url) ? prefix + siteUrl(url) : prefix + url));
  }

  function wrapSetter(proto, prop, shouldRewrite) {
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {
      ...desc,
      set(value) { desc.set.call(this, shouldRewrite(this) ? rewriteMarkup(value) : value); },
    });
  }
  wrapSetter(Element.prototype, "innerHTML", () => true);
  wrapSetter(Element.prototype, "outerHTML", () => true);
  wrapSetter(Node.prototype, "textContent", (node) => node instanceof HTMLStyleElement);
  // Inline styles that carry url(): el.style.maskImage = `url("/…")`.
  // Chromium resolves el.style.<prop> through a native interceptor, not
  // prototype accessors, so the declaration itself is wrapped.
  const styleProxies = new WeakMap();
  const styleHandler = {
    get(target, key) {
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
    set(target, key, value) {
      const next = typeof value === "string" && value.includes("url(") ? rewriteMarkup(value) : value;
      return Reflect.set(target, key, next, target);
    },
  };
  for (const ctor of [window.HTMLElement, window.SVGElement]) {
    const desc = ctor && Object.getOwnPropertyDescriptor(ctor.prototype, "style");
    if (!desc || !desc.get) continue;
    Object.defineProperty(ctor.prototype, "style", {
      ...desc,
      get() {
        const real = desc.get.call(this);
        let proxy = styleProxies.get(real);
        if (!proxy) {
          proxy = new Proxy(real, styleHandler);
          styleProxies.set(real, proxy);
        }
        return proxy;
      },
      set(value) { desc.set.call(this, rewriteMarkup(value)); },
    });
  }
  const setProperty = CSSStyleDeclaration.prototype.setProperty;
  CSSStyleDeclaration.prototype.setProperty = function demoSetProperty(name, value, priority) {
    return setProperty.call(this, name, rewriteMarkup(value), priority);
  };

  const insertAdjacentHTML = Element.prototype.insertAdjacentHTML;
  Element.prototype.insertAdjacentHTML = function demoInsertAdjacentHTML(position, text) {
    return insertAdjacentHTML.call(this, position, rewriteMarkup(text));
  };

  // Anything that still slips through (parser-created nodes) is fixed here.
  function fixTree(root) {
    if (!(root instanceof Element)) return;
    const nodes = [root, ...root.querySelectorAll("img[src],script[src],source[src],video[src],audio[src],iframe[src],link[href],a[href],image[href]")];
    for (const node of nodes) {
      const attr = urlAttr(node);
      const value = attr && node.getAttribute(attr);
      if (needsRewrite(value)) setAttribute.call(node, attr, siteUrl(value));
    }
  }
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") fixTree(record.target);
      else record.addedNodes.forEach(fixTree);
    }
  });

  /* ── boot UI ─────────────────────────────────────────── */
  const STEPS = {
    runtime: "Python 런타임 내려받는 중 · Loading Python runtime",
    packages: "서버 패키지 준비 중 · Loading server packages",
    storage: "저장된 데모 설정 불러오는 중 · Restoring saved demo state",
    backend: "carrot-wip 서버 코드 내려받는 중 · Fetching carrot-wip server code",
    server: "Carrot 서버 부팅 중 · Starting Carrot server",
    render: "화면 여는 중 · Opening Carrot Web",
  };

  function setStep(step) {
    const el = document.getElementById("demoBootStep");
    if (el) el.textContent = STEPS[step] || step;
    const order = Object.keys(STEPS);
    const bar = document.getElementById("demoBootBar");
    if (bar) bar.style.width = `${Math.round(((order.indexOf(step) + 1) / (order.length + 1)) * 100)}%`;
  }

  function showFailure(error) {
    const box = document.getElementById("demoBootError");
    if (!box) return;
    box.hidden = false;
    box.querySelector("pre").textContent = String(error).slice(0, 4000);
  }

  function rewriteShell(html) {
    const banner = `<link rel="stylesheet" href="${BASE}_demo/demo.css?v=${VERSION}">`;
    return rewriteMarkup(html).replace(/<head>/i, `<head>\n  ${banner}`);
  }

  function relTime(iso) {
    const t = Date.parse(iso);
    if (!t) return "";
    const min = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (min < 60) return `${min}분 전`;
    if (min < 60 * 24) return `${Math.round(min / 60)}시간 전`;
    return `${Math.round(min / 1440)}일 전`;
  }

  function mountBadge() {
    const commit = (META.commit || "").slice(0, 7);
    const badge = document.createElement("div");
    badge.id = "carrotDemoBadge";
    badge.innerHTML = `
      <div class="cdb-tabs">
        <button type="button" class="cdb-pill" aria-expanded="false">DEMO</button>
        <button type="button" class="cdb-pill cdb-pill--share" data-act="load">설정 불러오기(web)</button>
      </div>
      <div class="cdb-panel" hidden>
        <strong>Carrot Web 라이브 데모</strong>
        <p>실제 기기 없이 <b>${META.repo || ""}@${META.branch || ""}</b> 최신 코드를 그대로 돌립니다. 바꾼 값은 이 브라우저에만 저장되고 차량에 적용되지 않습니다.</p>
        <p class="cdb-meta">upstream <a target="_blank" rel="noopener" href="https://github.com/${META.repo}/commit/${META.commit}">${commit}</a> · ${relTime(META.commitDate)}<br>${(META.subject || "").replace(/[<>&]/g, "")}</p>
        <div class="cdb-actions">
          <button type="button" data-act="upload">내 설정 올리기</button>
          <button type="button" data-act="reset">데모 초기화</button>
          ${META.demoRepo ? `<a target="_blank" rel="noopener" href="https://github.com/${META.demoRepo}">소스</a>` : ""}
        </div>
      </div>`;
    const pill = badge.querySelector(".cdb-pill");
    let sharePromise = null;
    const withShare = (fn, options) => {
      sharePromise ||= new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = `${BASE}_demo/share.js?v=${VERSION}`;
        script.onload = () => resolve(window.CarrotDemoShare);
        script.onerror = () => { sharePromise = null; reject(new Error("share.js")); };
        document.head.appendChild(script);
      });
      return sharePromise.then((share) => {
        panel.hidden = true;
        return share[fn](options);
      }).catch((error) => {
        window.alert(`설정 화면을 열지 못했습니다. 다시 시도해 주세요.\n${error.message}`);
      });
    };
    badge.querySelector('[data-act="load"]').addEventListener("click", () => withShare("openLoad"));
    badge.querySelector('[data-act="upload"]').addEventListener("click", () => withShare("openUpload"));
    const panel = badge.querySelector(".cdb-panel");
    pill.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      pill.setAttribute("aria-expanded", String(!panel.hidden));
    });
    badge.querySelector('[data-act="reset"]').addEventListener("click", () => {
      if (!confirm("데모에서 바꾼 설정을 모두 지우고 처음 상태로 돌아갈까요?")) return;
      try { sessionStorage.setItem(RESET_FLAG, "1"); } catch {}
      location.reload();
    });
    document.body.appendChild(badge);
    mountIntroChoices(withShare);
  }

  // Demo-only extension: retain upstream navigation, restoration and cleanup.
  function mountIntroChoices(withShare, attempt = 0) {
    const intro = window.CarrotIntro;
    const shell = window.CarrotIntroShell;
    if (!intro?.get("welcome") || !shell) {
      if (attempt < 200) setTimeout(() => mountIntroChoices(withShare, attempt + 1), 50);
      return;
    }
    const ko = window.CarrotIntroLangs?.find(([code]) => code === "ko");
    if (ko) ko[2] = "안녕하세요!";
    function decorate(el) {
      el.querySelector("[data-restore]")?.remove();
      el.querySelectorAll(".intro-cyc").forEach((node) => {
        if (node.textContent === "오셨군요!") node.textContent = "안녕하세요!";
      });
      if (!el.querySelector(".demo-copyright")) {
        const footer = document.createElement("footer");
        footer.className = "demo-copyright";
        footer.innerHTML = '© 2026 <a href="https://github.com/joongyu01/Pilot_Demo" target="_blank" rel="noopener">Joongyu Shin · Pilot Demo</a><br>Based on <a href="https://github.com/ajouatom/openpilot" target="_blank" rel="noopener">CarrotPilot</a> &amp; <a href="https://github.com/commaai/openpilot" target="_blank" rel="noopener">openpilot</a>';
        el.appendChild(footer);
      }
    }
    const welcome = intro.get("welcome");
    const render = welcome.render;
    welcome.render = function(el) {
      const cleanup = render.call(this, el);
      decorate(el);
      return cleanup;
    };
    document.querySelectorAll("#introDeck .intro-col").forEach((el) => {
      if (el.querySelector("[data-lang]")) decorate(el);
    });
    intro.register({
      id: "demo-settings", flow: false,
      render(el) {
        el.innerHTML = `<h2 class="intro-title">설정은 어떻게 불러올까요?</h2>
          <div class="intro-opts">
            <button type="button" class="intro-opt" data-demo-new>새로 시작하기</button>
            <button type="button" class="intro-opt" data-demo-load>설정 불러오기(web)</button>
            <button type="button" class="intro-opt" data-demo-upload>내 설정 업로드하기(web)</button>
          </div>`;
        el.querySelector("[data-demo-new]").onclick = (event) => shell.pick(event.currentTarget, "car");
        el.querySelector("[data-demo-load]").onclick = () => withShare("openLoad", { onboarding: true });
        el.querySelector("[data-demo-upload]").onclick = () => withShare("openUpload");
        decorate(el);
      },
    });
    document.addEventListener("click", (event) => {
      const button = event.target.closest?.("#introDeck [data-lang]");
      if (!button) return;
      event.stopImmediatePropagation();
      intro.ctx.lang = button.dataset.lang;
      intro.applyLang();
      // Update the live UI as well as the server setting; saving alone leaves
      // the already-loaded settings catalog in its previous language.
      if (typeof window.setWebLanguage === "function") window.setWebLanguage(intro.ctx.lang);
      else window.CarrotIntroApi.setLanguage(intro.ctx.lang).catch(() => {});
      shell.pick(button, "demo-settings");
    }, true);
  }

  async function start() {
    let reset = false;
    try {
      reset = sessionStorage.getItem(RESET_FLAG) === "1";
      sessionStorage.removeItem(RESET_FLAG);
    } catch {}
    if (reset) {
      try { localStorage.clear(); } catch {}
    }

    worker = new NativeWorker(`${BASE}_demo/backend-worker.js?v=${VERSION}`, { type: "module" });
    worker.onmessage = (event) => {
      const msg = event.data || {};
      if (msg.type === "progress") setStep(msg.step);
      else if (msg.type === "ready") resolveReady(msg.info);
      else if (msg.type === "failed") rejectReady(new Error(msg.error));
      else if (msg.type === "response") {
        const resolve = pending.get(msg.id);
        pending.delete(msg.id);
        if (resolve) resolve(msg);
      }
    };
    worker.onerror = (event) => rejectReady(new Error(event.message || "backend worker error"));
    worker.postMessage({ type: "boot", base: BASE, version: VERSION, reset });

    const info = await ready;
    window.__CARROT_DEMO_BACKEND__ = info;
    setStep("render");
    // A server-side settings file suppresses the upstream first-run intro.
    // Seed only the browser fallback; explicit saved language still wins.
    try {
      if (!localStorage.getItem('carrot_web_lang')) localStorage.setItem('carrot_web_lang', 'ko');
    } catch {}
    const res = await backendFetch("GET", "/", { accept: "text/html" }, null);
    const html = new TextDecoder().decode(res.body);
    if (res.status !== 200) throw new Error(`index HTTP ${res.status}\n${html.slice(0, 500)}`);

    // Observe the Document node itself: it survives document.open(), so
    // markup inserted while the shell is still parsing is caught too.
    observer.observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["src", "href"] });
    document.open();
    document.write(rewriteShell(html));
    document.close();
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountBadge, { once: true });
    else mountBadge();
  }

  window.__CARROT_DEMO__ = { BASE, backendFetch, siteUrl };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => start().catch(showFailure), { once: true });
  } else {
    start().catch(showFailure);
  }
})();
