"""截一张液态玻璃主题的界面图（改动后目视对比用）。

用法：python tests/shot_glass.py [输出文件]
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ["RS_CONFIG_DIR"] = os.path.join(HERE, "_cfg_shot")
os.makedirs(os.environ["RS_CONFIG_DIR"], exist_ok=True)

import tkinter as tk  # noqa: E402

from PIL import ImageGrab  # noqa: E402

from resource_sniffer import theme  # noqa: E402
from resource_sniffer.gui import MainWindow  # noqa: E402

OUT = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(ROOT, "screenshot-glass2.png"))

# 必须先声明 DPI 感知，否则 Tk 看到的是被系统缩放的虚拟分辨率，
# 逻辑坐标和屏幕物理像素对不上，截出来的图会偏。
theme.enable_dpi_awareness()


def pump(seconds: float = 0.25) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.update_idletasks()
        app.update()
        time.sleep(0.02)


app = MainWindow()
app.update_idletasks()
app.cfg.theme_preset = "glass"
app._apply_preferences(app.cfg)
try:
    app.geometry("1400x840+30+20")
except Exception:
    pass
pump(2.4)

try:
    app.lift()
    app.attributes("-topmost", True)
    app.focus_force()
except Exception:
    pass
pump(1.0)

probe = tk.Frame(app, width=1, height=1, bg=theme.BG)
probe.place(x=0, y=0, width=1, height=1)
app.update_idletasks()

# 高 DPI 下 Tk 的坐标是逻辑像素、屏幕截图是物理像素，按屏幕宽度比例换算
factor = (ctypes.windll.user32.GetSystemMetrics(0)
          / max(1, app.winfo_screenwidth()))
width, height = app.winfo_width(), app.winfo_height()
x = int(probe.winfo_rootx() * factor)
y = int(probe.winfo_rooty() * factor)
image = ImageGrab.grab(
    bbox=(x, y, x + int(width * factor), y + int(height * factor)))
image.save(OUT)
print(f"saved {OUT} {image.size[0]}x{image.size[1]} (factor {factor:.2f})")
app.destroy()
