"""无边框窗口（自定义标题栏）支持。

Windows 下去掉系统标题栏，改用应用内绘制的标题栏：

* 拖动标题栏移动窗口，双击最大化/还原；
* 最小化 / 最大化 / 关闭按钮；
* 四边和四角可拖动缩放（带光标反馈，遵守最小尺寸）；
* 通过 ``WS_EX_APPWINDOW`` 让无边框窗口仍然出现在任务栏。

非 Windows 平台退化为普通 overrideredirect 窗口（仍可拖动，缩放依赖系统）。
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import Callable, Optional

from . import theme


# --------------------------------------------------------------------------
# Win32 小工具
# --------------------------------------------------------------------------
def _hwnd(window) -> int:
    try:
        import ctypes

        handle = ctypes.windll.user32.GetParent(window.winfo_id())
        return int(handle or window.winfo_id())
    except Exception:
        return 0


def _user32():
    import ctypes

    return ctypes.windll.user32


def apply_taskbar_style(window) -> None:
    """让无边框窗口出现在任务栏，并恢复最小化/关闭所需的窗口样式。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = _user32()
        hwnd = _hwnd(window)
        if not hwnd:
            return
        gwl_exstyle, gwl_style = -20, -16
        ws_ex_appwindow = 0x00040000
        ws_ex_toolwindow = 0x00000080
        ws_minimizebox = 0x00020000
        ws_sysmenu = 0x00080000
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        ex_style = get_long(hwnd, gwl_exstyle)
        style = get_long(hwnd, gwl_style)
        set_long(hwnd, gwl_exstyle,
                 (ex_style & ~ws_ex_toolwindow) | ws_ex_appwindow)
        set_long(hwnd, gwl_style, style | ws_minimizebox | ws_sysmenu)
        swp_nomove, swp_nosize, swp_nozorder, swp_framechanged = 0x2, 0x1, 0x4, 0x20
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            swp_nomove | swp_nosize | swp_nozorder
                            | swp_framechanged)
    except Exception:
        pass


def minimize(window) -> None:
    if os.name == "nt":
        try:
            _user32().ShowWindow(_hwnd(window), 6)    # SW_MINIMIZE
            return
        except Exception:
            pass
    try:
        window.iconify()
    except tk.TclError:
        pass


def work_area(window=None) -> tuple[int, int, int, int]:
    """返回窗口所在显示器的工作区（不含任务栏），物理像素。"""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            if window is not None:
                hwnd = _hwnd(window)
                monitor = user32.MonitorFromWindow(hwnd, 2)  # NEAREST
                if monitor:
                    info = MONITORINFO()
                    info.cbSize = ctypes.sizeof(MONITORINFO)
                    if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                        work = info.rcWork
                        return work.left, work.top, work.right, work.bottom
            rect = RECT()
            spi_getworkarea = 0x0030
            if user32.SystemParametersInfoW(
                    spi_getworkarea, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    return 0, 0, 0, 0


# --------------------------------------------------------------------------
# 拖动 / 最大化
# --------------------------------------------------------------------------
def begin_drag(window, event) -> None:
    if getattr(window, "_maximized", False):
        return
    try:
        window._drag_offset = (event.x_root - window.winfo_x(),
                               event.y_root - window.winfo_y())
    except tk.TclError:
        window._drag_offset = None


def do_drag(window, event) -> None:
    offset = getattr(window, "_drag_offset", None)
    if not offset or getattr(window, "_maximized", False):
        return
    try:
        x = event.x_root - offset[0]
        y = event.y_root - offset[1]
        window.geometry(f"+{max(-2000, x)}+{max(-2000, y)}")
    except tk.TclError:
        pass


def end_drag(window, _event=None) -> None:
    window._drag_offset = None


def toggle_maximize(window, on_state: Optional[Callable[[bool], None]] = None) -> bool:
    """最大化 / 还原，返回当前是否最大化。"""
    maximized = bool(getattr(window, "_maximized", False))
    try:
        if not maximized:
            window._normal_geometry = window.geometry()
            left, top, right, bottom = work_area(window)
            if right > left and bottom > top:
                width = max(200, right - left)
                height = max(120, bottom - top)
                window.geometry(f"{width}x{height}+{left}+{top}")
            else:
                try:
                    window.state("zoomed")
                except tk.TclError:
                    return maximized
            window._maximized = True
            maximized = True
        else:
            window._maximized = False
            geometry = getattr(window, "_normal_geometry", "")
            if geometry:
                window.geometry(geometry)
            maximized = False
    except tk.TclError:
        return bool(getattr(window, "_maximized", False))
    if on_state is not None:
        try:
            on_state(maximized)
        except tk.TclError:
            pass
    return maximized


# --------------------------------------------------------------------------
# 标题栏按钮
# --------------------------------------------------------------------------
def draw_round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    """画圆角矩形（避免 widgets ↔ frameless 的循环依赖）。"""
    radius = max(0, min(radius, abs(x2 - x1) // 2, abs(y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


class TrafficLight(tk.Canvas):
    """macOS 风格的红黄绿窗口按钮（液态玻璃主题）。"""

    COLORS = {"close": "#ff5f57", "min": "#febc2e", "max": "#28c840"}

    def __init__(self, master, kind: str, command: Callable,
                 bg: str = "", size: int = 14):
        super().__init__(master, width=size, height=size, bg=bg or theme.BG,
                         highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=0)
        self._kind = kind
        self._command = command
        self._bg = bg or theme.BG
        self._size = size
        self._hover = False
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _on_release(self, event) -> None:
        if 0 <= event.x <= self._size and 0 <= event.y <= self._size:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        pad = 1
        color = self.COLORS.get(self._kind, "#999999")
        self.create_oval(pad, pad, s - pad, s - pad, fill=color,
                         outline=theme.blend(color, "#000000", 0.72),
                         width=1)
        if self._hover:
            glyph = {"close": "×", "min": "−", "max": "+"}.get(self._kind, "")
            self.create_text(s / 2, s / 2 - 0.5, text=glyph,
                             fill="#3a1112" if self._kind == "close" else "#1d2a12",
                             font=("Segoe UI", max(7, int(s * 0.62)), "bold"))


class TrafficLights(tk.Frame):
    """红黄绿三个圆点；点击分别关闭 / 最小化 / 最大化。"""

    def __init__(self, master, on_close: Callable, on_min: Callable,
                 on_max: Callable, bg: str = ""):
        bg = bg or theme.TITLE_BG
        super().__init__(master, bg=bg)
        size = theme.px(14)
        for kind, command in (("close", on_close), ("min", on_min),
                              ("max", on_max)):
            dot = TrafficLight(self, kind, command, bg=bg, size=size)
            dot.pack(side="left", padx=(0, theme.px(8)))
            setattr(self, f"dot_{kind}", dot)


class WindowButton(tk.Canvas):
    """标题栏右侧的最小化 / 最大化 / 关闭按钮。"""

    def __init__(self, master, kind: str, command: Callable,
                 width: int = 44, height: int = 32, bg: str = "",
                 fg: str = ""):
        super().__init__(master, width=width, height=height, bg=bg or theme.BG,
                         highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=0)
        self._kind = kind
        self._command = command
        self._fg = fg or theme.FG_DIM
        self._bg = bg or theme.BG
        self._hover = False
        self._bw, self._bh = width, height
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _on_release(self, event) -> None:
        inside = 0 <= event.x <= self._bw and 0 <= event.y <= self._bh
        if inside:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        p = theme.px
        close = self._kind == "close"
        width = max(1, int(round(p(1.4))))
        if self._hover:
            fill = "#e81123" if close else theme.CARD_HOVER
            draw_round_rect(self, 2, 2, self._bw - 2, self._bh - 2,
                            p(7), fill=fill, outline="")
        color = "#ffffff" if (close and self._hover) else self._fg
        cx, cy = self._bw / 2, self._bh / 2
        s = p(5)
        if self._kind == "min":
            self.create_line(cx - s, cy, cx + s, cy, fill=color, width=width)
        elif self._kind in ("max", "restore"):
            if self._kind == "restore":
                self.create_rectangle(cx - s, cy - s + p(2),
                                      cx + s - p(2), cy + s,
                                      outline=color, width=width)
                self.create_rectangle(cx - s + p(2), cy - s,
                                      cx + s, cy + s - p(2),
                                      outline=color, width=width)
            else:
                self.create_rectangle(cx - s, cy - s, cx + s, cy + s,
                                      outline=color, width=width)
        elif close:
            self.create_line(cx - s, cy - s, cx + s, cy + s, fill=color,
                             width=width, capstyle="round")
            self.create_line(cx + s, cy - s, cx - s, cy + s, fill=color,
                             width=width, capstyle="round")
        elif self._kind == "settings":
            self.create_text(cx, cy, text="⚙", fill=color,
                             font=(theme.FONT_FAMILY, 10))


# --------------------------------------------------------------------------
# 边缘缩放
# --------------------------------------------------------------------------
def add_window_border(window) -> list[tk.Frame]:
    """给无边框窗口加一圈 1px 描边，浅色桌面上也能看清窗口边界。"""
    frames = []
    for options in ({"relwidth": 1, "height": 1},
                    {"relwidth": 1, "height": 1, "rely": 1, "y": -1},
                    {"relheight": 1, "width": 1},
                    {"relheight": 1, "width": 1, "relx": 1, "x": -1}):
        edge = tk.Frame(window, bg=theme.BORDER)
        edge.place(**options)
        edge.lift()
        frames.append(edge)
    return frames


# 用于扣掉窗口四角颜色的透明键（不会出现在任何主题配色里）
CORNER_KEY = "#ff00fe"


def clear_window_radius(window) -> None:
    for canvas in list(getattr(window, "_corner_canvases", [])):
        try:
            canvas.destroy()
        except tk.TclError:
            pass
    window._corner_canvases = []
    try:
        window.attributes("-transparentcolor", "")
    except tk.TclError:
        pass


def apply_window_radius(window, radius: int, bg: str, border: str) -> None:
    """用透明色键把窗口四角切成圆角（仅 Windows）。

    半径 0 时恢复直角。四角各放一个半径大小的 Canvas：
    画布底色用透明键，只有圆角内部的四分之一圆用窗口底色填充，
    于是圆角外的像素变透明，视觉上就是圆角窗口。
    """
    clear_window_radius(window)
    if radius <= 0 or os.name != "nt":
        return
    try:
        window.attributes("-transparentcolor", CORNER_KEY)
    except tk.TclError:
        return
    r = max(2, int(radius))
    specs = (
        (0, 0, (0, 0, 2 * r, 2 * r), 90),          # 左上
        (1, 0, (-r, 0, r, 2 * r), 0),              # 右上
        (0, 1, (0, -r, 2 * r, r), 180),            # 左下
        (1, 1, (-r, -r, r, r), 270),               # 右下
    )
    canvases = []
    try:
        for rel_x, rel_y, bbox, start in specs:
            canvas = tk.Canvas(window, width=r, height=r, bg=CORNER_KEY,
                               highlightthickness=0, bd=0, takefocus=0)
            options = {"relx": rel_x, "rely": rel_y, "anchor": "nw"}
            if rel_x:
                options["x"] = -r
            if rel_y:
                options["y"] = -r
            canvas.place(**options)
            canvas.create_arc(*bbox, start=start, extent=90, style="pieslice",
                              fill=bg, outline="")
            canvas.create_arc(*bbox, start=start, extent=90, style="arc",
                              outline=border, width=1)
            canvas.lift()
            canvases.append(canvas)
    except tk.TclError:
        for canvas in canvases:
            canvas.destroy()
        return
    window._corner_canvases = canvases


class WindowResizer:
    """在窗口四边/四角放透明的拖动热区，实现无边框缩放。"""

    _CURSORS = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "nw": "size_nw_se", "se": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
    }

    def __init__(self, window, min_width: int, min_height: int,
                 is_maximized: Optional[Callable[[], bool]] = None):
        self.window = window
        self.min_width = max(200, int(min_width))
        self.min_height = max(120, int(min_height))
        self._is_maximized = is_maximized or (lambda: False)
        self._enabled = True
        self._mode = ""
        self._start = (0, 0)
        self._geometry = (0, 0, 0, 0)
        self._grips: list[tk.Frame] = []
        thickness = theme.px(6)
        corner = theme.px(12)
        self._specs = {
            "n": {"relwidth": 1, "height": thickness},
            "s": {"relwidth": 1, "height": thickness, "rely": 1,
                  "y": -thickness},
            "w": {"relheight": 1, "width": thickness},
            "e": {"relheight": 1, "width": thickness, "relx": 1,
                  "x": -thickness},
            "nw": {"width": corner, "height": corner},
            "ne": {"width": corner, "height": corner, "relx": 1,
                   "x": -corner},
            "sw": {"width": corner, "height": corner, "rely": 1,
                   "y": -corner},
            "se": {"width": corner, "height": corner, "relx": 1,
                   "x": -corner, "rely": 1, "y": -corner},
        }
        for name, options in self._specs.items():
            cursor = self._CURSORS.get(name, "")
            try:
                frame = tk.Frame(window, bg=theme.BG, cursor=cursor)
            except tk.TclError:
                frame = tk.Frame(window, bg=theme.BG)
            frame.place(**options)
            frame.bind("<ButtonPress-1>",
                       lambda e, n=name: self._on_press(n, e))
            frame.bind("<B1-Motion>", self._on_motion)
            frame.bind("<ButtonRelease-1>", self._on_release)
            frame.lift()
            self._grips.append(frame)
        # 1px 描边：无边框窗口在浅色桌面上也能看清边界
        self._edges = add_window_border(window)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled) and not self._is_maximized()
        for name, frame in zip(self._specs, self._grips):
            try:
                frame.configure(cursor=(self._CURSORS.get(name, "")
                                        if self._enabled else ""))
            except tk.TclError:
                pass

    def _on_press(self, name: str, event) -> None:
        if not self._enabled or self._is_maximized():
            return
        self._mode = name
        self._start = (event.x_root, event.y_root)
        try:
            self._geometry = (self.window.winfo_x(), self.window.winfo_y(),
                              self.window.winfo_width(),
                              self.window.winfo_height())
        except tk.TclError:
            self._mode = ""

    def _on_motion(self, event) -> None:
        if not self._mode:
            return
        x0, y0, w0, h0 = self._geometry
        dx = event.x_root - self._start[0]
        dy = event.y_root - self._start[1]
        x, y, w, h = x0, y0, w0, h0
        if "n" in self._mode:
            y = y0 + dy
            h = h0 - dy
        if "s" in self._mode:
            h = h0 + dy
        if "w" in self._mode:
            x = x0 + dx
            w = w0 - dx
        if "e" in self._mode:
            w = w0 + dx
        if w < self.min_width:
            if "w" in self._mode:
                x = x0 + w0 - self.min_width
            w = self.min_width
        if h < self.min_height:
            if "n" in self._mode:
                y = y0 + h0 - self.min_height
            h = self.min_height
        try:
            self.window.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            self._mode = ""

    def _on_release(self, _event=None) -> None:
        self._mode = ""
