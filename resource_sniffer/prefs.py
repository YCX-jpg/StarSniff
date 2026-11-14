"""设置窗口：外观（主题 / 主题色 / 圆角 / 预览）、嗅探、网络、下载。"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox
from typing import Callable, Optional

from . import frameless, motion, smartproxy, theme, updater, useragents
from . import ytdlp
from . import __version__ as APP_VERSION
from .config import Config


# --------------------------------------------------------------------------
# 小组件
# --------------------------------------------------------------------------
class ThemeCard(tk.Canvas):
    """主题预设卡片：迷你窗口缩略图 + 名称 + 选中描边。"""

    def __init__(self, master, key: str, command: Callable[[str], None],
                 width: int = 0, height: int = 0):
        width = width or theme.px(116)
        height = height or theme.px(104)
        super().__init__(master, width=width, height=height, bg=theme.BG,
                         highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=0)
        self._key = key
        self._command = command
        self._selected = False
        self._hover = False
        self._bw, self._bh = width, height
        self.bind("<Button-1>", lambda _e: self._command(self._key))
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _draw(self) -> None:
        from .widgets import paint_wallpaper, round_rect

        self.delete("all")
        p = theme.px
        data = theme.palette_for(self._key, dark=theme.DARK_UI)
        colors = data["colors"]
        radius = p(min(8, data.get("window_radius", 6)))
        preview_bottom = self._bh - p(30)
        # 先铺这张主题自己的壁纸，缩略图就能看出各主题的壁纸差异
        paint_wallpaper(self, self._bw, preview_bottom,
                        spec=data.get("wallpaper"), tag="wall",
                        win=(self._bw, preview_bottom))
        # 壁纸按行铺，最后一行会超出 preview_bottom，用底色盖掉，给标签留出干净背景
        self.create_rectangle(0, preview_bottom, self._bw, self._bh,
                              fill=theme.BG, outline="")
        outline = theme.ACCENT if self._selected else (
            theme.BORDER_LIGHT if self._hover else theme.BORDER)
        width = max(2, p(2)) if self._selected else 1
        round_rect(self, 1, 1, self._bw - 1, preview_bottom, radius,
                   fill="", outline=outline, width=width)
        # 迷你标题栏
        bar_h = p(14)
        self.create_rectangle(p(2), p(2), self._bw - p(2), p(2) + bar_h,
                              fill=data.get("title_bg", colors["BG"]), outline="")
        self.create_text(p(7), p(2) + bar_h / 2, text="●", anchor="w",
                         fill=data["accent"], font=(theme.FONT_FAMILY, 7))
        # 迷你内容：磨砂卡片 + 两行文字 + 按钮
        content_top = p(2) + bar_h
        card_top = content_top + p(5)
        card_bottom = preview_bottom - p(14)
        round_rect(self, p(6), card_top, self._bw - p(6), card_bottom, p(5),
                   fill=(theme.blend("#ffffff", colors["PANEL"], 0.55)
                         if theme.luma(colors["BG"]) >= 0.45
                         else colors["PANEL"]), outline="")
        thumb_top = card_top + p(4)
        thumb_bottom = card_bottom - p(4)
        self.create_rectangle(p(10), thumb_top, p(32), thumb_bottom,
                              fill=data["accent"], outline="")
        self.create_line(p(40), thumb_top + p(5), self._bw - p(12),
                         thumb_top + p(5), fill=colors["FG"], width=max(2, p(2)))
        self.create_line(p(40), thumb_top + p(13), self._bw - p(26),
                         thumb_top + p(13), fill=colors["FG_FAINT"], width=1)
        btn_y = preview_bottom - p(7)
        round_rect(self, self._bw - p(44), btn_y - p(4), self._bw - p(8),
                   btn_y + p(4), p(min(6, data.get("button_radius", 4))),
                   fill=data["accent"], outline="")
        label = theme.PRESET_NAMES.get(self._key, self._key)
        self.create_text(self._bw / 2, self._bh - p(4), text=label, anchor="s",
                         fill=theme.ACCENT if self._selected else theme.FG_DIM,
                         font=(theme.FONT_FAMILY, 8))


class MiniSlider(tk.Canvas):
    """自绘滑杆：圆角轨道 + 主色填充 + 圆形滑块。"""

    def __init__(self, master, variable: tk.IntVar, low: int, high: int,
                 command: Callable[[], None], width: int = 220):
        super().__init__(master, width=width, height=theme.px(22),
                         bg=theme.BG, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=0)
        self._var = variable
        self._low = low
        self._high = high
        self._command = command
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        variable.trace_add("write", lambda *_: self._draw())

    def _fraction(self) -> float:
        span = max(1, self._high - self._low)
        return max(0.0, min(1.0, (self._var.get() - self._low) / span))

    def _on_drag(self, event) -> None:
        pad = theme.px(9)
        usable = max(1, self.winfo_width() - pad * 2)
        fraction = max(0.0, min(1.0, (event.x - pad) / usable))
        value = int(round(self._low + fraction * (self._high - self._low)))
        if value != self._var.get():
            self._var.set(value)
        self._command()

    def _draw(self) -> None:
        from .widgets import round_rect

        self.delete("all")
        p = theme.px
        width = max(self.winfo_width(), p(40))
        height = self.winfo_height()
        pad = p(9)
        cy = height / 2
        track = max(4, p(5))
        round_rect(self, pad, cy - track / 2, width - pad, cy + track / 2,
                   track / 2, fill=theme.SEGMENT_BG, outline="")
        x = pad + (width - pad * 2) * self._fraction()
        if x > pad + 1:
            round_rect(self, pad, cy - track / 2, x, cy + track / 2,
                       track / 2, fill=theme.ACCENT, outline="")
        r = p(6)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=theme.FG,
                         outline=theme.ACCENT, width=max(1, p(2)))


class _Swatch(tk.Canvas):
    def __init__(self, master, color: str, command: Callable[[str], None],
                 size: int = 0):
        size = size or theme.px(22)
        super().__init__(master, width=size, height=size, bg=theme.BG,
                         highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=0)
        self._color = color
        self._size = size
        self._selected = False
        self.bind("<Button-1>", lambda _e: command(color))
        self._draw()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._draw()

    def _draw(self) -> None:
        s = self._size
        p = theme.px
        self.delete("all")
        self.create_oval(p(2), p(2), s - p(2), s - p(2), fill=self._color,
                         outline="#ffffff" if self._selected else theme.BORDER,
                         width=max(2, p(2)) if self._selected else 1)
        if self._selected:
            self.create_line(s * 0.3, s * 0.52, s * 0.45, s * 0.67,
                             s * 0.72, s * 0.35, fill="#ffffff",
                             width=max(2, p(2)), capstyle="round")


class ToggleSwitch(tk.Canvas):
    """现代滑动开关：圆角轨道 + 圆形滑块，点击切换绑定的 BooleanVar。"""

    def __init__(self, master, variable: tk.BooleanVar,
                 command: Optional[Callable[[], None]] = None):
        self._tw = theme.px(46)
        self._th = theme.px(24)
        super().__init__(master, width=self._tw, height=self._th,
                         bg=theme.BG, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=0)
        self._var = variable
        self._command = command
        self._pos = 1.0 if bool(variable.get()) else 0.0
        self._job = None
        self._hover = False
        self.bind("<Button-1>", lambda _e: self.invoke())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        variable.trace_add("write", lambda *_: self._animate())
        self._draw()

    # -- 交互 ----------------------------------------------------------
    def invoke(self) -> None:
        try:
            self._var.set(not bool(self._var.get()))
        except tk.TclError:
            return
        if self._command is not None:
            self._command()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _animate(self) -> None:
        self._cancel()
        target = 1.0 if bool(self._var.get()) else 0.0
        if not theme.ANIMATIONS:
            self._pos = target
            self._draw()
            return
        self._step()

    def _step(self) -> None:
        target = 1.0 if bool(self._var.get()) else 0.0
        delta = target - self._pos
        if abs(delta) <= 0.02:
            self._pos = target
            self._job = None
            self._draw()
            return
        self._pos += delta * 0.45
        self._draw()
        try:
            self._job = self.after(theme.frame_delay(), self._step)
        except tk.TclError:
            self._job = None

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def destroy(self) -> None:  # noqa: D401 - Tk 生命周期
        self._cancel()
        super().destroy()

    # -- 绘制 ----------------------------------------------------------
    def _draw(self) -> None:
        from .widgets import round_rect

        self.delete("all")
        w, h = self._tw, self._th
        pad = max(1, theme.px(1))
        on = bool(self._var.get())
        if on:
            track = theme.ACCENT_HOVER if self._hover else theme.ACCENT
        else:
            track = theme.BORDER_LIGHT if self._hover else theme.BORDER
        round_rect(self, pad, pad, w - pad, h - pad, (h - 2 * pad) / 2,
                   fill=track, outline="")
        d = h - 2 * pad - theme.px(4)
        span = max(0, int(w - 2 * pad - theme.px(4) - d))
        x0 = pad + theme.px(2) + span * max(0.0, min(1.0, self._pos))
        y0 = (h - d) / 2
        self.create_oval(x0, y0, x0 + d, y0 + d, fill="#ffffff",
                         outline=theme.blend("#000000", "#ffffff", 0.14),
                         width=1)


class _ProgressBar(tk.Canvas):
    """细进度条：圆角轨道 + 主色填充，值域 0.0–1.0，带平滑过渡。"""

    def __init__(self, master, height: int = 8):
        self._h = theme.px(height)
        super().__init__(master, height=self._h, bg=theme.BG,
                         highlightthickness=0, bd=0)
        self._value = 0.0
        self._shown = 0.0
        self._job = None
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, fraction: float, animate: bool = True) -> None:
        try:
            self._value = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            self._value = 0.0
        if not animate or not theme.ANIMATIONS:
            self._shown = self._value
            self._draw()
            return
        if self._job is None:
            self._step()

    def _step(self) -> None:
        self._job = None
        delta = self._value - self._shown
        if abs(delta) <= 0.004:
            self._shown = self._value
            self._draw()
            return
        self._shown += delta * 0.4
        self._draw()
        try:
            self._job = self.after(16, self._step)
        except tk.TclError:
            self._job = None

    def _draw(self) -> None:
        from .widgets import round_rect

        try:
            self.delete("all")
        except tk.TclError:
            return
        width = max(self.winfo_width(), 1)
        height = self._h
        radius = height / 2
        round_rect(self, 0, 0, width, height, radius,
                   fill=theme.SEGMENT_BG, outline="")
        if self._shown > 0.002:
            filled = max(height, width * self._shown)
            round_rect(self, 0, 0, filled, height, radius,
                       fill=theme.ACCENT, outline="")


class ScrollPane(tk.Frame):
    """可滚动的设置页：Canvas + 细滚动条 + 鼠标滚轮。"""

    def __init__(self, master):
        from .widgets import SlimScrollbar

        super().__init__(master, bg=theme.BG)
        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        self.bar = SlimScrollbar(self, command=self._scroll,
                                 width=theme.px(8))
        self.bar.configure(bg=theme.BG)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y", padx=(theme.px(4), 0))
        self.body = tk.Frame(self.canvas, bg=theme.BG)
        self._win = self.canvas.create_window(0, 0, window=self.body,
                                              anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        # 平滑滚动状态
        self._scroll_job = None
        self._scroll_t0 = 0.0
        self._scroll_from = 0.0
        self._scroll_goal = 0.0
        self._scroll_total = 1.0
        self.body.bind("<Configure>", self._sync_region)
        self.canvas.bind("<Configure>", self._on_resize)
        self.bind_wheel()

    def _on_resize(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=max(1, event.width))
        self._sync_region()

    def _sync_region(self, _event=None) -> None:
        bbox = self.canvas.bbox("all")
        self.canvas.configure(scrollregion=bbox or (0, 0, 0, 0))

    def _scroll(self, what: str, value) -> None:
        try:
            if what == "moveto":
                self.canvas.yview_moveto(float(value))
            elif what == "scroll":
                self.canvas.yview_scroll(int(value), "units")
        except (tk.TclError, ValueError):
            pass

    def _on_wheel(self, event):
        bbox = self.canvas.bbox("all")
        if not bbox:
            return "break"
        total = max(1, bbox[3] - bbox[1])
        view = max(1, self.canvas.winfo_height())
        if total <= view + 2:
            return "break"
        step = -1 if event.delta > 0 else 1
        base = (self._scroll_goal if self._scroll_job is not None
                else self.canvas.canvasy(0))
        self._scroll_to(base + step * theme.px(56))
        return "break"

    # ---- 平滑滚动：滚过去而不是一帧跳过去
    SCROLL_TIME = 0.15

    def _cancel_scroll(self) -> None:
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except tk.TclError:
                pass
            self._scroll_job = None

    def _scroll_to(self, target: float, animate: bool = True) -> None:
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        total = max(1, bbox[3] - bbox[1])
        view = max(1, self.canvas.winfo_height())
        target = max(0.0, min(total - view, target))
        self._cancel_scroll()
        if not animate or not theme.ANIMATIONS:
            self._move_to(target, total)
            return
        self._scroll_from = self.canvas.canvasy(0)
        if abs(target - self._scroll_from) < 1.0:
            return
        self._scroll_goal = target
        self._scroll_total = total
        self._scroll_t0 = time.monotonic()
        self._scroll_tick()

    def _move_to(self, y: float, total: float) -> None:
        try:
            self.canvas.yview_moveto(max(0.0, min(1.0, y / max(1.0, total))))
        except tk.TclError:
            pass

    def _scroll_tick(self) -> None:
        self._scroll_job = None
        elapsed = (time.monotonic() - self._scroll_t0) / self.SCROLL_TIME
        if elapsed >= 1.0:
            self._move_to(self._scroll_goal, self._scroll_total)
            return
        eased = 1.0 - (1.0 - elapsed) ** 3
        y = self._scroll_from + (self._scroll_goal - self._scroll_from) * eased
        self._move_to(y, self._scroll_total)
        try:
            self._scroll_job = self.after(16, self._scroll_tick)
        except tk.TclError:
            self._scroll_job = None

    def bind_wheel(self, widget=None) -> None:
        widget = widget or self.body
        try:
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
        except tk.TclError:
            return
        for child in widget.winfo_children():
            self.bind_wheel(child)


class UpdateDialog(tk.Toplevel):
    """发现新版本时的应用内提示（不自动替换正在运行的程序）。"""

    def __init__(self, master, info: updater.UpdateInfo, config: Config,
                 on_save: Callable[[], None]):
        super().__init__(master)
        self.overrideredirect(True)
        self.title("发现新版本")
        self.configure(bg=theme.BG)
        try:
            self.attributes("-alpha", 0.0)     # 等界面搭好再淡入
        except tk.TclError:
            pass
        self.info = info
        self.cfg = config
        self.on_save = on_save
        self._closing = False
        self._drag_offset = None

        width, height = theme.px(600), theme.px(430)
        try:
            width = min(width, int(self.winfo_screenwidth() * 0.9))
            height = min(height, int(self.winfo_screenheight() * 0.85))
        except tk.TclError:
            pass
        self.geometry(f"{width}x{height}")
        self.transient(master)
        # 不用 grab_set：自动检查发现更新时不能抢走主窗口输入，
        # 否则用户点「设置」会像卡死一样点不动。
        self._build()
        self._border = frameless.add_window_border(self)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self.update_idletasks()
        self._center_on(master)
        self.after(30, lambda: motion.open_window(self, dy=theme.px(10),
                                                  duration=160, target=1.0))

    def _build(self) -> None:
        bar = tk.Frame(self, bg=theme.TITLE_BG, height=theme.px(40))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        title = tk.Label(bar, text="发现新版本", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG,
                         font=(theme.FONT_FAMILY, 11, "bold"))
        title.pack(side="left", padx=theme.px(16), pady=theme.px(7))
        close = frameless.WindowButton(
            bar, "close", self._close, width=theme.px(38),
            height=theme.px(28), bg=theme.TITLE_BG)
        close.pack(side="right", padx=theme.px(6), pady=theme.px(5))
        for widget in (bar, title):
            widget.bind("<Button-1>", lambda e: frameless.begin_drag(self, e))
            widget.bind("<B1-Motion>", lambda e: frameless.do_drag(self, e))
            widget.bind("<ButtonRelease-1>",
                        lambda _e: frameless.end_drag(self))
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.px(24),
                  pady=theme.px(18))
        row = tk.Frame(body, bg=theme.BG)
        row.pack(fill="x")
        badge = tk.Canvas(row, width=theme.px(46), height=theme.px(46),
                          bg=theme.BG, highlightthickness=0)
        badge.pack(side="left", padx=(0, theme.px(12)))
        badge.create_oval(theme.px(2), theme.px(2), theme.px(44),
                          theme.px(44), fill=theme.ACCENT, outline="")
        badge.create_text(theme.px(23), theme.px(23), text="↑",
                          fill="#ffffff",
                          font=(theme.FONT_FAMILY, 18, "bold"))
        text = tk.Frame(row, bg=theme.BG)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=f"v{APP_VERSION}  →  v{self.info.version}",
                 bg=theme.BG, fg=theme.FG,
                 font=(theme.FONT_FAMILY, 13, "bold")).pack(anchor="w")
        tk.Label(text, text=self.info.name or "StarSniff 新版本",
                 bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(anchor="w",
                                             pady=(theme.px(2), 0))

        notes_wrap = tk.Frame(body, bg=theme.PANEL, highlightthickness=1,
                              highlightbackground=theme.BORDER)
        notes_wrap.pack(fill="both", expand=True, pady=(theme.px(14), 0))
        notes = tk.Text(notes_wrap, bg=theme.PANEL, fg=theme.FG_DIM,
                        relief="flat", bd=0, font=theme.FONT_SMALL,
                        wrap="word", padx=theme.px(12),
                        pady=theme.px(10), height=8,
                        insertbackground=theme.FG)
        notes.insert("1.0", (self.info.notes or "（该版本没有填写更新说明）").strip())
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        if self.info.assets:
            names = "、".join(a.name for a in self.info.assets[:3])
            tk.Label(body, text="附件：" + names, bg=theme.BG,
                     fg=theme.FG_FAINT, font=theme.FONT_TINY,
                     anchor="w").pack(fill="x", pady=(theme.px(4), 0))

        footer = tk.Frame(body, bg=theme.BG)
        footer.pack(fill="x", pady=(theme.px(12), 0))
        from .widgets import AccentButton

        AccentButton(footer, "前往下载", command=self._open_download,
                     width=theme.px(104), height=theme.px(34),
                     radius=theme.button_radius(), bg=theme.BG).pack(side="right")
        AccentButton(footer, "稍后", command=self._close,
                     width=theme.px(76), height=theme.px(34),
                     radius=theme.button_radius(), color=theme.CARD,
                     hover=theme.CARD_HOVER, fg=theme.FG,
                     border=theme.BORDER, bg=theme.BG).pack(
            side="right", padx=(0, theme.px(8)))
        skip = tk.Label(footer, text="跳过此版本", bg=theme.BG,
                        fg=theme.FG_FAINT, font=theme.FONT_TINY,
                        cursor="hand2")
        skip.pack(side="left")
        skip.bind("<Button-1>", lambda _e: self._skip())

    def _open_download(self) -> None:
        try:
            webbrowser.open(self.info.download_url)
        except Exception:
            pass
        self._close()

    def _skip(self) -> None:
        self.cfg.skipped_version = self.info.version
        try:
            self.on_save()
        except Exception:
            pass
        self._close()

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        motion.close_window(self, self._finish_close, dy=theme.px(6),
                            duration=120)

    def _finish_close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _center_on(self, master) -> None:
        try:
            width, height = self.winfo_width(), self.winfo_height()
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 2
            x = max(0, min(x, self.winfo_screenwidth() - width))
            y = max(0, min(y, self.winfo_screenheight() - height))
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# 设置窗口
# --------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    def __init__(self, master, config: Config, on_apply: Callable[[Config], None],
                 on_save: Optional[Callable[[], None]] = None):
        super().__init__(master)
        self.overrideredirect(True)
        self.title("设置")
        self.configure(bg=theme.BG)
        try:
            self.attributes("-alpha", 0.0)     # 等界面搭好再淡入
        except tk.TclError:
            pass
        self.cfg = config
        self.on_apply = on_apply
        self.on_save = on_save or (lambda: None)
        self._closing = False
        self._drag_offset = None
        self._test_payload = None
        self._download_dir = config.download_dir
        self._page = "appearance"
        self._preview_job = None
        self._preview_pulse = 0.0
        self._preview_key = None

        # ---- 本地状态
        self.var_preset = tk.StringVar(value=config.theme_preset)
        self.var_accent = tk.StringVar(value=config.accent)
        self.var_radius_button = tk.IntVar(value=config.radius_button)
        self.var_radius_window = tk.IntVar(value=config.radius_window)
        self.var_animations = tk.BooleanVar(value=config.animations)
        self.var_anim_fps = tk.IntVar(
            value=max(5, min(120, int(getattr(config, "anim_fps", 60)))))
        self.var_dynamic = tk.BooleanVar(value=config.dynamic)
        self.var_deep = tk.BooleanVar(value=config.deep)
        self.var_scroll = tk.BooleanVar(value=config.scroll)
        self.var_headless = tk.BooleanVar(value=config.headless)
        self.var_ytdlp = tk.BooleanVar(value=config.use_ytdlp)
        self.var_check_updates = tk.BooleanVar(value=config.check_updates)
        self.var_proxy_mode = tk.StringVar(
            value=smartproxy.normalize_mode(config.proxy_mode))
        self._update_payload = None
        self._update_checking = False
        self._ytdlp_payload = None
        self._ytdlp_downloading = False
        self._ytdlp_cancel = threading.Event()
        self._ytdlp_job = None

        width, height = theme.px(860), theme.px(640)
        try:
            width = min(width, int(self.winfo_screenwidth() * 0.92))
            height = min(height, int(self.winfo_screenheight() * 0.90))
        except tk.TclError:
            pass
        self.geometry(f"{width}x{height}")
        self.resizable(False, False)
        self.transient(master)

        self._build_titlebar()
        self._build_body()
        self._border = frameless.add_window_border(self)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Alt-F4>", lambda _e: self._close())
        self._show_page("appearance")
        self.update_idletasks()
        self._center_on(master)
        # 先让窗口完成映射/置前，再在下一帧开始淡入（不做 modal，主界面照常可用）
        self.after(20, self._activate_window)
        self.after(40, lambda: motion.open_window(self, dy=theme.px(10),
                                                  duration=170, target=1.0))

    # ------------------------------------------------------------------ 外壳
    def _build_titlebar(self) -> None:
        bar = tk.Frame(self, bg=theme.TITLE_BG, height=theme.px(40))
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        title = tk.Label(bar, text="设置", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG,
                         font=(theme.FONT_FAMILY, 11, "bold"))
        title.pack(side="left", padx=theme.px(18), pady=theme.px(8))
        close = frameless.WindowButton(
            bar, "close", self._close, width=theme.px(40),
            height=theme.px(30), bg=theme.TITLE_BG)
        close.pack(side="right", padx=(0, theme.px(6)), pady=theme.px(5))
        for widget in (bar, title):
            widget.bind("<Button-1>", lambda e: frameless.begin_drag(self, e))
            widget.bind("<B1-Motion>", lambda e: frameless.do_drag(self, e))
            widget.bind("<ButtonRelease-1>",
                        lambda _e: frameless.end_drag(self))
        tk.Frame(self, bg=theme.BORDER, height=1).pack(fill="x")

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True)
        nav = tk.Frame(body, bg=theme.BG, width=theme.px(148))
        nav.pack(side="left", fill="y", padx=(theme.px(10), 0),
                 pady=theme.px(10))
        nav.pack_propagate(False)
        self._nav: dict[str, tk.Label] = {}
        for key, label in (("appearance", "外观"), ("sniff", "嗅探"),
                           ("network", "网络"), ("download", "下载"),
                           ("about", "关于")):
            lbl = tk.Label(nav, text=label, bg=theme.BG, fg=theme.FG_DIM,
                           font=theme.FONT_SMALL, anchor="w",
                           padx=theme.px(12), pady=theme.px(8),
                           cursor="hand2")
            lbl.pack(fill="x", pady=theme.px(1))
            lbl.bind("<Button-1>", lambda _e, k=key: self._show_page(k))
            lbl.bind("<Enter>", lambda _e, k=key: self._nav_hover(k, True))
            lbl.bind("<Leave>", lambda _e, k=key: self._nav_hover(k, False))
            self._nav[key] = lbl
        self._content = tk.Frame(body, bg=theme.BG)
        self._content.pack(side="left", fill="both", expand=True,
                           padx=theme.px(12), pady=theme.px(10))
        self._pages: dict[str, ScrollPane] = {}
        for key, builder in (("appearance", self._build_appearance),
                             ("sniff", self._build_sniff),
                             ("network", self._build_network),
                             ("download", self._build_download),
                             ("about", self._build_about)):
            pane = ScrollPane(self._content)
            builder(pane.body)
            pane.bind_wheel()
            self._pages[key] = pane
        footer = tk.Frame(self, bg=theme.BG)
        footer.pack(fill="x", side="bottom", before=body)
        tk.Frame(footer, bg=theme.BORDER, height=1).pack(fill="x")
        row = tk.Frame(footer, bg=theme.BG)
        row.pack(fill="x", padx=theme.px(18), pady=theme.px(10))
        from .widgets import AccentButton

        self.btn_apply = AccentButton(
            row, "应用", command=self._apply, width=theme.px(96),
            height=theme.px(34), radius=theme.button_radius())
        self.btn_apply.pack(side="right")
        self.btn_cancel = AccentButton(
            row, "取消", command=self._close, width=theme.px(84),
            height=theme.px(34), color=theme.CARD, hover=theme.CARD_HOVER,
            fg=theme.FG, radius=theme.button_radius(), border=theme.BORDER)
        self.btn_cancel.pack(side="right", padx=(0, theme.px(10)))

    def _show_page(self, key: str) -> None:
        if key not in self._pages:
            return
        self._page = key
        for name, page in self._pages.items():
            if name == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        pane = self._pages.get(key)
        if pane is not None:
            pane.bind_wheel()
            pane._sync_region()
        for name, lbl in self._nav.items():
            active = name == key
            lbl.configure(bg=theme.SEGMENT_ACTIVE if active else theme.BG,
                          fg=theme.FG if active else theme.FG_DIM)
        if key == "appearance":
            self._update_preview()

    def _nav_hover(self, key: str, entering: bool) -> None:
        lbl = self._nav.get(key)
        if lbl is None or key == self._page:
            return
        lbl.configure(bg=theme.ROW_HOVER if entering else theme.BG,
                      fg=theme.FG if entering else theme.FG_DIM)

    # ------------------------------------------------------------------ 外观页
    def _heading(self, parent, text: str) -> None:
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", pady=(theme.px(12), theme.px(4)))
        bar = tk.Frame(row, bg=theme.ACCENT, width=theme.px(3),
                       height=theme.px(13))
        bar.pack(side="left", padx=(0, theme.px(8)))
        bar.pack_propagate(False)
        tk.Label(row, text=text, bg=theme.BG, fg=theme.FG,
                 font=(theme.FONT_FAMILY, 10, "bold")).pack(side="left")

    def _build_appearance(self, page) -> None:
        self._heading(page, "主题预设")
        cards = tk.Frame(page, bg=theme.BG)
        cards.pack(fill="x")
        self._cards: dict[str, ThemeCard] = {}
        for key in theme.PRESET_KEYS:
            card = ThemeCard(cards, key, self._choose_preset)
            card.pack(side="left", padx=(0, theme.px(8)))
            self._cards[key] = card

        # 深浅变体：每套主题都有明暗两版
        self.var_dark = tk.BooleanVar(
            value=bool(getattr(self.cfg, "theme_dark", False)))
        self._toggle(page, "深色主题", self.var_dark,
                     hint="关掉即为该主题的浅色版本，标题栏一起变",
                     command=self._dark_changed)

        self.var_auto_ytdlp = tk.BooleanVar(
            value=bool(getattr(self.cfg, "auto_update_ytdlp", True)))
        self.var_auto_app = tk.BooleanVar(
            value=bool(getattr(self.cfg, "auto_update_app", True)))
        self._heading(page, "自动更新")
        self._toggle(page, "启动时自动更新 yt-dlp", self.var_auto_ytdlp,
                     hint="静默拉取官方最新版；下载失败不影响正常使用")
        self._toggle(page, "启动时检查软件更新", self.var_auto_app,
                     hint="发现更高版本才提醒，不会自动替换程序")

        self._heading(page, "主题色")
        swatch_row = tk.Frame(page, bg=theme.BG)
        swatch_row.pack(fill="x")
        self._swatches: list[_Swatch] = []
        for _name, color in theme.ACCENTS:
            swatch = _Swatch(swatch_row, color, self._choose_accent)
            swatch.pack(side="left", padx=(0, theme.px(7)))
            self._swatches.append(swatch)
        follow = tk.Label(swatch_row, text="跟随预设", bg=theme.BG,
                          fg=theme.FG_FAINT, font=theme.FONT_TINY,
                          cursor="hand2")
        follow.pack(side="left", padx=(theme.px(8), 0))
        follow.bind("<Button-1>", lambda _e: self._choose_accent(""))

        self._heading(page, "圆角与动画")
        self._radius_row(page, "按钮圆角", self.var_radius_button, 0, 28)
        self._radius_row(page, "窗口圆角", self.var_radius_window, 0, 30)

        self._toggle(page, "启用悬停 / 主题切换动画", self.var_animations,
                     hint="关闭后切换更快，也不占用显卡",
                     command=self._schedule_preview)

        fps_row = tk.Frame(page, bg=theme.BG)
        fps_row.pack(fill="x", pady=(theme.px(4), 0))
        tk.Label(fps_row, text="动画帧率", bg=theme.BG, fg=theme.FG_DIM,
                 width=8, anchor="w",
                 font=theme.FONT_SMALL).pack(side="left")
        self.fps_value = tk.Label(fps_row, text=f"{self.var_anim_fps.get()} FPS",
                                  bg=theme.BG, fg=theme.FG,
                                  width=6, font=theme.FONT_SMALL)
        self.fps_value.pack(side="right")
        MiniSlider(fps_row, self.var_anim_fps, 5, 120,
                   self._fps_changed, width=theme.px(220)).pack(
            side="left", padx=theme.px(10), fill="x", expand=True)
        self.var_anim_fps.trace_add(
            "write", lambda *_: self._fps_changed())

        self._heading(page, "实时预览")
        self.preview = tk.Canvas(page, bg=theme.BG, height=theme.px(178),
                                 highlightthickness=0, bd=0)
        self.preview.pack(fill="x", expand=True)
        self.preview.bind("<Configure>", lambda _e: self._schedule_preview())
        reset = tk.Label(page, text="恢复默认外观", bg=theme.BG,
                         fg=theme.FG_FAINT, font=theme.FONT_TINY,
                         cursor="hand2")
        reset.pack(anchor="e", pady=(theme.px(2), 0))
        reset.bind("<Button-1>", lambda _e: self._reset_appearance())
        self._refresh_appearance()

    def _radius_row(self, parent, label: str, var: tk.IntVar,
                    low: int, high: int) -> None:
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text=label, bg=theme.BG, fg=theme.FG_DIM, width=8,
                 anchor="w", font=theme.FONT_SMALL).pack(side="left")
        value = tk.Label(row, text=str(var.get()), bg=theme.BG, fg=theme.FG,
                         width=3, font=theme.FONT_SMALL)
        value.pack(side="right")
        slider = MiniSlider(row, var, low, high, self._schedule_preview,
                            width=theme.px(220))
        slider.pack(side="left", padx=theme.px(10), fill="x", expand=True)
        var.trace_add("write", lambda *_: (value.configure(text=str(var.get())),
                                           self._schedule_preview()))

    def _refresh_appearance(self) -> None:
        preset = self.var_preset.get()
        data = theme.palette_for(preset, self.var_accent.get(),
                                 dark=bool(self.var_dark.get()))
        for key, card in self._cards.items():
            card.set_selected(key == preset)
        accent = (self.var_accent.get() or data["accent"]).lower()
        for swatch in self._swatches:
            swatch.set_selected(swatch._color.lower() == accent)

    def _choose_preset(self, key: str) -> None:
        data = theme.PRESETS.get(key) or theme.PRESETS["acrylic"]
        self.var_preset.set(key)
        self.var_accent.set("")
        self.var_radius_button.set(int(data.get("button_radius", 10)))
        self.var_radius_window.set(int(data.get("window_radius", 0)))
        self._refresh_appearance()
        self._schedule_preview()

    def _dark_changed(self) -> None:
        self._refresh_appearance()
        self._schedule_preview()

    def _choose_accent(self, color: str) -> None:
        self.var_accent.set(color)
        self._refresh_appearance()
        self._schedule_preview()

    def _fps_changed(self) -> None:
        try:
            self.fps_value.configure(text=f"{self.var_anim_fps.get()} FPS")
        except tk.TclError:
            pass
        self._schedule_preview()

    def _reset_appearance(self) -> None:
        self.var_preset.set("acrylic")
        self.var_accent.set("")
        self.var_dark.set(bool(getattr(self.cfg, "theme_dark", False)))
        self.var_radius_button.set(14)
        self.var_radius_window.set(18)
        self.var_animations.set(True)
        self.var_anim_fps.set(60)
        self._refresh_appearance()
        self._schedule_preview()

    # ---- 预览
    def _schedule_preview(self, *_args) -> None:
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except tk.TclError:
                pass
        try:
            self._preview_job = self.after(30, self._update_preview)
        except tk.TclError:
            self._preview_job = None

    def _update_preview(self) -> None:
        self._preview_job = None
        canvas = getattr(self, "preview", None)
        if canvas is None:
            return
        data = theme.palette_for(self.var_preset.get(), self.var_accent.get(),
                                 dark=bool(self.var_dark.get()))
        colors = data["colors"]
        p = theme.px
        import math

        from .widgets import paint_wallpaper, round_rect

        width = max(canvas.winfo_width(), p(200))
        height = max(canvas.winfo_height(), p(120))
        win_r = p(min(24, self.var_radius_window.get()))
        btn_r = p(min(20, self.var_radius_button.get()))
        x1, y1, x2, y2 = p(8), p(6), width - p(8), height - p(6)
        # 静态部分只在预设/尺寸变化时重画：一张壁纸就是几百个图元，
        # 每帧重画会让设置窗口吃满 CPU。
        key = (self.var_preset.get(), self.var_accent.get(), width, height,
               win_r, btn_r)
        if key != self._preview_key:
            self._preview_key = key
            canvas.delete("all")
            # 主题壁纸铺满预览，窗口本身画成“浮在壁纸上的玻璃”
            paint_wallpaper(canvas, width, height, spec=data.get("wallpaper"),
                            tag="wall")
            round_rect(canvas, x1, y1, x2, y2, win_r, fill=colors["BG"],
                       outline=colors["BORDER"], width=1, tags=("static",))
            # 标题栏
            title_h = p(26)
            round_rect(canvas, x1 + 1, y1 + 1, x2 - 1, y1 + title_h,
                       max(0, win_r - 1),
                       fill=data.get("title_bg", colors["BG"]), outline="",
                       tags=("static",))
            canvas.create_text(x1 + p(10), y1 + title_h / 2, text="●",
                               anchor="w", fill=data["accent"],
                               font=(theme.FONT_FAMILY, 7), tags=("static",))
            canvas.create_text(x1 + p(24), y1 + title_h / 2,
                               text="StarSniff 星嗅", anchor="w",
                               fill=data.get("title_fg", colors["FG"]),
                               font=(theme.FONT_FAMILY, 8, "bold"),
                               tags=("static",))
            for i, glyph in enumerate(("—", "□", "×")):
                canvas.create_text(x2 - p(14) - i * p(16),
                                   y1 + title_h / 2, text=glyph,
                                   fill=data.get("title_sub", colors["FG_FAINT"]),
                                   font=(theme.FONT_FAMILY, 7),
                                   tags=("static",))
            # 内容卡片
            card_y = y1 + title_h + p(8)
            card_h = p(48)
            round_rect(canvas, x1 + p(10), card_y, x2 - p(10), card_y + card_h,
                       btn_r, fill=colors["PANEL"], outline=colors["BORDER"],
                       tags=("static",))
            thumb = card_h - p(10)
            round_rect(canvas, x1 + p(15), card_y + p(5),
                       x1 + p(15) + thumb, card_y + p(5) + thumb,
                       p(min(8, self.var_radius_button.get())),
                       fill=data["accent"], outline="", tags=("static",))
            text_x = x1 + p(20) + thumb
            canvas.create_text(text_x, card_y + p(16), anchor="w",
                               text="示例视频.mp4", fill=colors["FG"],
                               font=(theme.FONT_FAMILY, 9, "bold"),
                               tags=("static",))
            canvas.create_text(text_x, card_y + p(31), anchor="w",
                               text="1280×720 · 12.4 MB",
                               fill=colors["FG_FAINT"],
                               font=(theme.FONT_FAMILY, 8), tags=("static",))
            # 进度条底 + 按钮
            bar_y = card_y + card_h + p(14)
            round_rect(canvas, x1 + p(10), bar_y, x2 - p(160), bar_y + p(4),
                       p(2), fill=colors["SEGMENT_BG"], outline="",
                       tags=("static",))
            btn_y = bar_y + p(16)
            round_rect(canvas, x2 - p(146), btn_y, x2 - p(78), btn_y + p(28),
                       btn_r, fill=data["accent"], outline="",
                       tags=("static",))
            canvas.create_text(x2 - p(112), btn_y + p(14), text="下载",
                               fill="#ffffff",
                               font=(theme.FONT_FAMILY, 9, "bold"),
                               tags=("static",))
            round_rect(canvas, x2 - p(70), btn_y, x2 - p(10), btn_y + p(28),
                       btn_r, fill=colors["PANEL_ALT"],
                       outline=colors["BORDER"], tags=("static",))
            canvas.create_text(x2 - p(40), btn_y + p(14), text="选择",
                               fill=colors["FG_DIM"],
                               font=(theme.FONT_FAMILY, 9), tags=("static",))
        # 会动的部分：只重画进度条和小光点
        canvas.delete("live")
        bar_y = y1 + p(26) + p(8) + p(48) + p(14)
        progress = 0.35 + 0.45 * (0.5 + 0.5 * math.sin(self._preview_pulse))
        round_rect(canvas, x1 + p(10), bar_y,
                   x1 + p(10) + (x2 - p(160) - x1 - p(10)) * progress,
                   bar_y + p(4), p(2), fill=data["accent"], outline="",
                   tags=("live",))
        dot_x = x1 + p(18) + int((x2 - x1 - p(36)) *
                                 ((self._preview_pulse * 0.5) % 1.0))
        canvas.create_oval(dot_x - p(2), y2 - p(14) - p(2),
                           dot_x + p(2), y2 - p(14) + p(2),
                           fill=data["accent"], outline="", tags=("live",))
        if theme.ANIMATIONS:
            try:
                fps = max(5, min(120, int(self.var_anim_fps.get())))
            except (tk.TclError, ValueError):
                fps = theme.ANIM_FPS
            delay = max(4, int(round(1000.0 / fps)))
            # 按帧间隔推进相位，5–120 FPS 下动画速度保持一致
            self._preview_pulse = (self._preview_pulse
                                   + delay / 1000.0 * 2.5) % (math.pi * 20)
            try:
                self._preview_job = self.after(delay, self._update_preview)
            except tk.TclError:
                self._preview_job = None

    # ------------------------------------------------------------------ 嗅探页
    def _build_sniff(self, page) -> None:
        self._heading(page, "嗅探方式")
        for text, hint, var in (
                ("动态嗅探", "无头浏览器，能抓到 JS 异步加载的资源",
                 self.var_dynamic),
                ("深度解析", "CSS / JS / JSON 里的直链也一并提取",
                 self.var_deep),
                ("自动滚动页面", "触发懒加载，抓得更全但要等更久",
                 self.var_scroll),
                ("后台静默模式", "不显示浏览器窗口", self.var_headless)):
            self._toggle(page, text, var, hint=hint)

        self._heading(page, "参数")
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="页面加载后等待（秒）", bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(side="left")
        self.spin_wait = self._spin(row, 1, 60, int(self.cfg.dynamic_wait))
        self.spin_wait.pack(side="left", padx=(theme.px(8), theme.px(18)))
        tk.Label(row, text="最大资源数", bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(side="left")
        self.spin_max = self._spin(row, 50, 10000, self.cfg.max_items)
        self.spin_max.pack(side="left", padx=theme.px(8))

        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="最小图片边长", bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(side="left")
        self.spin_min = self._spin(row, 0, 4096, self.cfg.min_image_side)
        self.spin_min.pack(side="left", padx=theme.px(8))
        tk.Label(row, text="px（0 = 不过滤小图标）", bg=theme.BG,
                 fg=theme.FG_FAINT, font=theme.FONT_SMALL).pack(side="left")

        self._heading(page, "yt-dlp")
        self._toggle(page, "优先使用 yt-dlp", self.var_ytdlp,
                     hint="支持时能列出剧集与清晰度")

        yt_row = tk.Frame(page, bg=theme.BG)
        yt_row.pack(fill="x", pady=(theme.px(4), 0))
        self.ytdlp_status = tk.Label(
            yt_row, text=ytdlp.describe(), bg=theme.BG,
            fg=theme.SUCCESS if ytdlp.available() else theme.FG_FAINT,
            font=theme.FONT_TINY, anchor="w", justify="left",
            wraplength=theme.px(350))
        self.ytdlp_status.pack(side="left", fill="x", expand=True)
        self.btn_ytdlp = tk.Button(
            yt_row, text="重新下载" if ytdlp.available() else "一键下载 yt-dlp",
            bg=theme.CARD, fg=theme.FG_DIM, relief="flat", bd=0,
            font=theme.FONT_SMALL, padx=10, pady=3,
            activebackground=theme.CARD_HOVER, activeforeground=theme.FG,
            cursor="hand2", command=self._download_ytdlp)
        self.btn_ytdlp.pack(side="right", padx=(theme.px(8), 0))

        # 下载进度条：只有真的在下载时才推进，完成后保持满格
        self.ytdlp_progress = _ProgressBar(page)
        self.ytdlp_progress.pack(fill="x", pady=(theme.px(8), theme.px(10)))

        tk.Label(page, text="点「一键下载」会从 GitHub 拉取官方 yt-dlp.exe 到软件目录；"
                            "下载期间可继续操作，完成后立即启用。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 anchor="w", justify="left", wraplength=theme.px(430)
                 ).pack(fill="x", pady=(theme.px(4), 0))

    def _refresh_ytdlp_status(self) -> None:
        try:
            available = ytdlp.available()
            self.ytdlp_status.configure(
                text=ytdlp.describe(),
                fg=theme.SUCCESS if available else theme.FG_FAINT)
            if not self._ytdlp_downloading:
                self.btn_ytdlp.configure(
                    text="重新下载" if available else "一键下载 yt-dlp",
                    state="normal")
        except tk.TclError:
            pass

    def _download_ytdlp(self) -> None:
        if self._ytdlp_downloading:
            return
        self._ytdlp_downloading = True
        self._ytdlp_payload = None
        self._ytdlp_cancel = threading.Event()
        self.btn_ytdlp.configure(text="下载中…", state="disabled")
        self.ytdlp_status.configure(text="正在连接 GitHub…", fg=theme.FG_DIM)
        try:
            self.ytdlp_progress.set(0.0, animate=False)
        except (AttributeError, tk.TclError):
            pass
        proxy = ""
        direct = False
        try:
            mode = self.var_proxy_mode.get()
            proxy = self.entry_proxy.get().strip()
            direct = mode == smartproxy.DIRECT
        except Exception:
            pass
        cancel = self._ytdlp_cancel

        def work() -> None:
            try:
                path = ytdlp.download_latest(
                    progress=lambda done, total: setattr(
                        self, "_ytdlp_payload", ("progress", done, total)),
                    cancel=cancel.is_set, proxy=proxy, direct=direct)
                self._ytdlp_payload = ("done", path)
            except Exception as exc:
                self._ytdlp_payload = ("error", str(exc))

        threading.Thread(target=work, daemon=True,
                         name="ytdlp-install").start()
        self._ytdlp_job = self.after(120, self._poll_ytdlp_download)

    def _poll_ytdlp_download(self) -> None:
        self._ytdlp_job = None
        try:
            if not self.winfo_exists():
                return
            payload = self._ytdlp_payload
            if payload is None:
                self._ytdlp_job = self.after(120, self._poll_ytdlp_download)
                return
            if payload[0] == "progress":
                _, done, total = payload
                if total:
                    text = f"下载中 {done * 100 // total}%（{done / 1048576:.1f} MB）"
                    self.ytdlp_progress.set(done / total)
                else:
                    text = f"下载中 {done / 1048576:.1f} MB"
                self.ytdlp_status.configure(text=text, fg=theme.FG_DIM)
                self._ytdlp_payload = None
                self._ytdlp_job = self.after(120, self._poll_ytdlp_download)
                return
            self._ytdlp_downloading = False
            if payload[0] == "done":
                self.ytdlp_progress.set(1.0)
                self.var_ytdlp.set(True)
                self.cfg.use_ytdlp = True
                self.on_save()
                self.ytdlp_status.configure(
                    text="已下载并启用：" + os.path.basename(str(payload[1])),
                    fg=theme.SUCCESS)
                self._refresh_ytdlp_status()
            else:
                self.ytdlp_status.configure(
                    text="下载失败：" + str(payload[1])[:120], fg=theme.ERROR)
                self.ytdlp_progress.set(0.0, animate=False)
                self._refresh_ytdlp_status()
        except tk.TclError:
            pass

    def _spin(self, parent, low: int, high: int, value: int) -> tk.Spinbox:
        spin = tk.Spinbox(parent, from_=low, to=high, width=6, bg=theme.CARD,
                          fg=theme.FG, relief="flat", bd=4,
                          insertbackground=theme.FG,
                          buttonbackground=theme.CARD)
        spin.delete(0, "end")
        spin.insert(0, str(value))
        return spin

    # ------------------------------------------------------------------ 网络页
    def _build_network(self, page) -> None:
        self._heading(page, "网络 / 智能代理")
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="代理", bg=theme.BG, fg=theme.FG_DIM,
                 width=7, anchor="w", font=theme.FONT_SMALL).pack(side="left")
        self.btn_proxy_test = tk.Button(
            row, text="测速", bg=theme.CARD, fg=theme.FG_DIM, relief="flat",
            bd=0, font=theme.FONT_SMALL, padx=10, pady=2,
            activebackground=theme.CARD_HOVER, activeforeground=theme.FG,
            cursor="hand2", command=self._test_route)
        self.btn_proxy_test.pack(side="right", padx=(theme.px(8), 0))
        self.entry_proxy = tk.Entry(
            row, bg=theme.CARD, fg=theme.FG, relief="flat", bd=4,
            insertbackground=theme.FG, font=theme.FONT_SMALL,
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT)
        self.entry_proxy.insert(0, self.cfg.proxy)
        self.entry_proxy.pack(side="left", fill="x", expand=True)

        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="模式", bg=theme.BG, fg=theme.FG_DIM,
                 width=7, anchor="w", font=theme.FONT_SMALL).pack(side="left")
        for value, text in ((smartproxy.AUTO, "智能选择（测速后选更快的）"),
                            (smartproxy.PROXY, "强制走代理"),
                            (smartproxy.DIRECT, "不走代理")):
            tk.Radiobutton(
                row, text=text, variable=self.var_proxy_mode, value=value,
                bg=theme.BG, fg=theme.FG_DIM, selectcolor=theme.CARD,
                activebackground=theme.BG, activeforeground=theme.FG,
                font=theme.FONT_SMALL, bd=0, highlightthickness=0,
                cursor="hand2").pack(side="left", padx=(0, theme.px(8)))

        self.test_result = tk.Label(
            page, text="提示：智能选择会在每次嗅探前同时测试直连和代理，"
                       "只比较响应头延迟，通常不到 1 秒。",
            bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
            anchor="w", justify="left", wraplength=theme.px(440))
        self.test_result.pack(fill="x", pady=(theme.px(2), theme.px(6)))

        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="Cookie", bg=theme.BG, fg=theme.FG_DIM,
                 width=7, anchor="nw", font=theme.FONT_SMALL).pack(side="left")
        self.cookie_text = tk.Text(
            row, height=6, bg=theme.CARD, fg=theme.FG, relief="flat", bd=4,
            insertbackground=theme.FG, font=theme.FONT_SMALL, wrap="none",
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT)
        self.cookie_text.insert("1.0", self.cfg.cookie)
        self.cookie_text.pack(side="left", fill="both", expand=True)
        tk.Label(page, text="需要登录的站点可粘贴浏览器 Cookie（支持多行）。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 anchor="w").pack(fill="x")

    def _test_route(self) -> None:
        url = ""
        try:
            url = self.master.url_entry.value()      # type: ignore[attr-defined]
        except Exception:
            pass
        from .net import normalize_url

        url = normalize_url(url) or "https://www.bing.com/"
        proxy = self.entry_proxy.get().strip()
        self._test_payload = None
        self.btn_proxy_test.configure(state="disabled")
        self.test_result.configure(text="正在测速…（直连 vs 代理，最多几秒）",
                                   fg=theme.FG_DIM)

        def work() -> None:
            try:
                lat = smartproxy.probe_latencies(url, proxy, timeout=4.0)
                direct_ms, direct_err = lat["direct"]
                proxy_ms, proxy_err = lat["proxy"]
                bits: list[str] = []
                bits.append(f"直连 {direct_ms:.0f}ms" if direct_ms is not None
                            else "直连不可用")
                if proxy:
                    bits.append(f"代理 {proxy_ms:.0f}ms" if proxy_ms is not None
                                else "代理不可用")
                else:
                    bits.append("未填代理")
                if direct_ms is not None and proxy_ms is not None:
                    bits.append("建议：代理" if proxy_ms + 5 < direct_ms else "建议：直连")
                elif direct_ms is None and proxy_ms is not None:
                    bits.append("建议：代理")
                elif proxy_ms is None and direct_ms is not None:
                    bits.append("建议：直连")
                if direct_ms is None and direct_err:
                    bits.append(f"直连原因：{direct_err[:40]}")
                if proxy and proxy_ms is None and proxy_err:
                    bits.append(f"代理原因：{proxy_err[:40]}")
                self._test_payload = " · ".join(bits)
            except Exception as exc:
                self._test_payload = f"测速失败：{type(exc).__name__}: {exc}"

        threading.Thread(target=work, daemon=True, name="proxy-test").start()
        self.after(80, self._poll_test_result)

    def _poll_test_result(self) -> None:
        try:
            if not self.winfo_exists():
                return
            payload = getattr(self, "_test_payload", None)
            if payload is None:
                self.after(80, self._poll_test_result)
                return
            self.btn_proxy_test.configure(state="normal")
            self.test_result.configure(text=payload, fg=theme.FG)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 关于页
    def _build_about(self, page) -> None:
        box = tk.Frame(page, bg=theme.BG)
        box.pack(fill="x", pady=(theme.px(6), 0))
        title = tk.Label(box, text="StarSniff 星嗅", bg=theme.BG, fg=theme.FG,
                         font=(theme.FONT_FAMILY, 18, "bold"), anchor="w")
        title.pack(anchor="w")
        tk.Label(box, text=f"版本 v{APP_VERSION} · Windows 桌面版",
                 bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL, anchor="w").pack(anchor="w",
                                                         pady=(theme.px(2), 0))
        tk.Label(box,
                 text="剧集 / 网页媒体资源嗅探与下载。\n"
                      "纯 Python + Tkinter，可选 yt-dlp 与 Pillow。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 justify="left", anchor="w").pack(anchor="w",
                                                  pady=(theme.px(6), 0))

        self._heading(page, "更新")
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        self.update_status = tk.Label(
            row, text="启动时自动检查 GitHub Releases", bg=theme.BG,
            fg=theme.FG_DIM, font=theme.FONT_SMALL, anchor="w")
        self.update_status.pack(side="left", fill="x", expand=True)
        self.btn_check_update = tk.Button(
            row, text="检查更新", bg=theme.CARD, fg=theme.FG_DIM,
            relief="flat", bd=0, font=theme.FONT_SMALL, padx=12, pady=3,
            activebackground=theme.CARD_HOVER, activeforeground=theme.FG,
            cursor="hand2", command=self._check_updates)
        self.btn_check_update.pack(side="right", padx=(theme.px(8), 0))
        tk.Label(page,
                 text="公开仓库，无需登录 GitHub；只有发现更高版本时才会提示。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 anchor="w", justify="left",
                 wraplength=theme.px(420)).pack(fill="x",
                                                pady=(theme.px(2), 0))

        self._toggle(page, "启动时自动检查更新", self.var_check_updates,
                     hint="立即生效，不必等「应用」",
                     command=self._auto_update_changed)

        self._heading(page, "发布与反馈")
        repo = tk.Label(page, text="github.com/YCX-jpg/StarSniff",
                        bg=theme.BG, fg=theme.ACCENT, font=theme.FONT_SMALL,
                        cursor="hand2", anchor="w")
        repo.pack(fill="x", pady=(theme.px(2), 0))
        repo.bind("<Button-1>", lambda _e: webbrowser.open(
            "https://github.com/YCX-jpg/StarSniff"))
        tk.Label(page, text="新版本会以 Release 附件（exe / zip）发布。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 anchor="w").pack(fill="x")

        skipped = (self.cfg.skipped_version or "").strip()
        if skipped:
            reset = tk.Label(page, text=f"已跳过 v{skipped}，点此恢复提醒",
                             bg=theme.BG, fg=theme.WARN,
                             font=theme.FONT_TINY, cursor="hand2",
                             anchor="w")
            reset.pack(fill="x", pady=(theme.px(8), 0))
            reset.bind("<Button-1>", lambda _e: self._unskip(skipped, reset))

    def _unskip(self, version: str, label) -> None:
        self.cfg.skipped_version = ""
        self.on_save()
        try:
            label.configure(text="已恢复新版本提醒", fg=theme.SUCCESS)
        except tk.TclError:
            pass

    def _auto_update_changed(self) -> None:
        """自动检查开关立即写盘，不必等「应用」，避免用户以为没生效。"""
        enabled = bool(self.var_check_updates.get())
        self.cfg.check_updates = enabled
        self.on_save()
        try:
            self.update_status.configure(
                text=("已开启：启动时检查更新" if enabled
                      else "已关闭：启动时不再检查更新"),
                fg=theme.SUCCESS if enabled else theme.FG_DIM)
        except tk.TclError:
            pass

    def _check_updates(self) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self._update_payload = None
        self.btn_check_update.configure(state="disabled")
        self.update_status.configure(text="正在检查 GitHub Releases…",
                                     fg=theme.FG_DIM)

        def work() -> None:
            try:
                info = updater.check_latest(timeout=8)
                self._update_payload = (info, None)
            except Exception as exc:
                self._update_payload = (None, exc)

        threading.Thread(target=work, daemon=True,
                         name="settings-update").start()
        self.after(100, self._poll_update)

    def _poll_update(self) -> None:
        try:
            if not self.winfo_exists():
                return
            payload = self._update_payload
            if payload is None:
                self.after(100, self._poll_update)
                return
            self._update_checking = False
            self._update_payload = None
            self.btn_check_update.configure(state="normal")
            info, error = payload
            if error is not None:
                self.update_status.configure(
                    text="检查失败：" + updater.describe_error(error),
                    fg=theme.WARN)
                return
            if info.is_newer_than():
                self.update_status.configure(
                    text=f"发现新版本 v{info.version}（当前 v{APP_VERSION}）",
                    fg=theme.SUCCESS)
                if messagebox.askyesno(
                        "发现新版本",
                        f"StarSniff v{info.version} 已发布。\n\n"
                        "要打开 Release 下载页吗？", parent=self):
                    webbrowser.open(info.download_url)
            else:
                self.update_status.configure(
                    text=f"已是最新版本 v{APP_VERSION}", fg=theme.FG_DIM)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 下载页
    def _build_download(self, page) -> None:
        self._heading(page, "本地下载")
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="同时下载", bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(side="left")
        self.spin_concurrency = self._spin(row, 1, 8, self.cfg.concurrency)
        self.spin_concurrency.pack(side="left", padx=(theme.px(8), theme.px(6)))
        tk.Label(row, text="个文件", bg=theme.BG, fg=theme.FG_FAINT,
                 font=theme.FONT_SMALL).pack(side="left")

        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x", pady=theme.px(3))
        tk.Label(row, text="大文件加速", bg=theme.BG, fg=theme.FG_DIM,
                 font=theme.FONT_SMALL).pack(side="left")
        self.spin_segments = self._spin(row, 1, 8,
                                        getattr(self.cfg, "download_segments", 4))
        self.spin_segments.pack(side="left", padx=(theme.px(8), theme.px(6)))
        tk.Label(row, text="个连接（仅对支持 Range 的 8MB+ 文件生效）",
                 bg=theme.BG, fg=theme.FG_FAINT,
                 font=theme.FONT_SMALL).pack(side="left")

        self._heading(page, "保存目录")
        dir_row = tk.Frame(page, bg=theme.BG)
        dir_row.pack(fill="x", pady=theme.px(3))
        self.dir_label = tk.Label(dir_row, text=self._shorten(self._download_dir),
                                  bg=theme.BG, fg=theme.FG_DIM,
                                  font=theme.FONT_SMALL, anchor="w")
        self.dir_label.pack(side="left", fill="x", expand=True)
        tk.Button(dir_row, text="选择…", bg=theme.CARD, fg=theme.FG_DIM,
                  relief="flat", bd=0, font=theme.FONT_SMALL, padx=10, pady=2,
                  activebackground=theme.CARD_HOVER, activeforeground=theme.FG,
                  cursor="hand2", command=self._choose_dir
                  ).pack(side="right")
        tk.Label(page, text="下载完成后可以直接在列表里右键打开文件或所在文件夹。",
                 bg=theme.BG, fg=theme.FG_FAINT, font=theme.FONT_TINY,
                 anchor="w", justify="left", wraplength=theme.px(430)
                 ).pack(fill="x", pady=(theme.px(6), 0))

    def _choose_dir(self) -> None:
        folder = filedialog.askdirectory(parent=self,
                                         initialdir=self._download_dir)
        if folder:
            self._download_dir = folder
            self.dir_label.configure(text=self._shorten(folder))

    @staticmethod
    def _shorten(path: str, limit: int = 52) -> str:
        path = path or ""
        return path if len(path) <= limit else path[:14] + "…" + path[-limit + 16:]

    # ------------------------------------------------------------------ 通用
    def _toggle(self, parent, text: str, var: tk.BooleanVar,
                hint: str = "", command=None) -> tk.Frame:
        """一行「说明 + 滑动开关」；整行可点，不再用系统复选框。"""
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", pady=(theme.px(9), 0))
        info = tk.Frame(row, bg=theme.BG)
        info.pack(side="left", fill="x", expand=True)
        label = tk.Label(info, text=text, bg=theme.BG, fg=theme.FG,
                         font=theme.FONT_SMALL, anchor="w", justify="left")
        label.pack(fill="x")
        clickable = [row, info, label]
        if hint:
            tip = tk.Label(info, text=hint, bg=theme.BG, fg=theme.FG_FAINT,
                           font=theme.FONT_TINY, anchor="w", justify="left",
                           wraplength=theme.px(430))
            tip.pack(fill="x")
            clickable.append(tip)
        switch = ToggleSwitch(row, var, command)
        switch.pack(side="right", padx=(theme.px(12), 0), anchor="n")
        for widget in clickable:
            widget.bind("<Button-1>", lambda _e: switch.invoke(), add="+")
        return row

    def _check(self, parent, text: str, var: tk.BooleanVar,
               command=None) -> tk.Frame:
        """兼容旧调用：复选框统一换成滑动开关。"""
        return self._toggle(parent, text, var, command=command)

    def _apply(self) -> None:
        cfg = self.cfg
        cfg.theme_preset = self.var_preset.get()
        cfg.theme_dark = bool(getattr(self, "var_dark", None)
                              and self.var_dark.get())
        cfg.auto_update_ytdlp = bool(getattr(self, "var_auto_ytdlp", None)
                                     and self.var_auto_ytdlp.get())
        cfg.auto_update_app = bool(getattr(self, "var_auto_app", None)
                                   and self.var_auto_app.get())
        cfg.check_updates = cfg.auto_update_app
        cfg.accent = self.var_accent.get()
        cfg.radius_button = max(0, min(28, int(self.var_radius_button.get())))
        cfg.radius_window = max(0, min(30, int(self.var_radius_window.get())))
        cfg.animations = bool(self.var_animations.get())
        try:
            cfg.anim_fps = max(5, min(120, int(self.var_anim_fps.get())))
        except (TypeError, ValueError):
            cfg.anim_fps = 60
        cfg.dynamic = bool(self.var_dynamic.get())
        cfg.deep = bool(self.var_deep.get())
        cfg.scroll = bool(self.var_scroll.get())
        cfg.headless = bool(self.var_headless.get())
        cfg.use_ytdlp = bool(self.var_ytdlp.get())
        cfg.check_updates = bool(self.var_check_updates.get())
        try:
            cfg.dynamic_wait = max(1.0, float(self.spin_wait.get()))
        except ValueError:
            pass
        try:
            cfg.max_items = max(50, int(self.spin_max.get()))
        except ValueError:
            pass
        try:
            cfg.min_image_side = max(0, int(self.spin_min.get()))
        except ValueError:
            pass
        cfg.proxy = self.entry_proxy.get().strip()
        cfg.proxy_mode = smartproxy.normalize_mode(self.var_proxy_mode.get())
        cfg.cookie = self.cookie_text.get("1.0", "end-1c").strip()
        try:
            cfg.concurrency = max(1, min(8, int(float(self.spin_concurrency.get()))))
        except (AttributeError, ValueError):
            pass
        try:
            cfg.download_segments = max(
                1, min(8, int(float(self.spin_segments.get()))))
        except (AttributeError, ValueError):
            pass
        if self._download_dir:
            cfg.download_dir = self._download_dir
        try:
            if self._preview_job is not None:
                self.after_cancel(self._preview_job)
                self._preview_job = None
        except tk.TclError:
            pass
        self._cancel_ytdlp_job()
        self._closing = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        motion.close_window(self, self._finish_apply, dy=theme.px(6))

    def _finish_apply(self) -> None:
        cfg = self.cfg
        try:
            self.destroy()
        except tk.TclError:
            pass
        self.on_apply(cfg)

    def _activate_window(self) -> None:
        if self._closing:
            return
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            if self._preview_job is not None:
                self.after_cancel(self._preview_job)
                self._preview_job = None
        except tk.TclError:
            pass
        self._cancel_ytdlp_job()
        motion.close_window(self, self._finish_close, dy=theme.px(6))

    def _finish_close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _cancel_ytdlp_job(self) -> None:
        self._ytdlp_cancel.set()
        try:
            if self._ytdlp_job is not None:
                self.after_cancel(self._ytdlp_job)
                self._ytdlp_job = None
        except tk.TclError:
            pass

    def _center_on(self, master) -> None:
        try:
            width = self.winfo_width()
            height = self.winfo_height()
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 2
            x = max(0, min(x, self.winfo_screenwidth() - width))
            y = max(0, min(y, self.winfo_screenheight() - height))
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
