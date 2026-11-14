/* 桥接垫片：当页面不是由 pywebview 打开（而是本地 HTTP 服务）时，
   用 fetch 伪造出同样的 window.pywebview.api 接口，app.js 一行都不用改。 */
(function () {
  if (location.protocol !== "http:") return;   // pywebview 走 file://，不拦它
  if (window.pywebview && window.pywebview.api) return;

  const NAMES = [
    "init_state", "poll_events", "report_state",
    "start_sniff", "stop_sniff",
    "download_selected", "stop_downloads",
    "open_item", "choose_folder", "open_folder",
    "close", "minimize", "toggle_max", "settings", "help",
  ];

  const call = (name) => (...args) =>
    fetch(`/api/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    }).then((resp) => (resp.ok ? resp.json() : null)).catch(() => null);

  const api = {};
  NAMES.forEach((name) => { api[name] = call(name); });

  window.pywebview = { api };

  const fire = () => window.dispatchEvent(new Event("pywebviewready"));
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fire);
  } else {
    fire();
  }
})();
