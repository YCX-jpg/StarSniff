"""自绘控件：主按钮、图标按钮、自定义资源列表（预览 + 复选框）。"""

from __future__ import annotations

import math
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable, Optional

from . import theme
from .mediainfo import fmt_size


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius=8, **kwargs):
    """在 Canvas 上画圆角矩形。"""
    radius = max(0, min(radius, abs(x2 - x1) // 2, abs(y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


def paint_wallpaper(canvas: tk.Canvas, width: int, height: int,
                    spec: dict | None = None, tag: str = "wall",
                    offset: tuple[float, float] = (0, 0),
                    win: tuple[float, float] = (0, 0),
                    phase: float | None = None) -> None:
    """在 Canvas 上画主题壁纸：三段竖向渐变 + 几团柔和的彩色光斑。

    ``offset`` 是画布左上角在窗口里的像素位置，``win`` 是窗口尺寸，
    这样标题栏和内容区各自画出来的壁纸能接上，看起来是同一张。
    ``phase`` 不为空时让光斑非常缓慢地漂移（给标题栏做呼吸感）。
    """
    spec = spec or theme.wallpaper_spec()
    win_w, win_h = (win if win and win[0] > 8 and win[1] > 8
                    else (max(width, 1), max(height, 1)))
    ox, oy = offset
    try:
        canvas.delete(tag)
    except tk.TclError:
        return
    aspect = max(0.3, win_w / max(1.0, win_h))
    step = max(2, int(round(height / 150.0)))
    y = 0
    while y < height:
        v = (oy + y + step / 2.0) / max(1.0, win_h)
        color = theme.wallpaper_color_at(spec, 0.5, v, aspect, blobs=False)
        canvas.create_rectangle(0, y, width, y + step + 1, fill=color,
                                outline=color, tags=tag)
        y += step
    for buoy, buv, radius, blob_color, strength in spec.get("blobs", ()):
        if phase is not None:
            buoy = buoy + 0.014 * math.sin(math.tau * (phase + buv))
            buv = buv + 0.011 * math.cos(math.tau * (phase + buoy))
        cx = buoy * win_w - ox
        cy = buv * win_h - oy
        rx = radius * win_w
        if (cx + rx < 0 or cx - rx > width
                or cy + rx < 0 or cy - rx > height):
            continue
        base_at = theme.wallpaper_color_at(spec, buoy, buv, aspect,
                                           blobs=False)
        rings = 40
        for index in range(rings):
            k = (index + 1) / rings
            factor = 1.0 - k * 0.92
            color = theme.blend(blob_color, base_at, strength * k ** 1.7)
            canvas.create_oval(cx - rx * factor, cy - rx * factor,
                               cx + rx * factor, cy + rx * factor,
                               fill=color, outline="", tags=tag)


def wallpaper_base(spec: dict | None = None, u: float = 0.5, v: float = 0.3,
                   aspect: float = 1.7, white: float = 0.62) -> str:
    """玻璃卡片的底色：壁纸颜色混白，像磨砂玻璃透出后面的壁纸。"""
    spec = spec or theme.wallpaper_spec()
    wall = theme.wallpaper_color_at(spec, u, v, aspect)
    return theme.blend("#ffffff", wall, white)


def glass_surface(canvas: tk.Canvas, x1, y1, x2, y2, radius, base: str,
                  phase: float = 0.0, mode: str = "glass",
                  accent: str = "", backdrop: str = "",
                  sweep: bool = True, tags: tuple = ()) -> None:
    """绘制一块带光照的玻璃/亚克力表面。

    光照模型（固定）：
    * 主光来自左上方（天空/窗光）→ 顶边和左上角高光；
    * 环境光在顶部 → 上明下暗的竖向渐变；
    * 地面反光从下方进入玻璃 → 底部的折射亮带；
    * 右下边缘处于背光 → 暗边 + 投影。

    浅色主题下白色高光会“看不见”，所以改用冷调暗边 + 彩色折射带表现
    厚度。phase 控制彩色柔光与扫光的位置；sweep=False 时只画静态部分，
    会动的那层由 ``glass_sweep`` 按帧率单独补画。
    """
    width, height = x2 - x1, y2 - y1
    if width <= 3 or height <= 3:
        return
    p = theme.px
    radius = max(0, min(radius, width // 2, height // 2))
    # 用底色本身的明暗决定光照模型：浅色卡片按浅色玻璃画，
    # 深色按钮/强调色块仍然用白色高光，否则白字会糊成一片。
    dark = theme.luma(base) < 0.55
    accent = accent or theme.ACCENT
    backdrop = backdrop or theme.BG
    tags = tuple(tags) or ()
    # 投影：浅色玻璃更需要阴影才能“浮”在壁纸上
    shade = (theme.blend("#000000", backdrop, 0.45) if dark
             else theme.blend("#55698a", backdrop, 0.15))
    round_rect(canvas, x1, y1 + p(2), x2, y2 + p(2), radius, fill=shade,
               outline="", tags=tags)
    round_rect(canvas, x1, y1, x2, y2, radius, fill=base, outline="",
               tags=tags)
    inset = max(p(5), radius * 0.45)
    bands = 22
    top_boost = 0.12 if dark else 0.66
    bottom_shade = 0.24 if dark else 0.10
    for index in range(bands):
        t = index / max(1, bands - 1)
        if t < 0.62:
            color = theme.blend("#ffffff", base, top_boost * (1.0 - t) ** 1.7)
        else:
            k = (t - 0.62) / 0.38
            if dark:
                color = theme.blend("#000000", base, bottom_shade * k)
            else:
                color = theme.blend("#5f7fa8", base, bottom_shade * k)
        y0 = y1 + radius * 0.3 + (height - radius * 0.6) * t
        y1_band = y0 + (height - radius * 0.6) / bands + 1
        canvas.create_rectangle(x1 + inset, y0, x2 - inset, y1_band,
                                fill=color, outline=color, tags=tags)
    # 顶边 / 左边反射，右下背光暗边
    canvas.create_line(x1 + radius * 0.65, y1 + p(1),
                       x2 - radius * 0.65, y1 + p(1),
                       fill=theme.blend("#ffffff", base, 0.62 if dark else 0.92),
                       width=1, capstyle="round", tags=tags)
    canvas.create_line(x1 + p(1), y1 + radius * 0.7,
                       x1 + p(1), y2 - radius * 0.7,
                       fill=theme.blend("#ffffff", base, 0.22 if dark else 0.70),
                       width=1, tags=tags)
    canvas.create_line(x2 - p(1), y1 + radius * 0.7,
                       x2 - p(1), y2 - radius * 0.7,
                       fill=theme.blend("#000000", base, 0.26 if dark else 0.09),
                       width=1, tags=tags)
    canvas.create_line(x1 + radius * 0.7, y2 - p(1),
                       x2 - radius * 0.7, y2 - p(1),
                       fill=theme.blend("#000000", base, 0.34 if dark else 0.16),
                       width=1, tags=tags)
    if mode == "glass" and height > p(64):
        # 底部折射带：环境光从下方穿入，带强调色——浅色玻璃的“颜色”来源
        band_top = y1 + height * (0.58 if dark else 0.62)
        band_bottom = y1 + height * (0.74 if dark else 0.82)
        refracted = theme.blend(accent, base, 0.13 if dark else 0.20)
        canvas.create_polygon(
            x1 + inset + p(4), band_top,
            x2 - inset - p(4), band_top - p(2),
            x2 - inset - p(2), band_bottom,
            x1 + inset + p(2), band_bottom + p(2),
            fill=refracted, outline="", tags=tags)
        canvas.create_line(x1 + inset + p(6), band_top,
                           x2 - inset - p(6), band_top - p(1),
                           fill=theme.blend("#ffffff", base,
                                            0.28 if dark else 0.75),
                           width=1, tags=tags)
        # 左上角环境色柔光（静态版；动画版由 glass_sweep 负责）
        sx = x1 + width * 0.26
        sy = y1 + height * 0.22
        canvas.create_oval(sx - width * 0.16, sy - height * 0.12,
                           sx + width * 0.16, sy + height * 0.12,
                           fill=theme.blend(theme.blend(accent, "#ffffff", 0.4),
                                            base, 0.10 if dark else 0.16),
                           outline="", tags=tags)
    elif mode == "acrylic":
        # 亚克力：顶部雾光 + 很淡的竖向光泽，没有锐利折射
        canvas.create_rectangle(x1 + p(2), y1 + p(2),
                                x2 - p(2), y2 - p(2),
                                fill=theme.blend("#ffffff", base, 0.018),
                                outline="", tags=tags)
        canvas.create_polygon(
            x1 + width * 0.10, y1 + p(2),
            x2 - width * 0.10, y1 + p(2),
            x2 - width * 0.22, y1 + height * 0.55,
            x1 + width * 0.22, y1 + height * 0.55,
            fill=theme.blend("#ffffff", base, 0.045), outline="", tags=tags)
    if sweep:
        glass_sweep(canvas, x1, y1, x2, y2, radius, base, phase,
                    accent=accent, tags=tags)


def soft_beam(canvas: tk.Canvas, x1, y1, x2, y2, center: float,
               half: float, base: str, color: str, peak: float,
               tags: tuple, strips: int = 9) -> None:
    """用几条相邻的竖条拼出一条“软边”光带（Tk 没有透明度）。

    直接画一个椭圆或大色块会露出硬边，几条按正弦衰减的竖条拼起来，
    远看就是一道柔和的反光。
    """
    slant = (y2 - y1) * 0.18
    for index in range(strips):
        t = (index + 0.5) / strips
        weight = peak * math.sin(math.pi * t) ** 1.4
        if weight <= 0.002:
            continue
        xa = center - half + 2 * half * (index / strips)
        xb = center - half + 2 * half * ((index + 1) / strips)
        canvas.create_polygon(xa, y1 + 2, xb, y1 + 2, xb + slant, y2 - 2,
                              xa + slant, y2 - 2,
                              fill=theme.blend(color, base, weight),
                              outline="", tags=tags)


def glass_sweep(canvas: tk.Canvas, x1, y1, x2, y2, radius, base: str,
                phase: float, accent: str = "", tags: tuple = ()) -> None:
    """玻璃上会动的那部分：会换色的环境反光 + 一道斜向扫过的光带。

    只画十几个图元，所以可以放心地按 5–120 FPS 重绘。
    """
    width, height = x2 - x1, y2 - y1
    if width <= 3 or height <= 3:
        return
    dark = theme.luma(base) < 0.55
    tags = tuple(tags) or ()
    # 小控件（按钮）上扫光必须很淡，否则白字会消失在亮带里
    subtle = 0.4 if height < theme.px(60) else 1.0
    progress = phase % 1.0
    # 彩色环境光：颜色随时间在强调色/雾蓝/淡紫/浅粉之间流动
    aura = theme.aurora_color(phase)
    center = x1 + width * (progress * 1.3 - 0.15)
    soft_beam(canvas, x1, y1, x2, y2, center, width * 0.17, base, aura,
               (0.07 if dark else 0.11) * subtle, tags)
    soft_beam(canvas, x1, y1, x2, y2, center + width * 0.07, width * 0.05,
               base, "#ffffff", (0.05 if dark else 0.16) * subtle, tags,
               strips=5)



def ellipsize(text: str, font: tkfont.Font, max_width: int) -> str:
    """按像素宽度截断文本并加省略号。"""
    if max_width <= 0:
        return ""
    if font.measure(text) <= max_width:
        return text
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if font.measure(text[:mid] + ellipsis) <= max_width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + ellipsis if low > 0 else ellipsis


def wrap_lines(text: str, font: tkfont.Font, max_width: int,
               max_lines: int = 2) -> list[str]:
    """按像素宽度自动换行（中英混排按字符切分）。"""
    if max_width <= 0 or max_lines <= 0:
        return [""]
    lines: list[str] = []
    current = ""
    for ch in text:
        if font.measure(current + ch) <= max_width:
            current += ch
            continue
        if current:
            lines.append(current)
        current = ch
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    # 单行仍超宽 → 省略
    if len(lines) == max_lines:
        consumed = sum(len(line) for line in lines)
        if consumed < len(text):
            if len(lines) == 1:
                lines[0] = ellipsize(lines[0], font, max_width)
            else:
                lines[-1] = ellipsize(lines[-1], font, max_width)
    return lines or [""]


class AccentButton(tk.Canvas):
    """自绘按钮：支持 hover/按压动画、复古 3D 边框和玻璃高光。"""

    def __init__(self, master, text: str, command: Optional[Callable] = None,
                 width: int = 0, height: int = 0, color: str = "",
                 hover: Optional[str] = None, fg: str = "#ffffff",
                 font=None, radius: int = 0, border: str = "",
                 bg: str = ""):
        color = color or theme.ACCENT
        hover = hover or theme.ACCENT_HOVER
        width = width or theme.px(104)
        height = height or theme.px(34)
        super().__init__(master, width=width, height=height,
                        bg=bg or theme.PANEL,
                        highlightthickness=0, bd=0, takefocus=1)
        self._text = text
        self._command = command
        self._color = color
        self._hover_color = hover or theme.ACCENT_HOVER
        self._fg = fg
        self._font = font or theme.FONT_BUTTON
        self._radius = radius or theme.button_radius()
        self._border = border
        self._bw, self._bh = width, height
        self._state = "normal"
        self._hovering = False
        self._pressed = False
        self._fill = color
        self._anim_job = None
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        self.configure(cursor="hand2")

    # ------------------------------------------------------------------ 绘制
    def _panel_color(self) -> str:
        try:
            return self.master.cget("bg")
        except tk.TclError:
            return theme.PANEL

    def _draw(self) -> None:
        self.delete("all")
        p = theme.px
        enabled = self._state == "normal"
        fill = self._fill
        fg = self._fg
        if not enabled:
            fill = theme.blend(theme.CARD, theme.BG, 0.55)
            fg = theme.FG_FAINT
        if theme.CLASSIC:
            self._draw_classic(fill, fg)
            return
        inset = p(1) if self._pressed and theme.MOTION == "spring" else 0
        if theme.NEON:
            # 霓虹：外层淡光 + 内层实心，悬停时更亮
            glow = theme.blend(theme.ACCENT, theme.BG, 0.35 if self._hovering
                               else 0.55)
            round_rect(self, 1, 1, self._bw - 1, self._bh - 1,
                       self._radius + p(2), fill=glow, outline="")
        outline = self._border
        if theme.ACRYLIC and not outline:
            outline = theme.blend(theme.BORDER_LIGHT, theme.BORDER, 0.6)
        x1, y1 = 1 + inset, 1 + inset
        x2, y2 = self._bw - 1 - inset, self._bh - 1 - inset
        glassy = (theme.GLASS and not theme.NEON) or theme.ACRYLIC
        if glassy:
            glass_surface(self, x1, y1, x2, y2, self._radius, fill,
                          phase=(time.time() * 0.22 + self._bw * 0.01) % 1.0,
                          mode="acrylic" if theme.ACRYLIC else "glass",
                          accent=theme.ACCENT)
            if outline:
                round_rect(self, x1, y1, x2, y2, self._radius,
                           fill="", outline=outline, width=1)
        elif outline:
            round_rect(self, x1, y1, x2, y2, self._radius,
                       fill=fill, outline=outline, width=1)
        else:
            round_rect(self, x1, y1, x2, y2, self._radius,
                       fill=fill, outline="")
        self.create_text(self._bw / 2, self._bh / 2,
                         text=self._text, fill=fg, font=self._font)

    def _draw_classic(self, fill: str, fg: str) -> None:
        """Windows 2000 风格：灰底 + 3D 边框（按下时边框翻转）。"""
        w, h = self._bw, self._bh
        self.create_rectangle(0, 0, w, h, fill=fill, outline=theme.BORDER_LIGHT)
        light, dark = "#ffffff", theme.BORDER_LIGHT
        if self._pressed:
            light, dark = dark, light
        self.create_line(1, 1, w - 1, 1, fill=light)
        self.create_line(1, 1, 1, h - 1, fill=light)
        self.create_line(1, h - 1, w - 1, h - 1, fill=dark)
        self.create_line(w - 1, 1, w - 1, h - 1, fill=dark)
        self.create_text(w / 2, h / 2 + (1 if self._pressed else 0),
                         text=self._text, fill=fg, font=self._font)

    def _animate_to(self, target: str) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None
        if not theme.ANIMATIONS or theme.CLASSIC:
            self._fill = target
            self._draw()
            return
        start = self._fill
        # 过渡总时长固定在 ~110ms：帧率越低帧数越少，但不会变慢
        steps = max(2, int(round(110.0 / theme.frame_delay())))

        def step(index: int = 0) -> None:
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            weight = index / steps
            self._fill = theme.blend(target, start, weight)
            self._draw()
            if index < steps:
                self._anim_job = self.after(theme.frame_delay(),
                                            lambda: step(index + 1))
            else:
                self._anim_job = None

        step(0)

    def _on_enter(self, _e=None):
        self._hovering = True
        if self._state == "normal" and not self._pressed:
            self._animate_to(self._hover_color)

    def _on_leave(self, _e=None):
        self._hovering = False
        if self._state == "normal" and not self._pressed:
            self._animate_to(self._color)

    def _on_press(self, _e=None):
        if self._state != "normal":
            return
        self._pressed = True
        if theme.CLASSIC:
            self._draw()
            return
        self._fill = theme.blend(self._color, "#000000", 0.28)
        self._draw()

    def _on_release(self, event=None):
        if self._state != "normal":
            self._pressed = False
            self._draw()
            return
        self._pressed = False
        self._animate_to(self._hover_color if self._hovering else self._color)
        inside = 0 <= (event.x if event else 0) <= self._bw and \
            0 <= (event.y if event else 0) <= self._bh
        if inside and self._command:
            self._command()

    # ------------------------------------------------------------------ API
    def invoke(self) -> None:
        if self._state == "normal" and self._command:
            self._command()

    def set_state(self, state: str) -> None:
        self._state = state
        self.configure(cursor="hand2" if state == "normal" else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_command(self, command: Callable) -> None:
        self._command = command


class IconButton(tk.Canvas):
    """小号图标/文字按钮（用于行内操作）。"""

    def __init__(self, master, text: str, command=None, width: int = 28,
                 height: int = 24, color: str = theme.FG_DIM):
        bg = theme.PANEL
        super().__init__(master, width=width, height=height, bg=bg,
                        highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._color = color
        self._bw, self._bh = width, height
        self.create_text(width / 2, height / 2, text=text, fill=color,
                         font=theme.FONT_SMALL)
        self.bind("<Button-1>", lambda _e: command() if command else None)
        self.bind("<Enter>", lambda _e: self.itemconfigure(1, fill=theme.FG))
        self.bind("<Leave>", lambda _e: self.itemconfigure(1, fill=self._color))
        self.configure(cursor="hand2")


# --------------------------------------------------------------------------
# 滚动条（加宽、深色、可拖拽）
# --------------------------------------------------------------------------
class SlimScrollbar(tk.Canvas):
    """自绘细滚动条，替代 ttk.Scrollbar 以获得统一深色外观。"""

    def __init__(self, master, command: Callable[[str], None],
                 width: int = 10):
        super().__init__(master, width=width, bg=theme.PANEL,
                        highlightthickness=0, bd=0)
        self._command = command
        self._fraction = 0.0
        self._total = 1.0
        self._bw = width
        self._dragging = False
        self._drag_fraction = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, f, l):
        self._fraction = float(f)
        self._total = float(l)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        h = self.winfo_height()
        if h <= 1:
            return
        span = self._total - self._fraction
        if span >= 0.999:
            return
        top = self._fraction * h
        bottom = max(top + 24, self._total * h)
        bottom = min(bottom, h)
        round_rect(self, 2, top, self._bw - 2, bottom, 4,
                   fill=theme.BORDER_LIGHT, outline="")

    def _on_press(self, event):
        h = self.winfo_height()
        if h <= 1:
            return
        if self._total - self._fraction >= 0.999:
            click_h = max(0.05, event.y / h - 0.05)
            self._command("moveto", str(click_h))
            return
        top = self._fraction * h
        bottom = self._total * h
        if top <= event.y <= bottom:
            self._dragging = True
            self._drag_fraction = event.y / h
            self._fraction = max(0.0, min(1.0, event.y / h - 0.02))
            self._redraw()
            self._command("moveto", str(self._fraction))
            return
        click_h = max(0.05, event.y / h - 0.05)
        self._command("moveto", str(click_h))

    def _on_drag(self, event):
        if not self._dragging:
            return
        h = self.winfo_height()
        if h <= 1:
            return
        fraction = (event.y - self._drag_fraction * h) / h
        self._fraction = max(0.0, min(1.0, fraction))
        self._redraw()
        self._command("moveto", str(self._fraction))

    def _on_release(self, _event):
        self._dragging = False


# --------------------------------------------------------------------------
# 资源列表
# --------------------------------------------------------------------------
class ResourceList(tk.Frame):
    """资源列表：左侧预览（缩略图 / 时长+码率 / 分辨率+时长），右侧文件名、大小、链接。

    * 整行可以勾选（复选框），Ctrl/Shift 支持多选高亮；
    * 鼠标悬停高亮，双击打开链接，右键菜单；
    * 用 Canvas 自绘 + 手动虚拟化（只画可见行），上千条也不卡。
    """

    PAD_X = 12

    def __init__(self, master, on_change: Optional[Callable] = None,
                 on_open: Optional[Callable] = None,
                 on_context: Optional[Callable] = None):
        list_style = theme.LAYOUT.get("list", "rows")
        list_bg = theme.BG if list_style == "cards" else theme.PANEL
        super().__init__(master, bg=list_bg, highlightthickness=0)
        # _source 保存全部资源；items 只是当前筛选后可见的部分
        self._source: list = []
        self.items: list = []
        self._photos: dict[str, tk.PhotoImage] = {}
        self._placeholders: dict[str, tk.PhotoImage] = {}
        self._row_index: dict[int, int] = {}
        self._hover: Optional[int] = None
        self._selected: set[int] = set()
        self._filter_kind = "all"
        self._filter_query = ""
        self._sort_key = "default"
        self._sort_dirty = False
        self._on_change = on_change or (lambda: None)
        self._on_open = on_open or (lambda _item: None)
        self._on_context = on_context or (lambda _e, _item: None)
        self._rows_drawn: list[int] = []
        # 可见卡片的几何信息：动画只重画这些卡片上的扫光，不重画整行
        self._card_boxes: list[tuple] = []
        self._win_size = (0, 0)
        self._win_offset = (0, 0)
        # 批量插入时合并重绘，避免 N 次全表刷新
        self._redraw_pending = False
        self._change_pending = False
        self._shine_phase = 0.0
        self._shine_job = None
        self.empty_hint = "还没有嗅探到资源\n在上方输入网址后点击「开始」"

        self.f_title = tkfont.Font(family=theme.FONT_FAMILY, size=10)
        self.f_title_bold = tkfont.Font(family=theme.FONT_FAMILY, size=10,
                                        weight="bold")
        self.f_dim = tkfont.Font(family=theme.FONT_FAMILY, size=9)
        self.f_tiny = tkfont.Font(family=theme.FONT_FAMILY, size=8)
        self.f_preview = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_url = tkfont.Font(family="Consolas", size=8)

        self.canvas = tk.Canvas(self, bg=list_bg, highlightthickness=0,
                                bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll = SlimScrollbar(self, self.canvas.yview, width=theme.px(11))
        self.scroll.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._on_wheel_delta(-3))
        self.canvas.bind("<Button-5>", lambda _e: self._on_wheel_delta(3))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Control-a>", lambda _e: self.select_all())
        self.canvas.bind("<space>", lambda _e: self.toggle_selected())
        self.bind("<Control-a>", lambda _e: self.select_all())

        if (theme.ACTIVE_PRESET in ("glass", "acrylic")
                and theme.ANIMATIONS):
            self.bind("<Destroy>", self._on_destroy_shine, add="+")
            try:
                self._shine_job = self.after(theme.frame_delay(),
                                             self._shine_tick)
            except tk.TclError:
                self._shine_job = None

    def _shine_tick(self) -> None:
        """列表玻璃的动画：只重画卡片上的扫光，帧率跟随设置（5–120 FPS）。"""
        self._shine_job = None
        try:
            if not self.winfo_exists():
                return
            delay = theme.frame_delay()
            self._shine_phase = (self._shine_phase + 0.45 * delay / 1000.0) % 1.0
            if self._card_boxes and self.winfo_ismapped():
                self._draw_sweeps()
            self._shine_job = self.after(delay, self._shine_tick)
        except tk.TclError:
            self._shine_job = None

    def _draw_sweeps(self) -> None:
        """重画所有可见卡片的扫光（两个图元/卡片，所以可以跑满帧率）。

        扫光必须压在卡片上、但在文字下面，否则白字会糊掉：这里给每行一个
        独立 tag，画完后再把它压到该行内容的第一张图元下面。
        """
        canvas = self.canvas
        try:
            canvas.delete("shine")
        except tk.TclError:
            return
        radius = theme.card_radius()
        for index, x1, y, x2, h, base, anchor in self._card_boxes:
            tag = f"shine{index}"
            try:
                glass_sweep(canvas, x1, y, x2, y + h, radius, base,
                            (self._shine_phase + index * 0.11) % 1.0,
                            accent=theme.ACCENT, tags=("shine", tag))
                if anchor is not None:
                    canvas.tag_lower(tag, anchor)
            except tk.TclError:
                return

    def _on_destroy_shine(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._shine_job is not None:
            try:
                self.after_cancel(self._shine_job)
            except tk.TclError:
                pass
            self._shine_job = None

    # ------------------------------------------------------------------ 数据
    def set_empty_hint(self, text: str) -> None:
        """空列表时的提示文字（嗅探过程中显示进度）。"""
        if text != self.empty_hint:
            self.empty_hint = text
            if not self.items:
                self.request_redraw()

    def set_source(self, items: list) -> None:
        """设置完整资源集合（筛选不会破坏它）。"""
        self._source = list(items)
        self._selected.clear()
        self._recompute()

    def apply_filter(self, kind: str = "all", query: str = "") -> None:
        self._filter_kind = kind or "all"
        self._filter_query = (query or "").lower().strip()
        self._selected.clear()
        self._recompute()

    # ------------------------------------------------------------------ 排序
    SORT_KEYS = ("default", "size", "kind", "name")
    _KIND_ORDER = {"image": 0, "audio": 1, "video": 2, "stream": 3}

    def apply_sort(self, key: str) -> None:
        """切换排序方式：默认（嗅探顺序）/ 大小 / 类型 / 文件名。"""
        key = key if key in self.SORT_KEYS else "default"
        if key == self._sort_key:
            return
        selected = {id(it) for it in self.selected_items()}
        self._sort_key = key
        self._recompute(keep_scroll=True)
        self._restore_selection(selected)

    def _sort_key_of(self, item):
        kind = getattr(item, "kind", "")
        size = getattr(item, "size", 0) or 0
        name = (getattr(item, "filename", "")
                or getattr(item, "url", "")).lower()
        if self._sort_key == "size":
            return (0, -size, name)
        if self._sort_key == "kind":
            return (self._KIND_ORDER.get(kind, 9), -size, name)
        if self._sort_key == "name":
            return (name, "")
        return (0, 0, "")

    def _sort_items(self) -> None:
        if self._sort_key != "default":
            self.items.sort(key=self._sort_key_of)

    def _resort_in_place(self) -> None:
        """批量插入/元数据更新后统一排序一次（由空闲刷新触发）。"""
        if self._sort_key == "default":
            return
        selected = {id(it) for it in self.selected_items()}
        self._sort_items()
        self._row_index = {id(it): i for i, it in enumerate(self.items)}
        self._restore_selection(selected)

    def _restore_selection(self, ids: set[int]) -> None:
        self._selected = ({i for i, it in enumerate(self.items)
                           if id(it) in ids} if ids else set())
        self.redraw()

    def _matches(self, item) -> bool:
        if self._filter_kind != "all" and getattr(item, "kind", "") != self._filter_kind:
            return False
        if self._filter_query:
            haystack = f"{getattr(item, 'filename', '')} {getattr(item, 'url', '')}"
            if self._filter_query not in haystack.lower():
                return False
        return True

    def _recompute(self, keep_scroll: bool = False) -> None:
        self.items = [it for it in self._source if self._matches(it)]
        self._sort_items()
        self._row_index = {id(it): i for i, it in enumerate(self.items)}
        self._rebuild()
        if not keep_scroll:
            try:
                self.canvas.yview_moveto(0)
            except tk.TclError:
                pass
        self._on_change()

    def add_item(self, item) -> None:
        self._source.append(item)
        if self._matches(item):
            self.items.append(item)
            self._row_index[id(item)] = len(self.items) - 1
            if self._sort_key != "default":
                self._sort_dirty = True        # 空闲时统一重排，避免逐条排序
            self._update_scrollregion()
            self.request_redraw()
        self.request_change()

    # ------------------------------------------------- 合并刷新（批量友好）
    def request_redraw(self) -> None:
        """请求一次延迟重绘；同一批插入只重绘一次。"""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        try:
            self.after_idle(self._flush_ui)
        except tk.TclError:
            self._redraw_pending = False

    def request_change(self) -> None:
        """请求一次延迟的变更通知（用于刷新统计/页签计数）。"""
        if self._change_pending:
            return
        self._change_pending = True
        self.request_redraw()

    def _flush_ui(self) -> None:
        self._redraw_pending = False
        if self._sort_dirty:
            self._sort_dirty = False
            try:
                self._resort_in_place()
            except tk.TclError:
                return
        try:
            self.redraw()
        except tk.TclError:
            return
        if self._change_pending:
            self._change_pending = False
            try:
                self._on_change()
            except tk.TclError:
                pass

    def all_items(self) -> list:
        return list(self._source)

    def remove_item(self, item) -> None:
        """从列表中移除一个条目（用于合并重复变体）。"""
        self._source = [it for it in self._source if it is not item]
        if item in self.items:
            self.items = [it for it in self.items if it is not item]
            self._row_index = {id(it): i for i, it in enumerate(self.items)}
            self._update_scrollregion()
        self.request_change()

    def update_item(self, item) -> None:
        # 元数据（大小/类型/文件名）变化会影响排序，空闲时统一重排一次
        if self._sort_key != "default":
            self._sort_dirty = True
        self.request_redraw()

    def clear(self) -> None:
        self._source.clear()
        self.items.clear()
        self._row_index.clear()
        self._selected.clear()
        self._photos.clear()
        self._rebuild()
        self._on_change()

    def filtered(self, kind_filter: str = "all", query: str = "") -> list:
        """（保留兼容）返回按条件筛选后的列表，不修改内部状态。"""
        query = (query or "").lower().strip()
        out = []
        for item in self._source:
            if kind_filter != "all" and getattr(item, "kind", "") != kind_filter:
                continue
            if query:
                haystack = f"{getattr(item, 'filename', '')} {item.url}".lower()
                if query not in haystack:
                    continue
            out.append(item)
        return out

    # ------------------------------------------------------------------ 选择
    def selected_items(self) -> list:
        return [self.items[i] for i in sorted(self._selected)
                if 0 <= i < len(self.items)]

    def checked_items(self) -> list:
        """所有已勾选资源（不受当前筛选影响）。"""
        return [it for it in self._source if getattr(it, "checked", False)]

    def toggle_selected(self) -> None:
        for item in self.selected_items():
            item.checked = not item.checked
        self.redraw()
        self._on_change()

    def select_all(self, only_visible: bool = True) -> None:
        targets = self.items if only_visible else self._source
        for item in targets:
            item.checked = True
        self.redraw()
        self._on_change()

    def select_none(self) -> None:
        for item in self._source:
            item.checked = False
        self.redraw()
        self._on_change()

    def set_checked(self, items, checked: bool = True) -> None:
        for item in items:
            item.checked = checked
        self.redraw()
        self._on_change()

    def set_selected(self, items) -> None:
        self._selected = {self._row_index[id(it)] for it in items
                          if id(it) in self._row_index}
        self.redraw()

    def checked_count(self) -> int:
        return sum(1 for it in self._source if getattr(it, "checked", False))

    # ------------------------------------------------------------------ 绘制
    def _rebuild(self) -> None:
        self.canvas.delete("all")
        self._rows_drawn.clear()
        self._update_scrollregion()
        self.redraw()

    def _update_scrollregion(self) -> None:
        height = max(1, len(self.items)) * theme.ROW_HEIGHT + 8
        self.canvas.configure(scrollregion=(0, 0, 1, height))

    def _thumb(self, item):
        """取得缩略图 PhotoImage，并保证不超过预览框（过大时整数降采样）。"""
        key = item.url
        photo = self._photos.get(key)
        if photo is not None:
            return photo
        data = getattr(item, "thumbnail", None)
        if not data:
            return None
        try:
            try:
                photo = tk.PhotoImage(data=data, format="png")
            except tk.TclError:
                from PIL import Image, ImageTk  # type: ignore
                import io

                img = Image.open(io.BytesIO(data))
                photo = ImageTk.PhotoImage(img)
            box = theme.THUMB_SIZE
            factor = 1
            while max(photo.width(), photo.height()) // factor > box:
                factor += 1
            if factor > 1:
                photo = photo.subsample(factor, factor)
            self._photos[key] = photo
            return photo
        except Exception:
            return None

    def _placeholder(self, kind: str) -> tk.PhotoImage:
        photo = self._placeholders.get(kind)
        if photo is not None:
            return photo
        size = theme.THUMB_SIZE
        image = tk.PhotoImage(width=size, height=size)
        image.put(theme.THUMB_BG, to=(0, 0, size, size))
        self._placeholders[kind] = image
        return image

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._card_boxes = []
        width = max(canvas.winfo_width(), 200)
        height = max(canvas.winfo_height(), 100)
        top = canvas.canvasy(0)
        first = max(0, int(top // theme.ROW_HEIGHT) - 1)
        visible = int(height // theme.ROW_HEIGHT) + 3
        last = min(len(self.items), first + visible)

        # 壁纸在列表里只露出一条条底色（卡片之间和左右留白）：
        # 每一行先铺一条按窗口位置取样的壁纸色，再盖上玻璃卡片。
        self._win_size = (max(self.winfo_toplevel().winfo_width(), width),
                          max(self.winfo_toplevel().winfo_height(), height))
        try:
            self._win_offset = (canvas.winfo_rootx()
                                - self.winfo_toplevel().winfo_rootx(),
                                canvas.winfo_rooty()
                                - self.winfo_toplevel().winfo_rooty())
        except tk.TclError:
            self._win_offset = (0, 0)
        self._paint_row_backgrounds(width, first, last)

        if not self.items:
            self._draw_empty(width, height)
            return

        gap = theme.px(int(theme.LAYOUT.get("card_gap", 0)))
        for index in range(first, last):
            item = self.items[index]
            y = index * theme.ROW_HEIGHT + max(2, gap // 2)
            self._draw_row(index, item, y,
                           theme.ROW_HEIGHT - max(4, gap), width)
        if theme.ANIMATIONS:
            self._draw_sweeps()

    def _paint_row_backgrounds(self, width: int, first: int, last: int) -> None:
        """铺壁纸底色：让玻璃卡片看起来浮在壁纸上。

        用按行切片的竖向渐变代替整张位图：十几条色块，滚动时跟着内容走，
        开销极小，又比纯色平铺更像一张壁纸。
        """
        canvas = self.canvas
        spec = theme.wallpaper_spec()
        win_w, win_h = self._win_size
        aspect = max(0.3, win_w / max(1.0, win_h))
        ox, oy = self._win_offset
        top = canvas.canvasy(0)
        height = max(canvas.winfo_height(), 100)
        row = theme.ROW_HEIGHT
        if self.items:
            y = max(0, first - 1) * row - row / 2
        else:
            # 空列表：用较小的步长铺满整个视口
            row = max(theme.px(24), row // 3)
            y = top - row
        end = top + height + row
        # 顶部淡入：列表上沿紧挨着搜索区，直接切到壁纸色会出现一条硬边
        fade = max(1.0, float(theme.px(36)))
        while y < end:
            v = (oy + y - top + row / 2) / max(1.0, win_h)
            color = theme.wallpaper_color_at(spec, 0.5, v, aspect)
            if y < top + fade:
                k = max(0.0, min(1.0, (y - top) / fade))
                color = theme.blend(color, theme.BG, k)
            canvas.create_rectangle(0, y, width, y + row + 1, fill=color,
                                    outline=color, tags=("wall",))
            y += row
        canvas.tag_lower("wall")

    def _draw_empty(self, width: int, height: int) -> None:
        """空列表：居中图标 + 标题 + 说明。"""
        canvas = self.canvas
        p = theme.px
        cx = width / 2
        cy = min(height / 2, p(210))
        box = p(64)
        top = cy - box / 2 - p(26)
        round_rect(canvas, cx - box / 2, top, cx + box / 2, top + box,
                   theme.card_radius(), fill=theme.CARD, outline=theme.BORDER)
        # 放大镜
        r = p(13)
        canvas.create_oval(cx - r, top + box / 2 - r - p(2),
                           cx + r, top + box / 2 + r - p(2),
                           outline=theme.FG_FAINT, width=max(2, p(2)))
        canvas.create_line(cx + r * 0.7, top + box / 2 + r * 0.7 - p(2),
                           cx + r * 1.5, top + box / 2 + r * 1.5 - p(2),
                           fill=theme.FG_FAINT, width=max(2, p(2)),
                           capstyle="round")
        lines = (self.empty_hint or "").split("\n")
        title = lines[0] if lines else ""
        rest = "\n".join(line for line in lines[1:] if line)
        canvas.create_text(cx, top + box + p(24), text=title,
                           fill=theme.FG_DIM, justify="center",
                           font=(theme.FONT_FAMILY, 11, "bold"))
        if rest:
            canvas.create_text(cx, top + box + p(50), text=rest,
                               fill=theme.FG_FAINT, justify="center",
                               font=(theme.FONT_FAMILY, 9))

    def _draw_row(self, index: int, item, y: float, h: float, width: int) -> None:
        canvas = self.canvas
        p = theme.px
        style = theme.LAYOUT.get("list", "rows")
        margin = p(24) if style == "cards" else p(6)
        x1, x2 = margin, width - margin
        checked = bool(getattr(item, "checked", False))
        selected = index in self._selected
        hovered = self._hover == index
        if style == "cards":
            base = theme.CARD
            if theme.GLASS and not theme.NEON:
                # 卡片是磨砂玻璃：底色取它背后那块壁纸的颜色混白
                win_w, win_h = self._win_size
                offset = self._win_offset[1] + y - canvas.canvasy(0)
                base = wallpaper_base(
                    u=0.5, v=(offset + h / 2) / max(1.0, win_h),
                    aspect=max(0.3, win_w / max(1, win_h)), white=0.62)
            if checked:
                fill = theme.blend(theme.ACCENT, base, 0.22)
            elif selected:
                fill = theme.blend(theme.ACCENT, base, 0.12)
            elif hovered:
                fill = theme.blend(theme.CARD_HOVER, base, 0.55)
            else:
                fill = base
            glass_surface(
                canvas, x1, y, x2, y + h, theme.card_radius(), fill,
                phase=(self._shine_phase + index * 0.11) % 1.0,
                mode="acrylic" if theme.ACRYLIC else "glass",
                accent=theme.ACCENT, backdrop=theme.BG,
                sweep=not theme.ANIMATIONS)
            anchor = round_rect(
                canvas, x1, y, x2, y + h, theme.card_radius(),
                fill="", outline=theme.blend(theme.BORDER_LIGHT, fill, 0.55),
                width=1)
            if theme.ANIMATIONS:
                self._card_boxes.append((index, x1, y, x2, h, fill, anchor))
        elif style == "flat":
            fill = (theme.SELECTED_ROW if checked else
                    theme.ROW_HOVER if (selected or hovered) else theme.PANEL)
            canvas.create_rectangle(x1, y, x2, y + h, fill=fill,
                                    outline=theme.BORDER)
            canvas.create_line(x1 + 1, y + 1, x2 - 1, y + 1, fill="#ffffff")
            if checked or selected:
                canvas.create_line(x1 + p(8), y + p(10), x1 + p(8),
                                   y + h - p(10), fill=theme.ACCENT,
                                   width=max(2, p(3)))
        else:
            if checked:
                fill = theme.SELECTED_ROW
            elif selected:
                fill = theme.blend(theme.ACCENT, theme.PANEL, 0.12)
            elif hovered:
                fill = theme.ROW_HOVER
            else:
                fill = theme.PANEL
            round_rect(canvas, x1, y, x2, y + h, theme.card_radius(), fill=fill,
                       outline="")
            if theme.ACRYLIC:
                # 磨砂纹理，让行表面像半透明亚克力而不是纯色块
                try:
                    canvas.create_rectangle(
                        x1 + p(2), y + p(2), x2 - p(2), y + h - p(2),
                        fill=theme.blend("#ffffff", fill, 0.035),
                        stipple="gray12", outline="")
                except tk.TclError:
                    pass
            if checked or selected:
                canvas.create_line(x1 + p(10), y + p(14), x1 + p(10),
                                   y + h - p(14), fill=theme.ACCENT,
                                   width=max(2, p(3)), capstyle="round")
            if not (checked or selected or hovered):
                canvas.create_line(x1 + p(22), y + h, x2 - p(14), y + h,
                                   fill=theme.BORDER, width=1)
        if style == "cards" and (checked or selected):
            canvas.create_line(x1 + p(14), y + p(14), x1 + p(14),
                               y + h - p(14), fill=theme.ACCENT,
                               width=max(2, p(3)), capstyle="round")

        # ---- 复选框
        cb_s = p(18)
        cb_x, cb_y = x1 + p(18), y + h / 2 - cb_s / 2
        if checked:
            round_rect(canvas, cb_x, cb_y, cb_x + cb_s, cb_y + cb_s,
                       theme.small_radius(), fill=theme.ACCENT, outline="")
            canvas.create_line(cb_x + cb_s * 0.26, cb_y + cb_s * 0.53,
                               cb_x + cb_s * 0.44, cb_y + cb_s * 0.71,
                               cb_x + cb_s * 0.74, cb_y + cb_s * 0.32,
                               fill="#ffffff", width=max(2, p(2)),
                               capstyle="round", joinstyle="round")
        else:
            round_rect(canvas, cb_x, cb_y, cb_x + cb_s, cb_y + cb_s,
                       theme.small_radius(), fill=theme.CARD if theme.CLASSIC else "",
                       outline=theme.BORDER_LIGHT,
                       width=max(1.0, p(1.5)))

        # ---- 左侧预览
        thumb_x = cb_x + cb_s + p(14)
        thumb_y = y + (h - theme.THUMB_SIZE) / 2
        self._draw_preview(item, thumb_x, thumb_y)

        # ---- 右侧信息
        text_x = thumb_x + theme.THUMB_SIZE + p(16)
        right_w = width - text_x - p(24 if style == "cards" else 20)
        title, detail = item.info_lines()
        kind_color = theme.KIND_COLORS.get(item.kind, theme.FG_DIM)
        if style == "cards":
            line1, line2, line3 = (y + h * 0.28, y + h * 0.53,
                                   y + h * 0.76)
        elif style == "flat":
            line1, line2, line3 = (y + h * 0.32, y + h * 0.58,
                                   y + h * 0.80)
        else:
            line1 = y + p(23)
            line2 = y + p(43)
            line3 = y + p(62)

        # 第一行：类型徽标 + 文件名
        badge_text = item.kind_label
        badge_w = self.f_tiny.measure(badge_text) + p(14)
        badge_h = p(20)
        if theme.CLASSIC:
            badge_fill = theme.CARD
            badge_line = theme.BORDER_LIGHT
            badge_fg = theme.FG
        else:
            badge_fill = theme.blend(kind_color, theme.PANEL, 0.16)
            badge_line = theme.blend(kind_color, theme.PANEL, 0.34)
            badge_fg = kind_color
        round_rect(canvas, text_x, line1 - badge_h / 2, text_x + badge_w,
                   line1 + badge_h / 2, theme.small_radius(), fill=badge_fill,
                   outline=badge_line)
        canvas.create_text(text_x + badge_w / 2, line1, text=badge_text,
                           fill=badge_fg, font=self.f_tiny)
        name = title.split("] ", 1)[-1]
        name_x = text_x + badge_w + p(10)
        canvas.create_text(name_x, line1,
                           text=ellipsize(name, self.f_title_bold,
                                          right_w - badge_w - p(10)),
                           anchor="w", fill=theme.FG, font=self.f_title_bold)

        # 第二行：大小 / 码率 / 时长
        if detail:
            canvas.create_text(text_x, line2, text=ellipsize(
                detail, self.f_dim, right_w), anchor="w", fill=theme.FG_DIM,
                font=self.f_dim)
        # 第三行：URL
        url = item.url
        canvas.create_text(text_x, line3,
                           text=ellipsize(url, self.f_url, right_w),
                           anchor="w", fill=theme.FG_FAINT, font=self.f_url)

        # 进度条
        if getattr(item, "state", "") == "downloading":
            bar_x1, bar_x2 = text_x, text_x + right_w
            bar_h = max(4, p(4))
            bar_y = y + h - p(8)
            round_rect(canvas, bar_x1, bar_y, bar_x2, bar_y + bar_h, p(2),
                       fill=theme.BORDER, outline="")
            progress = max(0.0, min(1.0, getattr(item, "progress", 0) / 100.0))
            if progress > 0:
                round_rect(canvas, bar_x1, bar_y,
                           bar_x1 + max(4, (bar_x2 - bar_x1) * progress),
                           bar_y + bar_h, p(2), fill=theme.SUCCESS, outline="")
        elif getattr(item, "state", "") == "done":
            canvas.create_text(x2 - p(14), y + h / 2, text="✓ 已下载", anchor="e",
                               fill=theme.SUCCESS, font=self.f_tiny)
        elif getattr(item, "state", "") == "error":
            canvas.create_text(x2 - p(14), y + h / 2,
                               text=ellipsize(getattr(item, "message", "") or "失败",
                                              self.f_tiny, p(160)),
                               anchor="e", fill=theme.ERROR, font=self.f_tiny)

    def _draw_preview(self, item, x: float, y: float) -> None:
        canvas = self.canvas
        size = theme.THUMB_SIZE
        p = theme.px
        kind = getattr(item, "kind", "other")
        photo = self._thumb(item) if kind == "image" else None
        color = theme.KIND_COLORS.get(kind, theme.FG_DIM)
        if photo is not None:
            # 深色卡片做底，图片居中（小图标不被拉伸）
            round_rect(canvas, x, y, x + size, y + size, theme.small_radius(),
                       fill=theme.THUMB_BG, outline=theme.BORDER)
            iw, ih = photo.width(), photo.height()
            canvas.create_image(x + (size - iw) / 2, y + (size - ih) / 2,
                                anchor="nw", image=photo)
            return
        # 无图片：画深色卡片 + 类型图标 + 关键参数
        round_rect(canvas, x, y, x + size, y + size, theme.small_radius(),
                   fill=theme.THUMB_BG, outline=theme.BORDER)
        cx, cy = x + size / 2, y + size / 2
        icon, icon_color = _kind_icon(kind), color
        k = size / 64.0
        if kind == "audio":
            self._draw_waveform(cx, cy - 4 * k, color, k)
        elif kind == "video":
            canvas.create_polygon(cx - 7 * k, cy - 12 * k, cx + 10 * k, cy - 3 * k,
                                  cx - 7 * k, cy + 6 * k,
                                  fill=icon_color, outline="")
        elif kind == "stream":
            canvas.create_text(cx, cy - 10 * k, text="流", fill=icon_color,
                               font=(theme.FONT_FAMILY, max(8, int(11 * k)),
                                     "bold"))
            for i in range(3):
                canvas.create_arc(cx - (14 + i * 4) * k, cy - (4 + i * 4) * k,
                                  cx + (14 + i * 4) * k, cy + (4 + i * 4) * k,
                                  start=40, extent=100, style="arc",
                                  outline=icon_color, width=1)
        elif kind == "image":
            canvas.create_text(cx, cy, text=icon, fill=icon_color,
                               font=(theme.FONT_FAMILY, max(9, int(14 * k)),
                                     "bold"))
        else:
            canvas.create_text(cx, cy, text=icon, fill=icon_color,
                               font=(theme.FONT_FAMILY, max(9, int(13 * k)),
                                     "bold"))
        main, sub = item.preview_lines()
        if main:
            canvas.create_text(cx, y + size - 16, text=main, fill=theme.FG,
                               font=self.f_preview)
        if sub:
            canvas.create_text(cx, y + size - 6, text=sub, fill=theme.FG_DIM,
                               font=self.f_tiny)

    def _draw_waveform(self, cx: float, cy: float, color: str,
                       k: float = 1.0) -> None:
        heights = [4, 9, 14, 7, 16, 11, 5, 12, 8]
        canvas = self.canvas
        step = 4 * k
        start = cx - (len(heights) * step) / 2
        for i, h in enumerate(heights):
            x = start + i * step
            canvas.create_line(x, cy - h * k / 2, x, cy + h * k / 2, fill=color,
                               width=max(2, int(2 * k)), capstyle="round")

    # ------------------------------------------------------------------ 事件
    def _row_at(self, y: int) -> Optional[int]:
        index = int(y // theme.ROW_HEIGHT)
        if 0 <= index < len(self.items):
            return index
        return None

    def _on_wheel(self, event) -> None:
        delta = -1 if event.delta > 0 else 1
        steps = max(1, abs(event.delta) // 120)
        self._on_wheel_delta(delta * steps * 2)

    def _on_wheel_delta(self, steps: int) -> None:
        self.canvas.yview_scroll(steps, "units")
        self.redraw()

    def _on_motion(self, event) -> None:
        index = self._row_at(event.y)
        if index != self._hover:
            self._hover = index
            self.redraw()
            if index is None:
                self.canvas.configure(cursor="")
            else:
                item = self.items[index]
                self.canvas.configure(cursor="hand2")

    def _on_leave(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.redraw()

    def _on_click(self, event) -> None:
        index = self._row_at(event.y)
        if index is None:
            if not (event.state & 0x0004):
                self._selected.clear()
                self.redraw()
            return
        item = self.items[index]
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        if shift and self._selected:
            anchor = min(self._selected)
            lo, hi = sorted((anchor, index))
            self._selected = set(range(lo, hi + 1))
        elif ctrl:
            if index in self._selected:
                self._selected.discard(index)
            else:
                self._selected.add(index)
        else:
            if index not in self._selected:
                self._selected = {index}
            else:
                self._selected = {index}
        # 点击复选框区域或直接点击整行时都切换勾选（更符合直觉）
        item.checked = not item.checked
        self.redraw()
        self._on_change()

    def _on_double(self, event) -> None:
        index = self._row_at(event.y)
        if index is not None:
            self._on_open(self.items[index])

    def _on_right(self, event) -> None:
        index = self._row_at(event.y)
        if index is None:
            return
        if index not in self._selected:
            self._selected = {index}
            self.redraw()
        self._on_context(event, self.items[index])


def _kind_icon(kind: str) -> str:
    return {"image": "IMG", "audio": "♪", "video": "▶", "stream": "流",
            "other": "FILE"}.get(kind, "·")


class PlaceholderEntry(tk.Entry):
    """带占位提示文字的输入框。"""

    def __init__(self, master, placeholder: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self.placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        if not self.get():
            self._show_placeholder()

    def _show_placeholder(self):
        if self.get():
            return
        self._showing = True
        self.insert(0, self.placeholder)
        self.configure(fg=theme.FG_FAINT)

    def _on_focus_in(self, _e=None):
        if self._showing:
            self.delete(0, "end")
            self._showing = False
            self.configure(fg=theme.FG)

    def _on_focus_out(self, _e=None):
        if not self.get():
            self._show_placeholder()

    def value(self) -> str:
        return "" if self._showing else self.get().strip()

    def set_value(self, text: str) -> None:
        self.delete(0, "end")
        self._showing = False
        self.configure(fg=theme.FG)
        if text:
            self.insert(0, text)
        else:
            self._on_focus_out()


class SegmentedTabs(tk.Canvas):
    """类型筛选：胶囊式分段控件（全部 / 图片 / 音频 / 视频 / 流）。"""

    def __init__(self, master, options: list[tuple[str, str]],
                 command: Callable[[str], None]):
        try:
            bg = master.cget("bg")
        except tk.TclError:
            bg = theme.BG
        self._filter_style = theme.LAYOUT.get("filters", "segmented")
        self._height = theme.px(
            38 if self._filter_style == "capsule" else
            32 if self._filter_style == "chips" else
            30 if self._filter_style == "tabs" else 34)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=self._height, takefocus=0)
        self._command = command
        self._segments = list(options)
        self._current = options[0][0] if options else "all"
        # 单独记住每个页签的原始文案：绝不能再从控件当前文本反推，
        # 否则「全部 12」会被当成新文案，第二次刷新变成「全部 12 13」。
        self._base_labels: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._font = tkfont.Font(font=theme.FONT_SMALL)
        self._font_active = tkfont.Font(font=(theme.FONT_FAMILY, 9, "bold"))
        self._hover: Optional[str] = None
        self._boxes: list[tuple[float, float, str]] = []
        self._slide_from: Optional[tuple[float, float]] = None
        self._slide_to: Optional[tuple[float, float]] = None
        self._slide_t = 1.0
        self._slide_job = None
        self._lens_font: Optional[tkfont.Font] = None
        for key, label in options:
            self._base_labels[key] = label
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._refresh()

    # ------------------------------------------------------------------ 数据
    def select(self, key: str) -> None:
        old = next((box for box in self._boxes if box[2] == self._current), None)
        self._current = key
        self._slide_t = 1.0
        if old is not None and theme.ANIMATIONS and self._filter_style == "capsule":
            self._slide_from = (old[0], old[1])
            self._slide_to = None
            self._slide_t = 0.0
            self._start_slide()
        self._refresh()
        self._command(key)

    # ----------------------------------------------------- 胶囊指示器滑动
    def _start_slide(self) -> None:
        if self._slide_job is not None:
            try:
                self.after_cancel(self._slide_job)
            except tk.TclError:
                pass
            self._slide_job = None

        def step() -> None:
            self._slide_job = None
            if self._slide_t >= 1.0:
                self._refresh()
                return
            delay = theme.frame_delay()
            # 按帧间隔推进，保证 5–120 FPS 下切换时长都约 320ms
            self._slide_t = min(1.0, self._slide_t + delay / 320.0)
            self._refresh()
            try:
                self._slide_job = self.after(delay, step)
            except tk.TclError:
                self._slide_job = None

        try:
            self._slide_job = self.after(theme.frame_delay(), step)
        except tk.TclError:
            self._slide_job = None

    def set_counts(self, counts: dict) -> None:
        self._counts = dict(counts)
        self._refresh()

    def current(self) -> str:
        return self._current

    def label_of(self, key: str) -> str:
        """返回页签当前显示的完整文本（供自检使用）。"""
        return self._label_for(key)

    def _label_for(self, key: str) -> str:
        base = self._base_labels.get(key, key)
        count = self._counts.get(key, 0)
        return f"{base} {count}" if count else base

    # ------------------------------------------------------------------ 绘制
    def _refresh(self) -> None:
        self.delete("all")
        pad = theme.px(2)
        self._boxes = []
        total = pad
        for key, _label in self._segments:
            text = self._label_for(key)
            font = self._font_active if key == self._current else self._font
            width = font.measure(text) + theme.px(22)
            total += width
            self._boxes.append((total - width, total, key))
        total += pad
        self.configure(width=total)
        h = self._height
        style = self._filter_style
        if style == "chips":
            # 霓虹主题：无外框，每个页签是独立发光芯片
            for left, right, key in self._boxes:
                active = key == self._current
                outline = theme.ACCENT if active else theme.BORDER
                fill = (theme.blend(theme.ACCENT, theme.BG, 0.30) if active
                        else (theme.ROW_HOVER if key == self._hover
                              else theme.PANEL))
                round_rect(self, left + 2, pad + 1, right - 2, h - pad - 1,
                           theme.px(10), fill=fill, outline=outline,
                           width=1)
                font = self._font_active if active else self._font
                color = theme.ACCENT if active else theme.FG_DIM
                self.create_text((left + right) / 2, h / 2,
                                 text=self._label_for(key), fill=color,
                                 font=font)
            return
        if theme.CLASSIC or style == "tabs":
            self.create_rectangle(0, 0, total, h, fill=theme.SEGMENT_BG,
                                  outline=theme.BORDER_LIGHT)
            self.create_line(1, 1, total - 1, 1, fill="#ffffff")
        elif style == "capsule":
            glass_surface(self, 0, 0, total, h, theme.card_radius(),
                          theme.SEGMENT_BG,
                          phase=(time.time() * 0.12) % 1.0,
                          mode="glass", accent=theme.ACCENT)
            round_rect(self, 0, 0, total, h, theme.card_radius(),
                       fill="", outline=theme.BORDER_LIGHT, width=1)
        else:
            round_rect(self, 0, 0, total, h, theme.card_radius(),
                       fill=theme.SEGMENT_BG, outline="", width=1)
        active_box = next((b for b in self._boxes if b[2] == self._current),
                          None)
        if active_box is not None and self._slide_to is None:
            self._slide_to = (active_box[0], active_box[1])
        blob = self._blob_span() if style == "capsule" else None
        if blob is not None:
            self._draw_blob(blob[0], blob[1], pad, h)
        lens = self._bubble_geom(h) if (style == "capsule"
                                        and theme.ANIMATIONS) else None
        for left, right, key in self._boxes:
            active = key == self._current
            if theme.CLASSIC or style == "tabs":
                fill = theme.SEGMENT_ACTIVE if active else theme.SEGMENT_BG
                self.create_rectangle(left + 1, pad, right - 1, h - pad,
                                      fill=fill, outline=theme.BORDER_LIGHT)
                if active:
                    self.create_line(left + 2, pad + 1, right - 2, pad + 1,
                                     fill=theme.BORDER_LIGHT)
                    self.create_line(left + 2, pad + 1, left + 2, h - pad - 1,
                                     fill=theme.BORDER_LIGHT)
            elif active and style != "capsule":
                round_rect(self, left + 1, pad, right - 1, h - pad,
                           theme.button_radius(), fill=theme.SEGMENT_ACTIVE,
                           outline="")
            elif key == self._hover and not active:
                round_rect(self, left + 1, pad, right - 1, h - pad,
                           theme.button_radius(), fill=theme.ROW_HOVER,
                           outline="")
            center = (left + right) / 2
            # 球盖住的那一格不再画原字，改为球内放大版，才像真的透镜
            if lens is not None and abs(center - lens[0]) < lens[2] * 0.92:
                continue
            font = self._font_active if active else self._font
            if style == "capsule":
                # 文字颜色跟着液体块走：块还没到之前保持深色，别出现"白字消失了"
                on_blob = (blob is not None
                           and blob[0] - theme.px(6) <= center
                           <= blob[1] + theme.px(6))
                color = "#ffffff" if on_blob else (
                    theme.FG if active else theme.FG_DIM)
            else:
                color = theme.FG if active else theme.FG_DIM
            self.create_text(center, h / 2,
                             text=self._label_for(key), fill=color, font=font)
        if lens is not None:
            self._draw_bubble(pad, h, lens)

    # ------------------------------------------------ 液态玻璃：指示块与玻璃球
    def _blob_span(self):
        """液体指示块当前占据的横向范围（滑动时会被拉长）。"""
        box = next((b for b in self._boxes if b[2] == self._current), None)
        if box is None:
            return None
        left, right = box[0] + 2, box[1] - 2
        if (self._slide_t < 1.0 and self._slide_from and self._slide_to):
            t = max(0.0, min(1.0, self._slide_t))
            eased = 1 - (1 - t) ** 3
            a0 = self._slide_from[0] + 2
            a1 = self._slide_from[1] - 2
            b0 = self._slide_to[0] + 2
            b1 = self._slide_to[1] - 2
            left = a0 + (b0 - a0) * eased
            right = a1 + (b1 - a1) * eased
            grow = abs(b0 - a0) * 0.45 * math.sin(math.pi * t)
            left -= grow
            right += grow
        return (left, right)

    def _draw_blob(self, left: float, right: float, pad: int,
                   h: int) -> None:
        """液体指示块：几层由淡到浓，边缘像被抹开，而不是一块实心圆角矩形。"""
        accent = theme.ACCENT
        bar = theme.SEGMENT_BG
        radius = theme.px(15)
        if right - left < theme.px(8):
            right = left + theme.px(8)
        for expand, weight in ((-theme.px(6), 0.10), (-theme.px(3), 0.30),
                               (0, 1.0)):
            fill = (accent if weight >= 1.0
                    else theme.blend(accent, bar, weight))
            round_rect(self, left + expand, pad + 1 + expand * 0.35,
                       right - expand, h - pad - 1 - expand * 0.35,
                       max(theme.px(4), radius + expand * 0.3),
                       fill=fill, outline="")
        if right - left > theme.px(26):
            self.create_line(left + theme.px(10), pad + 2,
                             right - theme.px(10), pad + 2,
                             fill=theme.blend("#ffffff", accent, 0.45),
                             width=1)

    def _bubble_geom(self, h: int):
        """玻璃球当前的位置与形状：返回 (cx, cy, rx, ry, fade)，隐藏时 None。"""
        if not (self._slide_from and self._slide_to):
            return None
        if self._slide_t >= 1.0:
            return None
        t = max(0.0, min(1.0, self._slide_t))
        eased = 1 - (1 - t) ** 3
        cx_from = (self._slide_from[0] + self._slide_from[1]) / 2
        cx_to = (self._slide_to[0] + self._slide_to[1]) / 2
        cx = cx_from + (cx_to - cx_from) * eased
        cy = h / 2
        radius = h * 0.47
        travel = min(1.0, abs(cx_to - cx_from) / max(1.0, radius * 4.0))
        squash = 1.0 + 0.22 * math.sin(math.pi * min(1.0, t * 1.3)) * travel
        fade = 1.0 if t < 0.55 else max(0.0, 1.0 - (t - 0.55) / 0.45)
        if fade <= 0.02:
            return None
        return (cx, cy, radius * squash, radius / squash, fade)

    def _draw_bubble(self, pad: int, h: int, lens: tuple) -> None:
        """液态玻璃球：切换页签时从旧位置滚到新位置，途中把它盖住的内容放大。

        Tk 没有真正的折射：用「亮边 + 磨砂玻璃体 + 高光 + 焦散 + 放大的液体块
        和文字」拼出透镜观感。被球盖住的那一格的原字会先跳过，球里只画放大版，
        看起来就像内容被折射放大了。
        """
        cx, cy, rx, ry, fade = lens
        bar = theme.SEGMENT_BG
        accent = theme.ACCENT

        def toward(color: str, weight: float = 1.0) -> str:
            # 淡出 = 把颜色往胶囊底色里融
            return theme.blend(color, bar, fade * weight)

        body = toward(theme.blend("#ffffff", bar, 0.34))
        self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=body,
                         outline="")
        # 边缘一圈略深，做出玻璃的"厚度"
        self.create_oval(cx - rx * 0.99, cy - ry * 0.99,
                         cx + rx * 0.99, cy + ry * 0.99,
                         fill=toward(theme.blend(bar, "#ffffff", 0.65), 0.55),
                         outline="")
        self.create_oval(cx - rx * 0.82, cy - ry * 0.82,
                         cx + rx * 0.82, cy + ry * 0.82,
                         fill=body, outline="")
        # 亮边 + 外侧极细的暗边，让球从胶囊上"浮"起来
        self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry,
                         outline=toward("#ffffff", 0.95),
                         width=max(2, theme.px(2)))
        self.create_oval(cx - rx - 1, cy - ry - 1, cx + rx + 1, cy + ry + 1,
                         outline=toward(theme.blend(accent, "#000000", 0.25),
                                        0.35), width=1)
        # 放大后的液体块 + 文字
        box = min(self._boxes, key=lambda b: abs((b[0] + b[1]) / 2 - cx),
                  default=None)
        if box is None:
            return
        # 球里至少要有东西：先画一块放大的液体块，文字放得下再叠上去
        text = self._base_labels.get(box[2], "")
        active = box[2] == self._current
        if self._lens_font is None:
            size = max(8, int(round(theme.FONT_SMALL[1] * 1.15)))
            self._lens_font = tkfont.Font(
                family=theme.FONT_FAMILY, size=size, weight="bold")
        text_w = self._lens_font.measure(text) if text else 0
        blob_w = min(2 * rx * 0.82, max(theme.px(26), text_w + theme.px(18)))
        round_rect(self, cx - blob_w / 2, cy - ry * 0.52,
                   cx + blob_w / 2, cy + ry * 0.52, theme.px(10),
                   fill=toward(accent, 0.96), outline="")
        if text and text_w <= 2 * rx * 0.80:
            self.create_text(cx, cy, text=text, fill=toward("#ffffff", 0.97),
                             font=self._lens_font)
        # 左上镜面高光：贴着内缘，别糊在球中间的文字上
        hx, hy = cx - rx * 0.56, cy - ry * 0.60
        self.create_oval(hx - rx * 0.19, hy - ry * 0.11,
                         hx + rx * 0.19, hy + ry * 0.11,
                         fill=toward("#ffffff", 0.16), outline="")
        self.create_oval(hx - rx * 0.085, hy - ry * 0.048,
                         hx + rx * 0.085, hy + ry * 0.048,
                         fill=toward("#ffffff", 0.92), outline="")
        # 右下焦散：一道细小的暖白反光
        gx, gy = cx + rx * 0.60, cy + ry * 0.56
        self.create_oval(gx - rx * 0.13, gy - ry * 0.08,
                         gx + rx * 0.13, gy + ry * 0.08,
                         fill=toward("#fff3d6", 0.26), outline="")
        self.create_oval(gx - rx * 0.048, gy - ry * 0.028,
                         gx + rx * 0.048, gy + ry * 0.028,
                         fill=toward("#fffdf6", 0.80), outline="")

    # ------------------------------------------------------------------ 交互
    def _key_at(self, x: float) -> Optional[str]:
        for left, right, key in self._boxes:
            if left <= x <= right:
                return key
        return None

    def _on_click(self, event) -> None:
        key = self._key_at(event.x)
        if key is not None and key != self._current:
            self.select(key)

    def _on_motion(self, event) -> None:
        key = self._key_at(event.x)
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if key else "")
            self._refresh()

    def _on_leave(self, _event=None) -> None:
        if self._hover is not None:
            self._hover = None
            self.configure(cursor="")
            self._refresh()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._slide_job is not None:
            try:
                self.after_cancel(self._slide_job)
            except tk.TclError:
                pass
            self._slide_job = None


class SideTabs(tk.Canvas):
    """亚克力主题的左侧竖向筛选栏（与 SegmentedTabs 同接口）。"""

    def __init__(self, master, options: list[tuple[str, str]],
                 command: Callable[[str], None], width: int = 184):
        try:
            bg = master.cget("bg")
        except tk.TclError:
            bg = theme.BG
        self._row_h = theme.px(42)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         width=width, height=self._row_h * len(options) + 8,
                         takefocus=0)
        self._command = command
        self._segments = list(options)
        self._base_labels = {k: v for k, v in options}
        self._counts: dict[str, int] = {}
        self._current = options[0][0] if options else "all"
        self._hover: Optional[str] = None
        self._font = tkfont.Font(font=theme.FONT_SMALL)
        self._font_active = tkfont.Font(font=(theme.FONT_FAMILY, 10, "bold"))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self._refresh()

    def select(self, key: str) -> None:
        self._current = key
        self._refresh()
        self._command(key)

    def set_counts(self, counts: dict) -> None:
        self._counts = dict(counts)
        self._refresh()

    def current(self) -> str:
        return self._current

    def label_of(self, key: str) -> str:
        base = self._base_labels.get(key, key)
        count = self._counts.get(key, 0)
        return f"{base} {count}" if count else base

    def _row_at(self, y: float) -> Optional[str]:
        index = int((y - 4) // self._row_h)
        if 0 <= index < len(self._segments):
            return self._segments[index][0]
        return None

    def _refresh(self) -> None:
        self.delete("all")
        p = theme.px
        width = int(self.cget("width"))
        for index, (key, _label) in enumerate(self._segments):
            top = 4 + index * self._row_h
            active = key == self._current
            hover = key == self._hover
            if active:
                round_rect(self, p(4), top + p(3), width - p(4),
                           top + self._row_h - p(3), theme.px(12),
                           fill=theme.blend(theme.ACCENT, theme.PANEL, 0.24),
                           outline=theme.blend(theme.ACCENT, theme.PANEL, 0.55),
                           width=1)
                self.create_line(p(5), top + p(12), p(5),
                                 top + self._row_h - p(12),
                                 fill=theme.ACCENT, width=max(2, p(3)),
                                 capstyle="round")
            elif hover:
                round_rect(self, p(4), top + p(3), width - p(4),
                           top + self._row_h - p(3), theme.px(12),
                           fill=theme.ROW_HOVER, outline="")
            color = theme.FG if active else theme.FG_DIM
            font = self._font_active if active else self._font
            self.create_text(p(18), top + self._row_h / 2,
                             anchor="w", text=self.label_of(key),
                             fill=color, font=font)
            count = self._counts.get(key, 0)
            if count:
                self.create_text(width - p(16), top + self._row_h / 2,
                                 anchor="e", text=str(count),
                                 fill=theme.ACCENT if active else theme.FG_FAINT,
                                 font=theme.FONT_TINY)

    def _on_click(self, event) -> None:
        key = self._row_at(event.y)
        if key is not None and key != self._current:
            self.select(key)

    def _on_motion(self, event) -> None:
        key = self._row_at(event.y)
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if key else "")
            self._refresh()

    def _on_leave(self, _event=None) -> None:
        if self._hover is not None:
            self._hover = None
            self.configure(cursor="")
            self._refresh()


class DropdownButton(tk.Canvas):
    """带下拉菜单的圆角按钮（用于 User-Agent 选择）。

    Tk 原生的 OptionMenu/Menubutton 在深色主题下样式很难统一，
    这里用 Canvas 自绘按钮 + tk.Menu 弹出选项。
    """

    def __init__(self, master, options: list[tuple[str, str]],
                 command: Callable[[str], None], value: str = "",
                 width: int = 150, height: int = 38, prefix: str = "",
                 font=None):
        try:
            bg = master.cget("bg")
        except tk.TclError:
            bg = theme.PANEL
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self._bw, self._bh = width, height
        self._items = list(options)
        self._command = command
        self._value = value or (options[0][0] if options else "")
        self._prefix = prefix
        self._font = font or theme.FONT_SMALL
        self._hovering = False
        self._menu: tk.Menu | None = None
        self._draw()
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self.open_menu())
        self.bind("<Return>", lambda _e: self.open_menu())
        self.bind("<space>", lambda _e: self.open_menu())

    # ------------------------------------------------------------------ 外观
    def _set_hover(self, value: bool) -> None:
        self._hovering = value
        self._draw()

    def _label(self) -> str:
        for key, label in self._items:
            if key == self._value:
                return label
        return self._items[0][1] if self._items else ""

    def _draw(self) -> None:
        self.delete("all")
        p = theme.px
        fill = theme.CARD_HOVER if self._hovering else theme.CARD
        if theme.CLASSIC:
            self.create_rectangle(0, 0, self._bw, self._bh, fill=fill,
                                  outline=theme.BORDER_LIGHT)
            self.create_line(1, 1, self._bw - 1, 1, fill="#ffffff")
            self.create_line(1, 1, 1, self._bh - 1, fill="#ffffff")
            self.create_line(1, self._bh - 1, self._bw - 1, self._bh - 1,
                             fill=theme.BORDER_LIGHT)
            self.create_line(self._bw - 1, 1, self._bw - 1, self._bh - 1,
                             fill=theme.BORDER_LIGHT)
        else:
            outline = theme.BORDER_LIGHT if theme.ACRYLIC else theme.BORDER
            round_rect(self, 1, 1, self._bw - 1, self._bh - 1,
                       theme.button_radius(), fill=fill, outline=outline)
            if theme.GLASS:
                self.create_line(p(12), p(2), self._bw - p(12), p(2),
                                 fill=theme.blend(fill, "#ffffff", 0.45),
                                 width=max(1, p(1)), capstyle="round")
        arrow_w = p(16)
        text_w = self._bw - p(20) - arrow_w
        label = self._label()
        if self._prefix:
            label = f"{self._prefix}{label}"
        self.create_text(p(10), self._bh / 2, anchor="w",
                         text=ellipsize(label, tkfont.Font(font=self._font),
                                        text_w),
                         fill=theme.FG, font=self._font)
        cx = self._bw - p(12)
        cy = self._bh / 2
        d = p(4)
        self.create_polygon(cx - d, cy - d / 2, cx + d, cy - d / 2, cx, cy + d * 0.8,
                            fill=theme.FG_DIM, outline="")

    # ------------------------------------------------------------------ 交互
    def open_menu(self) -> None:
        """弹出选项菜单。"""
        menu = tk.Menu(self, tearoff=0, bg=theme.PANEL, fg=theme.FG,
                       activebackground=theme.ACCENT, activeforeground="#ffffff",
                       bd=0, font=self._font)
        for key, label in self._items:
            menu.add_radiobutton(
                label=label, value=key, variable=tk.StringVar(value=self._value),
                command=lambda k=key: self.select(k))
        self._menu = menu
        try:
            menu.tk_popup(self.winfo_rootx(),
                          self.winfo_rooty() + self._bh + theme.px(2))
        finally:
            menu.grab_release()

    def select(self, key: str) -> None:
        if key == self._value:
            return
        self._value = key
        self._draw()
        self._command(key)

    def value(self) -> str:
        return self._value

    def set_value(self, key: str) -> None:
        self._value = key
        self._draw()

    def set_options(self, options: list[tuple[str, str]]) -> None:
        self._items = list(options)
        self._draw()
