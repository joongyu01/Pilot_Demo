/* Shared settings for the demo: "설정 불러오기(web)" and "내 설정 올리기".
 *
 * Presets are published by the share bot (tools/share_settings.py) from
 * GitHub issues. Loading one goes through the upstream restore endpoints
 * (/api/params_restore_preview → /api/params_restore_json), the same path the
 * Tools QR restore uses, so the diff and the write are the real server logic.
 */
(() => {
  "use strict";

  const DEMO = window.__CARROT_DEMO__;
  const META = window.__CARROT_DEMO_META__ || {};
  const BASE = DEMO.BASE;
  const ISSUE_URL_LIMIT = 7800;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let schemaPromise = null;
  function loadSchema() {
    schemaPromise ||= fetch(`${BASE}shared/schema.json?v=${(META.commit || "").slice(0, 12)}`).then((r) => {
      if (!r.ok) throw new Error(`schema HTTP ${r.status}`);
      return r.json();
    });
    return schemaPromise;
  }

  /* ── same rules as tools/share_settings.py ───────────── */
  function normalize(value) {
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number") return value;
    const text = String(value ?? "").trim();
    if (/^(true|false)$/i.test(text)) return text.toLowerCase() === "true" ? 1 : 0;
    if (text !== "" && Number.isFinite(Number(text))) return Number(text);
    return text;
  }

  function cleanSettings(raw, schema) {
    const kept = {};
    const dropped = [];
    const invalid = [];
    for (const [key, value] of Object.entries(raw || {})) {
      const meta = schema.settings[key];
      if (!meta) { dropped.push(key); continue; }
      const v = normalize(value);
      if (typeof meta.min === "number" && typeof meta.max === "number"
          && (typeof v !== "number" || v < meta.min || v > meta.max)) {
        invalid.push(`${key}=${value}`);
        continue;
      }
      if (v === normalize(meta.default)) continue;
      kept[key] = String(v);
    }
    return { kept: Object.fromEntries(Object.entries(kept).sort()), dropped: dropped.sort(), invalid };
  }

  // Preset values sit on top of the defaults, so loading one reproduces that
  // person's setup instead of mixing it with whatever the visitor had.
  function fullValues(preset, schema) {
    const out = {};
    for (const [key, meta] of Object.entries(schema.settings)) {
      if (meta.default !== null && meta.default !== undefined) out[key] = String(normalize(meta.default));
    }
    return Object.assign(out, preset.values || {});
  }

  function title(schema, key) {
    const meta = schema.settings[key];
    return meta?.title && meta.title !== key ? meta.title : key;
  }

  function todayKst() {
    return new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  }

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  function toast(message, tone) {
    if (typeof window.showAppToast === "function") window.showAppToast(message, tone);
  }

  /* ── modal shell ─────────────────────────────────────── */
  function openModal(heading) {
    document.getElementById("cdsOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "cdsOverlay";
    overlay.className = "cds-overlay";
    overlay.innerHTML = `
      <div class="cds-modal" role="dialog" aria-modal="true" aria-label="${esc(heading)}">
        <header class="cds-head"><strong>${esc(heading)}</strong><button type="button" class="cds-x" aria-label="닫기">×</button></header>
        <div class="cds-body"></div>
      </div>`;
    const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    overlay.querySelector(".cds-x").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    return { body: overlay.querySelector(".cds-body"), close };
  }

  /* ── 설정 불러오기(web) ──────────────────────────────── */
  async function openLoad() {
    const modal = openModal("설정 불러오기 (web)");
    modal.body.innerHTML = `<p class="cds-muted">불러오는 중…</p>`;
    let schema, index, current;
    try {
      [schema, index, current] = await Promise.all([
        loadSchema(),
        fetch(`${BASE}shared/index.json?t=${Date.now()}`).then((r) => r.json()),
        api("/api/params_bulk?names=CarSelected3").then((j) => j.values?.CarSelected3 || "").catch(() => ""),
      ]);
    } catch (e) {
      modal.body.innerHTML = `<p class="cds-error">목록을 불러오지 못했습니다: ${esc(e.message)}</p>`;
      return;
    }
    const presets = index.presets || [];
    const makers = [...new Set(presets.map((p) => String(p.car || "").split(" ")[0]).filter(Boolean))].sort();
    modal.body.innerHTML = `
      <div class="cds-filters">
        <select class="cds-maker">
          <option value="">전체 차종</option>
          ${current ? `<option value="=${esc(current)}">내 차종 (${esc(current)})</option>` : ""}
          ${makers.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("")}
        </select>
        <input class="cds-q" type="search" placeholder="닉네임·차종·메모 검색">
      </div>
      <div class="cds-list"></div>
      <p class="cds-foot">다른 사람이 올린 설정입니다. 적용 전에 바뀌는 값을 확인하세요. <button type="button" class="cds-link" data-upload>내 설정 올리기</button></p>`;
    modal.body.querySelector("[data-upload]").addEventListener("click", () => { modal.close(); openUpload(); });
    const list = modal.body.querySelector(".cds-list");
    const makerSel = modal.body.querySelector(".cds-maker");
    const q = modal.body.querySelector(".cds-q");

    const render = () => {
      const maker = makerSel.value;
      const needle = q.value.trim().toLowerCase();
      const shown = presets.filter((p) => {
        if (maker.startsWith("=") && p.car !== maker.slice(1)) return false;
        if (maker && !maker.startsWith("=") && !String(p.car).startsWith(maker + " ")) return false;
        return !needle || [p.nickname, p.car, p.memo].join(" ").toLowerCase().includes(needle);
      });
      if (!presets.length) {
        list.innerHTML = `<p class="cds-muted">아직 공유된 설정이 없습니다. 첫 번째로 올려 보세요.</p>`;
        return;
      }
      list.innerHTML = shown.length ? shown.map((p, i) => `
        <article class="cds-item">
          <div class="cds-item-main">
            <div><strong>${esc(p.nickname)}</strong> <span class="cds-date">${esc(p.date)}</span></div>
            <div class="cds-car">${esc(p.car)}</div>
            ${p.memo ? `<div class="cds-memo">${esc(p.memo)}</div>` : ""}
            <div class="cds-muted">기본값과 다른 설정 ${p.count}개 · <a href="${esc(p.issue)}" target="_blank" rel="noopener">@${esc(p.author)}</a></div>
          </div>
          <div class="cds-item-actions">
            <button type="button" class="cds-btn cds-primary" data-apply="${i}">미리보기·적용</button>
            <button type="button" class="cds-btn" data-json="${i}">JSON 받기</button>
          </div>
        </article>`).join("") : `<p class="cds-muted">조건에 맞는 설정이 없습니다.</p>`;
      list.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", () => preview(shown[+b.dataset.apply])));
      list.querySelectorAll("[data-json]").forEach((b) => b.addEventListener("click", () => downloadJson(shown[+b.dataset.json])));
    };
    makerSel.addEventListener("change", render);
    q.addEventListener("input", render);
    render();

    async function fetchPreset(p) {
      const r = await fetch(`${BASE}shared/${p.file}?t=${Date.now()}`);
      if (!r.ok) throw new Error(`preset HTTP ${r.status}`);
      return r.json();
    }

    async function downloadJson(p) {
      const preset = await fetchPreset(p);
      const blob = new Blob([JSON.stringify(fullValues(preset, schema), null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${String(p.car).replace(/[^\w.-]+/g, "_")}_${String(p.nickname).replace(/[^\w가-힣.-]+/g, "_")}_${p.date}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    }

    async function preview(p) {
      modal.body.innerHTML = `<p class="cds-muted">바뀌는 값을 확인하는 중…</p>`;
      let values, pv;
      try {
        values = fullValues(await fetchPreset(p), schema);
        pv = (await api("/api/params_restore_preview", { values })).preview;
      } catch (e) {
        modal.body.innerHTML = `<p class="cds-error">미리보기 실패: ${esc(e.message)}</p>`;
        return;
      }
      const changed = pv.entries.filter((e) => e.apply);
      modal.body.innerHTML = `
        <div class="cds-sum"><strong>${esc(p.nickname)}</strong> · ${esc(p.car)} · ${esc(p.date)}</div>
        <p>${changed.length ? `지금 데모 설정에서 <b>${changed.length}개</b>가 바뀝니다.` : "지금 데모 설정과 같습니다."}
           <span class="cds-muted">(같음 ${pv.summary.same} · 건너뜀 ${pv.summary.skipped + pv.summary.invalid})</span></p>
        <div class="cds-diff">${changed.map((e) => `
          <div class="cds-row"><span class="cds-k" title="${esc(e.key)}">${esc(title(schema, e.key))}</span>
          <span class="cds-v"><s>${esc(e.current)}</s> → <b>${esc(e.value)}</b></span></div>`).join("")}</div>
        <div class="cds-actions">
          <button type="button" class="cds-btn" data-back>목록</button>
          <button type="button" class="cds-btn cds-primary" data-go ${changed.length ? "" : "disabled"}>적용</button>
        </div>`;
      modal.body.querySelector("[data-back]").addEventListener("click", () => { modal.close(); openLoad(); });
      modal.body.querySelector("[data-go]").addEventListener("click", async () => {
        try {
          const j = await api("/api/params_restore_json", { values });
          const failed = new Set((j.result?.fails || []).map((f) => String(f?.key || "")));
          const restored = {};
          (j.preview?.entries || []).forEach((e) => { if (e.apply && !failed.has(e.key)) restored[e.key] = e.value; });
          // Same events the Tools QR restore sends, so open pages refresh.
          window.dispatchEvent(new CustomEvent("carrot:paramsrestored", { detail: { source: "web_preset", values: restored } }));
          Object.entries(restored).forEach(([name, value]) => {
            window.dispatchEvent(new CustomEvent("carrot:paramchange", { detail: { name, value, source: "web_preset" } }));
          });
          toast(`${p.nickname} 설정 ${Number(j.result?.ok_cnt || 0)}개를 적용했습니다`, "success");
          modal.close();
        } catch (e) {
          toast(`적용 실패: ${e.message}`, "error");
        }
      });
    }
  }

  /* ── 내 설정 올리기 ──────────────────────────────────── */
  async function openUpload() {
    const modal = openModal("내 설정 올리기");
    let schema;
    try {
      schema = await loadSchema();
    } catch (e) {
      modal.body.innerHTML = `<p class="cds-error">준비하지 못했습니다: ${esc(e.message)}</p>`;
      return;
    }
    modal.body.innerHTML = `
      <label class="cds-field"><span>설정 파일</span>
        <input type="file" accept=".json,application/json" data-file>
        <button type="button" class="cds-link" data-current>파일 대신 지금 데모 설정 쓰기</button></label>
      <div class="cds-muted" data-summary>도구 → Backup으로 받은 JSON을 고르세요.</div>
      <label class="cds-field"><span>닉네임</span><input maxlength="20" data-nick placeholder="목록에 보일 이름"></label>
      <label class="cds-field"><span>차종</span><input list="cdsCars" data-car placeholder="차량 목록에서 선택"></label>
      <datalist id="cdsCars">${schema.cars.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <label class="cds-field"><span>날짜</span><input type="date" data-date value="${todayKst()}"></label>
      <label class="cds-field"><span>메모 <small>(선택)</small></span><textarea maxlength="200" rows="2" data-memo placeholder="예: 고속도로 위주, 회생제동 3단계"></textarea></label>
      <p class="cds-note">GitHub 이슈 양식이 채워진 채로 열립니다. GitHub에 로그인한 뒤 <b>Create</b>를 누르면 봇이 확인하고 1~3분 뒤 목록에 올라갑니다. 닉네임·차종·날짜·메모와 GitHub 계정이 공개되고, 설정은 웹에서 바꿀 수 있는 값 중 기본값과 다른 것만 저장됩니다.</p>
      <div class="cds-actions"><button type="button" class="cds-btn cds-primary" data-submit disabled>GitHub에 올리기</button></div>`;
    const $ = (sel) => modal.body.querySelector(sel);
    let cleaned = null;

    function accept(raw, source) {
      cleaned = cleanSettings(raw, schema);
      const n = Object.keys(cleaned.kept).length;
      const extra = [];
      if (cleaned.dropped.length) extra.push(`웹 설정이 아닌 키 ${cleaned.dropped.length}개 제외`);
      if (cleaned.invalid.length) extra.push(`범위 밖 값 ${cleaned.invalid.length}개 제외`);
      $("[data-summary]").innerHTML = n
        ? `${esc(source)}: 공유될 설정 <b>${n}개</b>${extra.length ? ` · ${esc(extra.join(" · "))}` : ""}`
        : `<span class="cds-error">${esc(source)}: 기본값과 다른 설정이 없습니다.</span>`;
      const car = raw.CarSelected3;
      if (car && schema.cars.includes(String(car)) && !$("[data-car]").value) $("[data-car]").value = car;
      $("[data-submit]").disabled = !n;
    }

    $("[data-file]").addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const raw = JSON.parse(await file.text());
        accept(raw && typeof raw.values === "object" ? raw.values : raw, file.name);
        const m = /(20\d{2})[-_]?(\d{2})[-_]?(\d{2})/.exec(file.name);
        if (m) $("[data-date]").value = `${m[1]}-${m[2]}-${m[3]}`;
      } catch (err) {
        cleaned = null;
        $("[data-submit]").disabled = true;
        $("[data-summary]").innerHTML = `<span class="cds-error">JSON을 읽지 못했습니다: ${esc(err.message)}</span>`;
      }
    });
    $("[data-current]").addEventListener("click", async () => {
      const names = [...Object.keys(schema.settings), "CarSelected3"].join(",");
      const j = await api(`/api/params_bulk?names=${encodeURIComponent(names)}`);
      accept(j.values || {}, "지금 데모 설정");
    });

    $("[data-submit]").addEventListener("click", async () => {
      const nick = $("[data-nick]").value.trim();
      const car = $("[data-car]").value.trim();
      const date = $("[data-date]").value;
      const memo = $("[data-memo]").value.trim();
      const problems = [];
      if (!nick) problems.push("닉네임을 입력하세요");
      if (!schema.cars.includes(car)) problems.push("차종은 목록에서 고르세요");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) problems.push("날짜를 고르세요");
      if (problems.length) { $("[data-summary]").innerHTML = `<span class="cds-error">${esc(problems.join(" · "))}</span>`; return; }

      const json = JSON.stringify(cleaned.kept, null, 1);
      const params = new URLSearchParams({
        template: "share-settings.yml", title: `[설정 공유] ${nick} · ${car}`, nickname: nick, car, date, memo, settings: json,
      });
      let url = `https://github.com/${META.demoRepo}/issues/new?${params}`;
      let copied = false;
      if (url.length > ISSUE_URL_LIMIT) {
        params.delete("settings");
        url = `https://github.com/${META.demoRepo}/issues/new?${params}`;
        try { await navigator.clipboard.writeText(json); copied = true; } catch {}
      }
      window.open(url, "_blank", "noopener");
      modal.body.innerHTML = `
        <p>GitHub 이슈 양식을 새 탭에서 열었습니다. 로그인 후 <b>Create</b>를 누르세요.</p>
        ${url.includes("settings=") ? "" : copied
          ? `<p class="cds-note">설정이 길어 링크에 다 담지 못했습니다. 설정 JSON이 <b>클립보드에 복사</b>됐으니 양식의 '설정 JSON' 칸에 붙여 넣으세요.</p>`
          : `<p class="cds-note">설정이 길어 링크에 다 담지 못했습니다. 아래 내용을 '설정 JSON' 칸에 붙여 넣으세요.</p><textarea class="cds-copy" rows="6" readonly>${esc(json)}</textarea>`}
        <p class="cds-muted">봇이 확인하면 이슈에 결과 댓글이 달리고, 통과하면 1~3분 뒤 '설정 불러오기(web)'에 나타납니다.</p>`;
    });
  }

  window.CarrotDemoShare = { openLoad, openUpload, cleanSettings, normalize };
})();
