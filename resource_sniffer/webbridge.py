"""不依赖 pythonnet 的界面外壳：本地 HTTP 桥 + Edge 应用窗口。

为什么要这样：pywebview 在 Windows 上靠 pythonnet 调 .NET，打进 exe 后
``Failed to resolve Python.Runtime.Loader.Initialize``（PyInstaller 的老问题），
而 WebView2 本身并不需要 .NET。这里改成：

1. 起一个只监听 127.0.0.1 的 HTTP 服务，直接把 ``webui/`` 目录发出去；
2. 用 Edge 的 ``--app=`` 模式开一个无地址栏窗口（Chromium 内核，真 blur）；
3. 页面用 fetch 调 ``/api/<方法>``，事件用 ``/api/poll_events`` 轮询。

这样打包出来不依赖 .NET / pythonnet，Edge 在 Win10/11 上是系统自带的。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import mimetypes
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import webapp

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.join(HERE, "webui")

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_edge() -> str:
    for path in _EDGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


class _Handler(BaseHTTPRequestHandler):
    server_version = "StarSniffUi/1.0"

    def log_message(self, fmt, *args):  # noqa: D401 - 屏蔽访问日志
        return

    # ------------------------------------------------------------ 工具
    def _send(self, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(path)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            self._send(fh.read(), ctype)

    # ------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        api = self.server.api  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_file(os.path.join(WEBUI, "index.html"))
        if path == "/api/poll_events":
            payload = json.dumps(api.poll_events(), ensure_ascii=False)
            return self._send(payload.encode("utf-8"))
        target = os.path.normpath(os.path.join(WEBUI, path.lstrip("/")))
        if not target.startswith(WEBUI):
            self.send_error(403)
            return
        return self._serve_file(target)

    # ------------------------------------------------------------ POST
    def do_POST(self):  # noqa: N802
        api = self.server.api  # type: ignore[attr-defined]
        name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"[]"
        try:
            args = json.loads(raw.decode("utf-8") or "[]")
        except (ValueError, UnicodeDecodeError):
            args = []
        func = getattr(api, name, None)
        if not callable(func):
            self.send_error(404, "no such api")
            return
        try:
            result = func(*args)
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
        payload = json.dumps(result, ensure_ascii=False, default=str)
        self._send(payload.encode("utf-8"))


class BridgeApi(webapp.Api):
    """在 webapp.Api 之上，把“窗口操作”换成本地进程/窗口控制。"""

    def __init__(self) -> None:
        super().__init__()
        self.proc: subprocess.Popen | None = None
        self.stopping = threading.Event()

    # ---- 窗口控制
    def _hwnd(self) -> int:
        if self.proc is None:
            return 0
        pid = self.proc.pid
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            owner = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd,
                                                         ctypes.byref(owner))
            if owner.value != pid:
                return True
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            title = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
            if "StarSniff" in title.value:
                found.append(hwnd)
            return True

        ctypes.windll.user32.EnumWindows(callback, 0)
        return found[0] if found else 0

    def close(self) -> None:
        self.stopping.set()
        super().close()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def minimize(self) -> None:
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE

    def toggle_max(self) -> None:
        hwnd = self._hwnd()
        if not hwnd:
            return
        if ctypes.windll.user32.IsZoomed(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            ctypes.windll.user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE

    def choose_folder(self) -> str:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(initialdir=self.download_dir)
        root.destroy()
        if folder:
            self.download_dir = folder
            self.cfg.download_dir = folder
            webapp.cfgmod.save(self.cfg, self.base_dir)
        return self.download_dir


def run() -> None:
    """启动本地服务并用 Edge 打开界面，直到窗口关闭。"""
    edge = find_edge()
    if not edge:
        raise RuntimeError("没有找到 Edge/Chrome，无法打开网页界面")

    # 先声明 DPI 感知，否则在 150% 缩放下拿到的屏幕尺寸是虚拟值
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

    api = BridgeApi()
    port = random.randint(47000, 56000)
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.api = api  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="ui-http").start()

    profile = tempfile.mkdtemp(prefix="rs_edge_")
    url = f"http://127.0.0.1:{port}/index.html"
    args = [
        edge,
        f"--app={url}",
        f"--user-data-dir={profile}",
        # 直接最大化：布局是响应式的，省掉和系统缩放反复换算窗口尺寸
        "--start-maximized",
        # 页面按 1:1 物理像素渲染：这套界面是按 1x 设计的，
        # 让 Chromium 再乘系统缩放会把内容顶出窗口（右侧被裁）。
        "--force-device-scale-factor=1",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--disable-background-timer-throttling",
    ]
    api.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    try:
        while api.proc.poll() is None:
            time.sleep(0.3)
    finally:
        api.stopping.set()
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            api.downloader.stop()
            api.sniffer.stop()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    run()
    sys.exit(0)


if __name__ == "__main__":
    main()
