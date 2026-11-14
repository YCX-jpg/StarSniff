"""StarSniff 的 WebView2 外壳（pywebview）。

Tk 画不出真玻璃：没有 backdrop-filter、没有 GPU 合成，只能靠 Pillow 把壁纸
模糊后贴成位图，既不准也慢。这里把界面整个搬进 WebView2：壁纸是页面里的
CSS 渐变，玻璃用 ``backdrop-filter`` 真模糊，动画走合成线程，不占 CPU。

启动方式::

    python -m resource_sniffer.webapp
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import sys
import threading
import time

try:  # pragma: no cover - 打包版不带 pywebview，走 webbridge
    import webview

    FOLDER_DIALOG = webview.FOLDER_DIALOG
except Exception:  # noqa: BLE001
    webview = None
    FOLDER_DIALOG = 20      # pywebview 里 FOLDER_DIALOG 的取值

from . import config as cfgmod
from .downloader import DownloadManager, DownloadTask
from .engine import Sniffer, SniffOptions
from .models import ResourceItem
from .net import normalize_url

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "webui", "index.html")


def _data_url(data: bytes) -> str:
    if not data:
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _clean_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip(" .") or "download"


class Api:
    """暴露给 JS 的后端接口（``window.pywebview.api.*``）。"""

    def __init__(self) -> None:
        self.window = None
        self.base_dir = (os.environ.get("RS_CONFIG_DIR", "").strip()
                         or cfgmod.app_dir())
        self.cfg = cfgmod.load(self.base_dir)
        self.download_dir = (self.cfg.download_dir
                             or cfgmod.default_download_dir(self.base_dir))
        try:
            os.makedirs(self.download_dir, exist_ok=True)
        except OSError:
            self.download_dir = cfgmod.user_download_dir()

        self.items: dict[str, ResourceItem] = {}
        self.last_state: dict = {}
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._alive = threading.Event()
        self._alive.set()

        self.sniffer = Sniffer(
            on_item=self._on_item,
            on_log=self._on_log,
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_remove=self._on_remove,
        )
        self.downloader = DownloadManager(
            on_start=self._on_dl_start,
            on_progress=self._on_dl_progress,
            on_finish=self._on_dl_finish,
            segments=getattr(self.cfg, "download_segments", 4),
            max_workers=self.cfg.concurrency,
        )

    # ------------------------------------------------------------ 事件泵
    def _emit(self, payload: dict) -> None:
        self._events.put(payload)

    def poll_events(self) -> list:
        """页面定时来取事件。

        不用 ``window.evaluate_js`` 反向推：pywebview 在无边框窗口下那条路径
        会抛 “Main window failed to start”，而 JS 主动调用后端一直可靠。
        """
        batch = []
        deadline = time.monotonic() + 0.012
        while len(batch) < 80:
            try:
                batch.append(self._events.get_nowait())
            except queue.Empty:
                break
            if time.monotonic() > deadline:
                break
        return batch

    def report_state(self, state: dict) -> None:
        """页面把渲染后的统计回传给后端（测试与日志用）。"""
        self.last_state = state or {}

    # ------------------------------------------------------------ 窗口控制
    def close(self) -> None:
        self._alive.clear()
        try:
            self.sniffer.stop()
            self.downloader.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.window is not None:
            self.window.destroy()

    def minimize(self) -> None:
        self.window.minimize()

    def toggle_max(self) -> None:
        self.window.toggle_fullscreen()

    # ------------------------------------------------------------ 初始状态
    def init_state(self) -> dict:
        return {
            "download_dir": self.download_dir,
            "status": "等待开始…",
            "counts": {"all": 0, "image": 0, "audio": 0, "video": 0, "stream": 0},
        }

    # ------------------------------------------------------------ 目录
    def choose_folder(self) -> str:
        result = self.window.create_file_dialog(FOLDER_DIALOG)
        if result:
            self.download_dir = result if isinstance(result, str) else result[0]
            self.cfg.download_dir = self.download_dir
            cfgmod.save(self.cfg, self.base_dir)
        return self.download_dir

    def open_folder(self) -> None:
        try:
            os.startfile(self.download_dir)  # noqa: S606
        except OSError:
            pass

    # ------------------------------------------------------------ 嗅探
    def start_sniff(self, url: str) -> dict:
        if self.sniffer.running:
            self.sniffer.stop()
            return {"ok": False, "reason": "stopping"}

        target = normalize_url((url or "").strip())
        if not target:
            self._emit({"type": "log", "text": "网址格式不正确，请检查后重试"})
            return {"ok": False, "reason": "bad-url"}

        self.items.clear()
        self._emit({"type": "reset"})
        self._emit({"type": "log", "text": "开始嗅探…"})
        self._emit({"type": "progress", "value": 3})
        self._emit({"type": "busy", "value": True})
        cfgmod.save(self.cfg, self.base_dir)
        try:
            self.sniffer.start(target, self._options(),
                               proxy=self.cfg.proxy, cookie=self.cfg.cookie,
                               proxy_mode=self.cfg.proxy_mode)
        except Exception as exc:  # noqa: BLE001
            self._emit({"type": "log", "text": f"启动失败：{exc}"})
            self._emit({"type": "busy", "value": False})
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    def stop_sniff(self) -> None:
        try:
            self.sniffer.stop()
        except Exception:  # noqa: BLE001
            pass
        self._emit({"type": "log", "text": "正在停止…"})
        self._emit({"type": "busy", "value": False})

    def _options(self) -> SniffOptions:
        cfg = self.cfg
        return SniffOptions(
            deep=cfg.deep,
            dynamic=cfg.dynamic,
            dynamic_wait=cfg.dynamic_wait,
            scroll=cfg.scroll,
            max_items=cfg.max_items,
            min_image_side=cfg.min_image_side,
            kinds=tuple(cfg.kinds or ["image", "audio", "video", "stream"]),
            expand_manifests=True,
            workers=cfg.workers,
            browser_path=cfg.browser_path,
            headless=cfg.headless,
            timeout=cfg.timeout,
            probe_deadline=cfg.probe_deadline,
            ua_key=cfg.user_agent,
        )

    # ---- 引擎回调（工作线程）
    def _on_item(self, item: ResourceItem) -> None:
        self.items[item.url] = item
        self._emit({"type": "item", "item": self._payload(item)})

    def _on_remove(self, item: ResourceItem) -> None:
        self.items.pop(item.url, None)
        self._emit({"type": "remove", "id": item.url})

    def _on_log(self, message: str) -> None:
        self._emit({"type": "log", "text": message})

    def _on_progress(self, message: str) -> None:
        self._emit({"type": "log", "text": message})

    def _on_done(self, summary: dict) -> None:
        counts = {"all": len(self.items)}
        for item in self.items.values():
            counts[item.kind] = counts.get(item.kind, 0) + 1
        self._emit({"type": "counts", "counts": counts})
        self._emit({"type": "progress", "value": 100})
        self._emit({"type": "busy", "value": False})
        self._emit({"type": "done", "total": len(self.items)})

    def _payload(self, item: ResourceItem) -> dict:
        name = item.filename or item.url.rsplit("/", 1)[-1] or item.url
        main, sub = item.preview_lines()
        bits = []
        if item.size:
            from .mediainfo import fmt_size
            bits.append(fmt_size(item.size))
        if item.bitrate:
            from .mediainfo import fmt_bitrate
            bits.append(fmt_bitrate(item.bitrate))
        if item.extra:
            bits.append(item.extra)
        return {
            "id": item.url,
            "kind": item.kind,
            "kindLabel": item.kind_label,
            "name": name,
            "url": item.url,
            "meta": " · ".join(bits),
            "stamp": [part for part in (main, sub) if part],
            "thumb": _data_url(item.thumbnail or b""),
            "state": item.state,
        }

    # ------------------------------------------------------------ 下载
    def download_selected(self, urls: list[str]) -> dict:
        picks = [self.items[u] for u in urls if u in self.items]
        if not picks:
            self._emit({"type": "log", "text": "请先勾选要下载的资源"})
            return {"ok": False, "count": 0}
        active = self.downloader.active_urls()
        ready = [i for i in picks if i.url not in active]
        if not ready:
            self._emit({"type": "log", "text": "选中的资源都在下载队列里了"})
            return {"ok": False, "count": 0}
        for item in ready:
            item.state = "downloading"
            item.progress = 0.0
        self.downloader.enqueue(ready, self.download_dir, namer=self._name_for)
        self.downloader.start()
        self._emit({"type": "log", "text": f"开始下载 {len(ready)} 个文件"})
        self._emit({"type": "busy_download", "value": True})
        return {"ok": True, "count": len(ready)}

    def stop_downloads(self) -> None:
        self.downloader.cancel_all()
        self._emit({"type": "log", "text": "正在停止下载…"})

    def _name_for(self, item: ResourceItem) -> str:
        from .htmlparse import ensure_extension, guess_filename

        name = _clean_name(item.filename or guess_filename(item.url))
        if item.kind == "stream" and item.fmt in ("m3u8", "mpd"):
            base = os.path.splitext(name)[0] or "stream"
            name = base + (".ts" if item.fmt == "m3u8" else ".mp4")
        return ensure_extension(name, item.kind, item.fmt)

    def _on_dl_start(self, task: DownloadTask) -> None:
        self._emit({"type": "dl", "id": task.url, "state": "running",
                    "progress": 0})

    def _on_dl_progress(self, task: DownloadTask) -> None:
        self._emit({"type": "dl", "id": task.url, "state": "running",
                    "progress": round(task.progress, 1)})

    def _on_dl_finish(self, task: DownloadTask) -> None:
        text = {"done": "已下载", "error": task.error or "下载失败"}.get(
            task.state, task.state)
        self._emit({"type": "dl", "id": task.url, "state": task.state,
                    "progress": round(task.progress, 1), "text": text})
        busy = self.downloader.busy
        self._emit({"type": "busy_download", "value": bool(busy)})
        if not busy:
            self._emit({"type": "log", "text": "下载队列已清空"})

    # ------------------------------------------------------------ 设置
    def get_settings(self) -> dict:
        from . import useragents

        cfg = self.cfg
        return {
            "download_dir": self.download_dir,
            "proxy": cfg.proxy,
            "proxy_mode": cfg.proxy_mode,
            "deep": bool(cfg.deep),
            "dynamic": bool(cfg.dynamic),
            "scroll": bool(cfg.scroll),
            "workers": int(cfg.workers),
            "concurrency": int(cfg.concurrency),
            "segments": int(cfg.download_segments),
            "user_agent": cfg.user_agent,
            "auto_check_all": bool(cfg.auto_check_all),
            "use_ytdlp": bool(cfg.use_ytdlp),
            "ua_options": [[p.key, p.label] for p in useragents.PROFILES],
        }

    def save_settings(self, data: dict) -> dict:
        cfg = self.cfg
        data = data or {}

        def _int(name: str, low: int, high: int, attr: str = "") -> None:
            try:
                value = int(data.get(name))
            except (TypeError, ValueError):
                return
            setattr(cfg, attr or name, max(low, min(high, value)))

        folder = str(data.get("download_dir") or "").strip()
        if folder and os.path.isdir(folder):
            self.download_dir = folder
            cfg.download_dir = folder

        cfg.proxy = str(data.get("proxy") or "").strip()
        mode = str(data.get("proxy_mode") or "").strip()
        if mode in ("auto", "proxy", "direct"):
            cfg.proxy_mode = mode
        _int("workers", 1, 16)
        _int("concurrency", 1, 8)
        _int("segments", 1, 8, "download_segments")

        ua = str(data.get("user_agent") or "").strip()
        if ua:
            cfg.user_agent = ua

        for key in ("deep", "dynamic", "scroll", "auto_check_all", "use_ytdlp"):
            if key in data:
                setattr(cfg, key, bool(data[key]))

        cfgmod.save(cfg, self.base_dir)
        self.downloader.segments = max(1, min(8, int(cfg.download_segments or 1)))
        self._emit({"type": "log", "text": "设置已保存"})
        return self.get_settings()

    # ------------------------------------------------------------ 其它
    def open_item(self, url: str) -> None:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    def settings(self) -> None:
        self._emit({"type": "log", "text": "设置面板还在迁移中"})

    def help(self) -> None:
        self._emit({"type": "log", "text": "帮助还在迁移中"})


def build_window(api: Api):
    if webview is None:
        raise RuntimeError("没有安装 pywebview")
    return webview.create_window(
        "StarSniff 星嗅",
        url="file:///" + INDEX.replace("\\", "/"),
        js_api=api,
        width=1180, height=780,
        x=60, y=60,
        min_size=(900, 640),
        frameless=True,
        easy_drag=False,
        background_color="#eef0fb",
    )


def run() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        pass
    # 老核显（Intel HD 520 之类）默认会被 Chromium 挡在 GPU 加速之外，
    # 关掉模糊还不够，这里显式请求硬件光栅化，避免退化成软件渲染。
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--ignore-gpu-blocklist --enable-gpu-rasterization "
        "--enable-zero-copy --disable-background-timer-throttling")
    api = Api()
    window = build_window(api)
    api.window = window
    webview.start(debug=bool(os.environ.get("RS_WEBDEBUG")), gui="edgechromium")


def main() -> None:
    run()
    sys.exit(0)


if __name__ == "__main__":
    main()
