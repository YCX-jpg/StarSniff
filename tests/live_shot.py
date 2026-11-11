"""真实站点截图：用 GUI 跑指定 URL，跑完后截图。

用法：python tests/live_shot.py "<url>" [输出文件名]
需要能访问目标站点的网络（必要时通过 HTTP_PROXY/HTTPS_PROXY 环境变量）。
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("RS_CONFIG_DIR", os.path.join(HERE, "_cfg"))


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    name = sys.argv[2] if len(sys.argv) > 2 else "live.png"
    if not url:
        print("缺少 URL")
        return 2

    if sys.platform == "win32":
        try:
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    from PIL import ImageGrab

    from resource_sniffer import theme
    from resource_sniffer.gui import MainWindow

    theme.enable_dpi_awareness()
    app = MainWindow()
    app.update()

    import tkinter as tk

    probe = tk.Frame(app, width=1, height=1, bg=theme.BG)
    probe.place(x=0, y=0, width=1, height=1)

    def pump(seconds=0.2):
        end = time.time() + seconds
        while time.time() < end:
            app.update_idletasks()
            app.update()
            time.sleep(0.02)

    def grab(path):
        app.lift()
        app.attributes("-topmost", True)
        pump(0.4)
        x, y = probe.winfo_rootx(), probe.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
        print("截图:", path)
        app.attributes("-topmost", False)

    out = os.path.join(ROOT, "screenshots", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    app.url_entry.set_value(url)
    app.var_dynamic.set(False)          # 沙箱内无法启动浏览器
    app.start_sniff()
    deadline = time.time() + 120
    while app.sniffer.running and time.time() < deadline:
        pump(0.2)
    pump(1.0)
    print("结果条数:", len(app.list_view.all_items()))
    grab(out)
    app.sniffer.stop()
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
