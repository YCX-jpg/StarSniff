/* 交互层：事件流渲染、飞入动画、筛选、选择、下载。
   后端通过 window.__rs.push([...]) 推事件，格式见 webapp.Api._emit。 */

(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const list = $("#list");
  const empty = $("#empty");
  const tabs = $("#tabs");
  const pill = $(".pill", tabs);

  const state = {
    items: new Map(),      // id -> {payload, el}
    selected: new Set(),
    kind: "all",
    query: "",
    busyDownload: false,
  };

  /* ------------------------------------------------------- 标签滑块 */
  function movePill(button) {
    if (!button || !pill) return;
    const box = tabs.getBoundingClientRect();
    const rect = button.getBoundingClientRect();
    pill.style.width = `${rect.width}px`;
    pill.style.transform = `translateX(${rect.left - box.left - 4}px)`;
  }

  function pickTab(kind) {
    state.kind = kind;
    const button = $$(".tab", tabs).find((tab) => tab.dataset.kind === kind);
    $$(".tab", tabs).forEach((tab) => tab.classList.toggle("is-active", tab === button));
    movePill(button);
    applyFilter();
  }

  tabs?.addEventListener("click", (event) => {
    const button = event.target.closest(".tab");
    if (button) pickTab(button.dataset.kind);
  });

  /* ------------------------------------------------------- 渲染 */
  const KIND_CLASS = { video: "v", image: "i", audio: "a", stream: "s" };

  function buildRow(payload, order) {
    const article = document.createElement("article");
    article.className = "row";
    article.dataset.id = payload.id;
    article.dataset.kind = payload.kind;
    article.style.animationDelay = `${Math.min(order, 11) * 55}ms`;

    const stamp = (payload.stamp || []).map((line) =>
      `<span>${escapeHtml(line)}</span>`).join("");

    article.innerHTML = `
      <label class="tick"><input type="checkbox"><i></i></label>
      <div class="thumb ${KIND_CLASS[payload.kind] || ""}">
        ${payload.thumb ? `<img src="${payload.thumb}" alt="">` : ""}
        ${payload.kind === "video" || payload.kind === "stream" ? '<span class="play"></span>' : ""}
        ${stamp ? `<span class="stamp">${stamp}</span>` : ""}
      </div>
      <div class="info">
        <div class="line1">
          <span class="badge ${KIND_CLASS[payload.kind] || ""}">${escapeHtml(payload.kindLabel || "")}</span>
          <h3 title="${escapeHtml(payload.name)}">${escapeHtml(payload.name)}</h3>
        </div>
        <div class="line2">${escapeHtml(payload.meta || "")}</div>
        <div class="line3">${escapeHtml(payload.url)}</div>
        <div class="bar"><i></i></div>
      </div>
      <div class="actions"><button class="ghost" data-act="open-item">打开</button></div>`;

    return article;
  }

  function escapeHtml(text) {
    return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);
  }

  function addItem(payload, order) {
    if (!payload?.id) return;
    const exists = state.items.get(payload.id);
    if (exists) {
      // 同一链接补充了更完整的信息：就地更新文案，不再飞一次
      updateRow(exists.el, payload);
      return;
    }
    const el = buildRow(payload, order);
    list.appendChild(el);
    state.items.set(payload.id, { payload, el });
    refreshEmpty();
  }

  function updateRow(el, payload) {
    const line2 = $(".line2", el);
    const line3 = $(".line3", el);
    if (line2 && payload.meta) line2.textContent = payload.meta;
    if (line3 && payload.url) line3.textContent = payload.url;
    const img = $(".thumb img", el);
    if (payload.thumb && (!img || !img.getAttribute("src"))) {
      const thumb = $(".thumb", el);
      thumb.insertAdjacentHTML("afterbegin", `<img src="${payload.thumb}" alt="">`);
    }
  }

  function removeItem(id) {
    const entry = state.items.get(id);
    if (!entry) return;
    entry.el.classList.add("is-leaving");
    setTimeout(() => entry.el.remove(), 180);
    state.items.delete(id);
    state.selected.delete(id);
    refreshEmpty();
    updateCount();
  }

  function refreshEmpty() {
    if (!empty) return;
    empty.hidden = state.items.size > 0;
  }

  /* ------------------------------------------------------- 筛选 */
  function applyFilter() {
    const query = state.query.trim().toLowerCase();
    state.items.forEach(({ payload, el }) => {
      const kindOk = state.kind === "all" || payload.kind === state.kind;
      const textOk = !query
        || (payload.name || "").toLowerCase().includes(query)
        || (payload.url || "").toLowerCase().includes(query);
      el.hidden = !(kindOk && textOk);
    });
    refreshEmpty();
  }

  /* ------------------------------------------------------- 选择 */
  function toggleRow(el, on) {
    const input = $(".tick input", el);
    const id = el.dataset.id;
    const checked = on ?? !input.checked;
    input.checked = checked;
    el.classList.toggle("is-selected", checked);
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    updateCount();
  }

  list?.addEventListener("click", (event) => {
    const row = event.target.closest(".row");
    if (!row) return;
    if (event.target.closest("[data-act]")) {
      const act = event.target.closest("[data-act]").dataset.act;
      if (act === "open-item") window.pywebview?.api?.open_item(row.dataset.id);
      return;
    }
    if (event.target.closest(".tick")) {
      // 让原生 checkbox 先切换，再同步选中态
      setTimeout(() => toggleRow(row), 0);
      return;
    }
    toggleRow(row);
  });

  /* ------------------------------------------------------- 计数 */
  function updateCount() {
    const visible = [...state.items.values()].filter(({ el }) => !el.hidden);
    const picked = visible.filter(({ el }) => el.classList.contains("is-selected"));
    $("#count").textContent = `已选 ${picked.length} 项`;
  }

  function updateCounts(counts) {
    $$(".tab", tabs).forEach((tab) => {
      const kind = tab.dataset.kind;
      const badge = $("b", tab);
      if (badge) badge.textContent = counts[kind] ?? 0;
    });
  }

  /* ------------------------------------------------------- 下载态 */
  function markDownload(id, value, text) {
    const entry = state.items.get(id);
    if (!entry) return;
    const bar = $(".bar i", entry.el);
    entry.el.classList.toggle("is-downloading", value < 100);
    entry.el.classList.toggle("is-done", value >= 100);
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
    if (text) {
      const line2 = $(".line2", entry.el);
      if (line2) {
        const base = entry.payload.meta || "";
        line2.textContent = base ? `${base} · ${text}` : text;
      }
    }
  }

  /* ------------------------------------------------------- 事件入口 */
  window.__rs = {
    push(batch) {
      let order = 0;
      for (const evt of batch || []) {
        switch (evt.type) {
          case "item":
            addItem(evt.item, order++);
            break;
          case "remove":
            removeItem(evt.id);
            break;
          case "reset":
            list.querySelectorAll(".row").forEach((el) => el.remove());
            state.items.clear();
            state.selected.clear();
            refreshEmpty();
            updateCount();
            break;
          case "log": {
            const status = $(".status");
            if (status) status.textContent = evt.text;
            break;
          }
          case "progress": {
            const bar = $("#bar");
            if (bar) bar.style.width = `${evt.value}%`;
            break;
          }
          case "counts":
            updateCounts(evt.counts || {});
            break;
          case "busy": {
            const button = $("#go");
            if (button) {
              button.textContent = evt.value ? "停止" : "开始";
              button.classList.toggle("is-stop", !!evt.value);
            }
            break;
          }
          case "dl":
            markDownload(evt.id, evt.progress ?? 0, evt.text);
            break;
          case "busy_download":
            state.busyDownload = !!evt.value;
            $("#download").textContent = evt.value ? "停止下载" : "下载";
            $("#download").classList.toggle("is-stop", !!evt.value);
            break;
          case "done":
            applyFilter();
            break;
          default:
            break;
        }
      }
    },
  };

  /* ------------------------------------------------------- 关窗动画 */
  let closing = false;

  async function closeWindow() {
    if (closing) return;
    closing = true;
    document.body.classList.add("is-closing");
    // 等动画放完再销毁窗口，否则会直接闪没
    await new Promise((resolve) => setTimeout(resolve, 190));
    try {
      await window.pywebview?.api?.close?.();
    } catch (err) {
      /* 窗口已经没了 */
    }
  }

  /* ------------------------------------------------------- 动作分发 */
  const ACTIONS = {
    settings: "settings",
    help: "help",
    folder: "choose_folder",
    open_folder: "open_folder",
    minimize: "minimize",
    toggle_max: "toggle_max",
  };

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-act]");
    if (!button) return;
    const act = button.dataset.act;

    if (act === "start") {
      const input = $("#url");
      const target = input.value.trim();
      if (!target) return input.focus();
      const busy = button.textContent.trim() === "停止";
      if (busy) await window.pywebview?.api?.stop_sniff?.();
      else await window.pywebview?.api?.start_sniff?.(target);
      return;
    }

    if (act === "all" || act === "none") {
      [...state.items.values()].forEach(({ el }) => {
        if (!el.hidden) toggleRow(el, act === "all");
      });
      return;
    }

    if (act === "download") {
      if (state.busyDownload) {
        await window.pywebview?.api?.stop_downloads?.();
        return;
      }
      const urls = [...state.selected];
      if (!urls.length) {
        const status = $(".status");
        if (status) status.textContent = "请先勾选要下载的资源（点行任意位置即可）";
        return;
      }
      await window.pywebview?.api?.download_selected?.(urls);
      return;
    }

    if (act === "close") {
      await closeWindow();
      return;
    }

    if (act === "perf") {
      setPerf(!document.documentElement.classList.contains("perf-lite"));
      return;
    }

    const method = ACTIONS[act];
    if (method && window.pywebview?.api?.[method]) {
      const value = await window.pywebview.api[method]();
      if (method === "choose_folder" && typeof value === "string") {
        $("#path").textContent = value;
      }
    }
  });

  /* ------------------------------------------------------- 输入 */
  $("#url")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#go").click();
  });

  $("#query")?.addEventListener("input", (event) => {
    state.query = event.target.value;
    applyFilter();
    updateCount();
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      event.preventDefault();
      $("#query").focus();
    }
    if (event.key === "Escape") $("#url")?.blur();
  });

  /* ------------------------------------------------------- 性能模式 */
  const PERF_KEY = "starSniff.perfLite";

  function gpuRenderer() {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl");
      if (!gl) return "";
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "";
    } catch (err) {
      return "";
    }
  }

  // 老核显（Intel HD/UHD 5xx、软件渲染）默认走性能模式
  function weakGpu(name) {
    return /HD Graphics (3|4|5)|UHD Graphics (5|6[0-2])|GMA|SwiftShader|Software/i
      .test(name || "");
  }

  const gpu = gpuRenderer();

  function setPerf(lite) {
    document.documentElement.classList.toggle("perf-lite", !!lite);
    const button = $("#perf");
    const label = $("#perf-label");
    if (button) button.classList.toggle("is-lite", !!lite);
    if (label) label.textContent = lite ? "性能模式" : "效果全开";
    localStorage.setItem(PERF_KEY, lite ? "1" : "0");
  }

  function initPerf() {
    const saved = localStorage.getItem(PERF_KEY);
    if (saved !== null) return setPerf(saved === "1");
    setPerf(weakGpu(gpu));
  }

  /* ------------------------------------------------------- 帧率采样 */
  const perf = { fps: 0, frames: 0, last: performance.now() };
  (function tick(now) {
    perf.frames += 1;
    if (now - perf.last >= 1000) {
      perf.fps = Math.round(perf.frames * 1000 / (now - perf.last));
      perf.frames = 0;
      perf.last = now;
    }
    requestAnimationFrame(tick);
  })(performance.now());

  /* ------------------------------------------------------- 事件轮询 */
  async function pollEvents() {
    const api = window.pywebview?.api;
    if (!api?.poll_events) {
      setTimeout(pollEvents, 200);
      return;
    }
    try {
      const batch = await api.poll_events();
      if (batch && batch.length) window.__rs.push(batch);
    } catch (err) {
      /* 窗口关闭时会抛，忽略 */
    }
    setTimeout(pollEvents, 45);
  }

  /* 把渲染结果回传后端，方便日志与自动化测试断言 */
  setInterval(() => {
    window.pywebview?.api?.report_state?.({
      rows: state.items.size,
      selected: state.selected.size,
      kind: state.kind,
      status: $(".status")?.textContent || "",
      gpu,
      fps: perf.fps,
      perfLite: document.documentElement.classList.contains("perf-lite"),
    });
  }, 600);

  /* ------------------------------------------------------- 启动 */
  window.addEventListener("pywebviewready", async () => {
    try {
      const info = await window.pywebview.api.init_state();
      if (info?.download_dir) $("#path").textContent = info.download_dir;
      if (info?.status) $(".status").textContent = info.status;
      updateCounts(info?.counts || {});
    } catch (err) {
      console.warn("init_state failed", err);
    }
    pollEvents();
  });

  requestAnimationFrame(() => movePill($(".tab.is-active")));
  window.addEventListener("resize", () => movePill($(".tab.is-active")));
  initPerf();
  refreshEmpty();

  /* 入场动画用了 fill-mode: both，万一动画没被触发（后台标签页、
     虚拟时间停住等），内容会一直是透明。这里兜底强制显示。 */
  setTimeout(() => {
    const app = document.querySelector(".app");
    if (app) app.style.animation = "none";
  }, 600);
})();
