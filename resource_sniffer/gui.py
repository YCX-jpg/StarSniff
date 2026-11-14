"""主界面（Tkinter）。

布局：

    ┌────────────────────────────────────────────────────────────┐
    │ StarSniff 星嗅                    [设置] [使用说明] [— □ ✕] │  自绘标题栏
    ├────────────────────────────────────────────────────────────┤
    │ [https://… 输入链接            ] [ 开始 ]   ○深度 ○动态      │  地址栏
    ├────────────────────────────────────────────────────────────┤
    │ 全部 12  图片 8  音频 2  视频 1  流媒体 1      [搜索…]      │  筛选
    ├────────────────────────────────────────────────────────────┤
    │ ☑ [缩略图]  [图片] 文件名.jpg                              │
    │             1.2 MB · 1920×1080                             │
    │             https://…                                      │
    ├────────────────────────────────────────────────────────────┤
    │ 状态：已发现 12 个资源…                                     │  状态栏
    │ 保存到：<软件目录>\\Download  [更改]              [下载]     │  操作栏
    └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox
from typing import Optional

from . import config as cfgmod
from . import frameless, glass, smartproxy, theme, updater, useragents
from . import __version__ as APP_VERSION
from .config import Config
from .downloader import DownloadManager, DownloadTask
from .engine import SniffOptions, Sniffer
from .mediainfo import fmt_size
from . import mediainfo
from .models import ResourceItem
from .net import normalize_url
from . import ytdlp
from .prefs import SettingsDialog
from .widgets import (AccentButton, DropdownButton, PlaceholderEntry,
                      ResourceList, SegmentedTabs, SideTabs, ellipsize,
                      glass_surface, paint_wallpaper, round_rect, soft_beam)


class MainWindow(tk.Tk):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        # 无系统标题栏：使用应用内绘制的标题栏（见 frameless 模块）
        self._maximized = False
        self._normal_geometry = ""
        self._drag_offset = None
        self._closing = False
        self.overrideredirect(True)

        # 配置目录：默认放在程序目录；测试/绿色版可用 RS_CONFIG_DIR 指定别处，
        # 避免把运行状态写进用户目录。
        self.base_dir = (os.environ.get("RS_CONFIG_DIR", "").strip()
                         or cfgmod.app_dir())
        os.makedirs(self.base_dir, exist_ok=True)
        self.cfg: Config = cfgmod.load(self.base_dir)

        # 先应用视觉预设，再按 DPI 放大度量并构建界面
        theme.apply_style(self.cfg.theme_preset, self.cfg.accent,
                          self.cfg.radius_button, self.cfg.radius_window,
                          self.cfg.animations, self.cfg.anim_fps)
        self._scale = theme.apply_scale(self)
        self._apply_geometry_scale()

        self.title("StarSniff 星嗅")
        self.configure(bg=theme.BG)
        # 动画要跑到 30–120 FPS，必须先把系统定时器精度提到 1ms
        theme.begin_timer_resolution()
        self._set_window_icon()
        # 缩略图按当前 DPI 生成，避免高分屏发虚
        mediainfo.THUMBNAIL_SIDE = theme.THUMB_SIZE

        self.download_dir = (self.cfg.download_dir
                             or cfgmod.default_download_dir(self.base_dir))
        try:
            os.makedirs(self.download_dir, exist_ok=True)
        except OSError:
            # 软件装在只读目录（如 Program Files）时退回系统「下载」目录
            self.download_dir = cfgmod.user_download_dir()
            os.makedirs(self.download_dir, exist_ok=True)
        self._restore_geometry()

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.sniffer = Sniffer(on_item=self._sniff_item, on_log=self._sniff_log,
                               on_progress=self._sniff_progress,
                               on_done=self._sniff_done,
                               on_remove=self._sniff_remove)
        self.downloader = DownloadManager(
            on_start=self._dl_start, on_progress=self._dl_progress,
            on_finish=self._dl_finish,
            segments=getattr(self.cfg, "download_segments", 4),
            max_workers=self.cfg.concurrency)
        self._kind_filter = "all"
        self._query = ""
        self._start_time = 0.0
        self._pump_job = None
        self._ytdlp_thread = None
        self._ytdlp_cancel = threading.Event()
        self._update_thread = None
        self._update_info = None
        self._update_job = None
        self._update_dialog = None
        self._header_job = None
        self._header_phase = 0.0
        self._settings_dialog = None
        self._build_ui()
        self._resizer = frameless.WindowResizer(
            self, theme.px(860), theme.px(560),
            is_maximized=lambda: self._maximized)
        self._apply_window_radius()
        self.after(300, self._apply_window_glass)
        self.bind("<Alt-F4>", lambda _e: self._on_close())
        self.bind("<Control-a>", self._on_select_all_key)
        self.bind("<Control-A>", self._on_select_all_key)
        self.bind("<Control-f>", self._on_focus_search)
        self.bind("<Control-F>", self._on_focus_search)
        self._pump_job = self.after(80, self._pump_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # overrideredirect 窗口默认不在任务栏：改窗口样式后再显示
        self.after(50, lambda: frameless.apply_taskbar_style(self))
        self._update_job = self.after(1600, self._auto_check_updates)
        self._log("就绪。输入网址开始嗅探。")

    # ================================================================== UI
    def _apply_geometry_scale(self) -> None:
        """窗口尺寸按 DPI 缩放，保证 150% 下窗口内容仍完整可见。"""
        width, height = theme.px(1080), theme.px(720)
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
        except tk.TclError:
            screen_w, screen_h = width, height
        width = min(width, max(theme.px(860), int(screen_w * 0.92)))
        height = min(height, max(theme.px(560), int(screen_h * 0.86)))
        self.geometry(f"{width}x{height}")
        self.minsize(theme.px(860), theme.px(560))

    # ------------------------------------------------------- 窗口位置记忆
    @staticmethod
    def _parse_geometry(text: str):
        """解析 Tk 的 "宽x高±x±y" 几何字符串；非法返回 None。"""
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)",
                             (text or "").strip())
        if not match:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return width, height, int(match.group(3)), int(match.group(4))

    def _restore_geometry(self) -> None:
        parsed = self._parse_geometry(getattr(self.cfg, "window_geometry", ""))
        if parsed is None:
            return
        width, height, x, y = parsed
        min_w, min_h = theme.px(860), theme.px(560)
        if width < min_w or height < min_h:
            return
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
        except tk.TclError:
            return
        width = min(width, max(min_w, screen_w))
        height = min(height, max(min_h, screen_h))
        # 位置完全在屏幕外（换过显示器/分辨率）时只恢复尺寸，避免窗口"消失"
        if (x + width <= 80 or y + height <= 80
                or x >= screen_w - 80 or y >= screen_h - 80):
            self.geometry(f"{width}x{height}")
            return
        self.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _remember_geometry(self) -> None:
        if getattr(self, "_maximized", False):
            geometry = getattr(self, "_normal_geometry", "")
        else:
            try:
                geometry = self.geometry()
            except tk.TclError:
                return
        if self._parse_geometry(geometry):
            self.cfg.window_geometry = geometry

    def _set_window_icon(self) -> None:
        roots = [getattr(sys, "_MEIPASS", ""),
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        for root in roots:
            path = os.path.join(root, "assets", "starsniff.png") if root else ""
            ico = os.path.join(root, "assets", "starsniff.ico") if root else ""
            if path and os.path.isfile(path):
                try:
                    icon = tk.PhotoImage(file=path)
                    self.iconphoto(True, icon)
                    self._icon = icon
                    if os.name == "nt" and os.path.isfile(ico):
                        try:
                            self.iconbitmap(default=ico)
                        except tk.TclError:
                            pass
                    return
                except tk.TclError:
                    pass
        try:
            icon = tk.PhotoImage(width=32, height=32)
            icon.put(theme.ACCENT, to=(4, 4, 28, 28))
            icon.put("#ffffff", to=(12, 8, 20, 24))
            self.iconphoto(True, icon)
            self._icon = icon
        except tk.TclError:
            pass

    def _build_ui(self) -> None:
        """按主题的 layout 规格搭建界面。

        默认主题是传统自上而下布局；亚克力用左侧导航栏；液态玻璃用
        悬浮胶囊 + 居中标题 + 卡片列表；霓虹用紧凑顶栏 + 芯片筛选；
        复古主题额外画出菜单栏和状态栏。
        """
        self._layout = dict(theme.LAYOUT)
        # 重建界面时旧控件的引用必须清掉，否则 _update_counts 会操作已销毁的标签
        for attr in ("side_stats", "sidebar", "menubar", "traffic_lights",
                     "_header_glass", "_hero_glass", "_glass_body",
                     "_capsule_item"):
            setattr(self, attr, None)
        self._shell = tk.Frame(self, bg=theme.BG)
        self._shell.pack(fill="both", expand=True)
        if self._layout.get("header") == "menu":
            self._build_header(self._shell)
            self._build_menubar(self._shell)
            self._build_standard_body(self._shell)
            return
        self._build_header(self._shell)
        shell = self._layout.get("shell")
        if shell == "sidebar":
            self._build_sidebar_body(self._shell)
        elif shell == "floating":
            self._build_glass_body(self._shell)
        elif self._layout.get("header") == "minimal":
            self._build_neon_body(self._shell)
        else:
            self._build_standard_body(self._shell)
        if theme.GLASS and glass.available():
            # 换主题时窗口尺寸没变、Configure 可能不再触发：这里主动补一次，
            # 否则壁纸和磨砂卡片会一直是白板。
            for delay, job in ((300, self._flush_hero),
                               (340, self._flush_body),
                               (380, self._redraw_glass_list),
                               (420, self._flush_footer)):
                try:
                    self.after(delay, job)
                except tk.TclError:
                    pass

    def _redraw_glass_list(self) -> None:
        view = getattr(self, "list_view", None)
        if view is not None:
            try:
                view.redraw()
            except tk.TclError:
                pass

    def _build_standard_body(self, parent) -> None:
        body = tk.Frame(parent, bg=theme.BG)
        body.pack(fill="both", expand=True)
        self._build_searchbar(body)
        self._build_filterbar(body)
        self._build_list(body)
        self._build_footer(body)

    # ------------------------------------------------------------ 标题栏
    def _build_header(self, parent=None) -> None:
        variant = self._layout.get("header", "bar")
        if variant == "floating":
            self._build_glass_header(parent)
            return
        if variant == "minimal":
            self._build_neon_header(parent)
            return
        if variant == "menu":
            self._build_retro_header(parent)
            return
        parent = parent or self
        height = theme.px(int(self._layout.get("header_height", 54)))
        bar = tk.Frame(parent, bg=theme.TITLE_BG, height=height)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._titlebar = bar
        size = theme.px(34)
        logo = tk.Canvas(bar, width=size, height=size, bg=theme.TITLE_BG,
                         highlightthickness=0)
        logo.pack(side="left", padx=(theme.px(18), theme.px(10)),
                  pady=theme.px(10))
        from .widgets import round_rect

        pad = theme.px(2)
        round_rect(logo, pad, pad, size - pad, size - pad,
                   theme.small_radius() if theme.CLASSIC else theme.px(9),
                   fill=theme.ACCENT, outline="")
        k = size / 34.0
        logo.create_oval(9 * k, 12 * k, 20 * k, 23 * k, outline="#ffffff",
                         width=max(2, theme.px(2)))
        logo.create_line(20 * k, 12 * k, 26 * k, 7 * k, fill="#ffffff",
                         width=max(2, theme.px(2)))
        title = tk.Label(bar, text="StarSniff 星嗅", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG, font=theme.FONT_H1)
        title.pack(side="left", pady=theme.px(14))
        self.subtitle = tk.Label(
            bar,
            text=f"剧集 · 图片 · 音频 · 视频 · 流媒体　·　v{APP_VERSION}",
            bg=theme.TITLE_BG, fg=theme.TITLE_SUB, font=theme.FONT_SMALL)
        self.subtitle.pack(side="left", padx=(theme.px(12), 0),
                           pady=theme.px(18))

        self._make_window_buttons(bar, theme.TITLE_BG, theme.TITLE_SUB)
        self._make_header_links(bar, theme.TITLE_BG, theme.TITLE_SUB)
        self._bind_title_region(bar, logo, title, self.subtitle)
        tk.Frame(parent, bg=theme.BORDER, height=1).pack(fill="x")

    # ---- 通用标题栏零件
    def _make_window_buttons(self, bar, bg: str, fg: str,
                             compact: bool = False) -> None:
        button_h = theme.px(28 if compact else 34)
        button_w = theme.px(38 if compact else 44)
        self.btn_close = frameless.WindowButton(
            bar, "close", self._on_close, width=button_w, height=button_h,
            bg=bg, fg=fg)
        self.btn_close.pack(side="right",
                            padx=(0, theme.px(6 if compact else 8)),
                            pady=theme.px(4 if compact else 10))
        self.btn_max = frameless.WindowButton(
            bar, "max", self._toggle_maximize, width=button_w, height=button_h,
            bg=bg, fg=fg)
        self.btn_max.pack(side="right", pady=theme.px(4 if compact else 10))
        self.btn_min = frameless.WindowButton(
            bar, "min", self._minimize, width=button_w, height=button_h,
            bg=bg, fg=fg)
        self.btn_min.pack(side="right", pady=theme.px(4 if compact else 10))
        if not compact:
            tk.Frame(bar, bg=theme.BORDER, width=1,
                     height=theme.px(18)).pack(
                side="right", padx=(theme.px(8), theme.px(12)))

    def _make_header_links(self, bar, bg: str, fg: str,
                           compact: bool = False) -> None:
        self.btn_settings = tk.Label(
            bar, text="⚙ 设置" if not compact else "设置", bg=bg, fg=fg,
            font=theme.FONT_SMALL, cursor="hand2",
            padx=theme.px(6 if compact else 8))
        self.btn_settings.pack(side="right")
        self.btn_settings.bind("<Button-1>", lambda _e: self.open_settings())
        self.btn_settings.bind(
            "<Enter>", lambda _e: self.btn_settings.configure(fg=theme.TITLE_FG))
        self.btn_settings.bind(
            "<Leave>", lambda _e: self.btn_settings.configure(fg=fg))
        self.btn_help = tk.Label(
            bar, text="? 使用说明" if not compact else "帮助", bg=bg, fg=fg,
            font=theme.FONT_SMALL, cursor="hand2",
            padx=theme.px(6 if compact else 8))
        self.btn_help.pack(side="right",
                           padx=(0, theme.px(6 if compact else 10)))
        self.btn_help.bind("<Button-1>", lambda _e: self.show_help())
        self.btn_help.bind(
            "<Enter>", lambda _e: self.btn_help.configure(fg=theme.TITLE_FG))
        self.btn_help.bind(
            "<Leave>", lambda _e: self.btn_help.configure(fg=fg))
        self.btn_update = tk.Label(
            bar, text="", bg=bg, fg=theme.ACCENT,
            font=(theme.FONT_FAMILY, theme.FONT_SMALL[1], "bold"),
            cursor="hand2", padx=theme.px(6))
        self.btn_update.bind("<Button-1>",
                             lambda _e: self._on_update_badge_click())

    def _bind_title_region(self, *widgets) -> None:
        for widget in widgets:
            widget.bind("<Button-1>", self._title_press, add="+")
            widget.bind("<B1-Motion>", self._title_drag, add="+")
            widget.bind("<ButtonRelease-1>",
                        lambda _e: frameless.end_drag(self), add="+")
            widget.bind("<Double-Button-1>",
                        lambda _e: self._toggle_maximize(), add="+")

    def _brand_icon(self, parent, bg: str, size: int = 34) -> tk.Canvas:
        from .widgets import round_rect

        logo = tk.Canvas(parent, width=size, height=size, bg=bg,
                         highlightthickness=0)
        pad = theme.px(2)
        round_rect(logo, pad, pad, size - pad, size - pad,
                   size // 2 if theme.LAYOUT.get("shell") == "floating"
                   else theme.px(9), fill=theme.ACCENT, outline="")
        k = size / 34.0
        logo.create_oval(9 * k, 12 * k, 20 * k, 23 * k, outline="#ffffff",
                         width=max(2, theme.px(2)))
        logo.create_line(20 * k, 12 * k, 26 * k, 7 * k, fill="#ffffff",
                         width=max(2, theme.px(2)))
        return logo

    # ---- 液态玻璃：居中标题 + 红黄绿灯 ----
    def _build_glass_header(self, parent) -> None:
        parent = parent or self
        height = theme.px(int(self._layout.get("header_height", 58)))
        bar = tk.Frame(parent, bg=theme.TITLE_BG, height=height)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._titlebar = bar
        # 标题栏的玻璃底层：顶部天空反光 + 缓慢扫过的环境高光
        self._header_glass = tk.Canvas(bar, bg=theme.TITLE_BG,
                                       highlightthickness=0, bd=0)
        self._header_glass.place(x=0, y=0, relwidth=1, relheight=1)
        self._header_glass.bind("<Configure>",
                                lambda _e: self._draw_header_glass())
        self._draw_header_glass()
        if theme.ANIMATIONS:
            self._header_job = self.after(theme.frame_delay(),
                                          self._header_light_tick)
        lights = frameless.TrafficLights(
            bar, self._on_close, self._minimize, self._toggle_maximize,
            bg=theme.TITLE_BG)
        lights.pack(side="left", padx=(theme.px(18), 0),
                    pady=theme.px(21))
        self.traffic_lights = lights
        center = tk.Frame(bar, bg=theme.TITLE_BG)
        center.place(relx=0.5, rely=0.5, anchor="center")
        icon = self._brand_icon(center, theme.TITLE_BG, theme.px(22))
        icon.pack(side="left", padx=(0, theme.px(8)))
        title = tk.Label(center, text="StarSniff", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG,
                         font=(theme.FONT_FAMILY, 12, "bold"))
        title.pack(side="left")
        self.subtitle = tk.Label(center, text="星嗅", bg=theme.TITLE_BG,
                                 fg=theme.TITLE_SUB, font=theme.FONT_TINY)
        self.subtitle.pack(side="left", padx=(theme.px(6), 0),
                           pady=(theme.px(3), 0))
        self._make_header_links(bar, theme.TITLE_BG, theme.TITLE_SUB,
                                compact=True)
        spacer = tk.Frame(bar, bg=theme.TITLE_BG, width=theme.px(18))
        spacer.pack(side="right")
        self._bind_title_region(bar, center, icon, title, self.subtitle)
        tk.Frame(parent, bg=theme.BORDER, height=1).pack(fill="x")

    def _draw_header_glass(self) -> None:
        canvas = getattr(self, "_header_glass", None)
        if canvas is None:
            return
        try:
            if not canvas.winfo_exists():
                return
            width = max(canvas.winfo_width(), 200)
            height = max(canvas.winfo_height(), theme.px(40))
            canvas.delete("all")
            base = theme.TITLE_BG
            # 浅色玻璃：顶部一层天空光，向下回到很淡的底色
            bands = 12
            sky = theme.blend(theme.ACCENT, "#ffffff", 0.24)
            for index in range(bands):
                t = index / (bands - 1)
                color = theme.blend(sky, base, 0.10 * (1.0 - t) ** 1.6)
                y0 = height * t
                canvas.create_rectangle(0, y0, width, y0 + height / bands + 1,
                                        fill=color, outline=color)
            canvas.create_line(theme.px(24), 1, width - theme.px(24), 1,
                               fill=theme.blend("#ffffff", base, 0.92),
                               width=1)
            canvas.create_line(0, height - 1, width, height - 1,
                               fill=theme.blend("#7f93ad", base, 0.16),
                               width=1)
            self._draw_header_sheen()
        except tk.TclError:
            pass

    def _draw_header_sheen(self) -> None:
        """标题栏上缓缓飘过的彩色环境光（每帧只重画这一层）。"""
        canvas = getattr(self, "_header_glass", None)
        if canvas is None:
            return
        try:
            width = max(canvas.winfo_width(), 200)
            height = max(canvas.winfo_height(), theme.px(40))
            canvas.delete("sheen")
            base = theme.TITLE_BG
            # 两团完全不同步的柔光：颜色在强调色 / 雾蓝 / 淡紫 / 浅粉间流动
            for offset in (0.0, 0.45):
                phase = (self._header_phase + offset) % 1.0
                center = -width * 0.15 + phase * width * 1.3
                soft_beam(canvas, 0, 0, width, height, center, width * 0.16,
                          base, theme.aurora_color(phase + offset),
                          0.10, ("sheen",), strips=14)
            # 一道更窄的白色反光，让玻璃有“湿度”
            sheen_x = -width * 0.3 + (self._header_phase % 1.0) * width * 1.6
            soft_beam(canvas, 0, 0, width, height, sheen_x, width * 0.05,
                      base, "#ffffff", 0.35, ("sheen",), strips=6)
        except tk.TclError:
            pass

    def _header_light_tick(self) -> None:
        self._header_job = None
        try:
            if self._closing or not self.winfo_exists():
                return
            delay = theme.frame_delay()
            self._header_phase = (self._header_phase
                                  + 0.25 * delay / 1000.0) % 1.0
            # 拖窗口 / 缩放时先不画彩光，把 CPU 让给布局
            if not glass.busy():
                self._draw_header_sheen()
            self._header_job = self.after(delay, self._header_light_tick)
        except tk.TclError:
            self._header_job = None

    # ---- 霓虹：极简细顶栏 + 主色细线 ----
    def _build_neon_header(self, parent) -> None:
        parent = parent or self
        height = theme.px(int(self._layout.get("header_height", 42)))
        bar = tk.Frame(parent, bg=theme.TITLE_BG, height=height)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._titlebar = bar
        logo = self._brand_icon(bar, theme.TITLE_BG, theme.px(24))
        logo.pack(side="left", padx=(theme.px(16), theme.px(8)),
                  pady=theme.px(9))
        title = tk.Label(bar, text="StarSniff", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG,
                         font=(theme.FONT_FAMILY, 11, "bold"))
        title.pack(side="left")
        tk.Label(bar, text="星嗅", bg=theme.TITLE_BG, fg=theme.ACCENT,
                 font=theme.FONT_TINY).pack(side="left",
                                            padx=(theme.px(6), 0),
                                            pady=(theme.px(2), 0))
        self.subtitle = tk.Label(bar, text=f"v{APP_VERSION}",
                                 bg=theme.TITLE_BG, fg=theme.TITLE_SUB,
                                 font=theme.FONT_TINY)
        self.subtitle.pack(side="left", padx=(theme.px(10), 0))
        self._make_window_buttons(bar, theme.TITLE_BG, theme.TITLE_SUB,
                                  compact=True)
        self._make_header_links(bar, theme.TITLE_BG, theme.TITLE_SUB,
                                compact=True)
        self._bind_title_region(bar, logo, title)
        tk.Frame(parent, bg=theme.ACCENT, height=1).pack(fill="x")

    # ---- 复古：Win2000 蓝色标题栏 + 菜单栏 ----
    def _build_retro_header(self, parent) -> None:
        parent = parent or self
        height = theme.px(int(self._layout.get("header_height", 30)))
        bar = tk.Frame(parent, bg=theme.TITLE_BG, height=height)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._titlebar = bar
        title = tk.Label(bar, text="StarSniff 星嗅", bg=theme.TITLE_BG,
                         fg=theme.TITLE_FG,
                         font=(theme.FONT_FAMILY, 9, "bold"))
        title.pack(side="left", padx=theme.px(6), pady=theme.px(2))
        self.subtitle = None
        self._make_window_buttons(bar, theme.TITLE_BG, "#ffffff",
                                  compact=True)
        self._bind_title_region(bar, title)
        tk.Frame(parent, bg=theme.BORDER_LIGHT, height=1).pack(fill="x")

    def _build_menubar(self, parent) -> None:
        bar = tk.Frame(parent, bg=theme.PANEL, height=theme.px(24))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.menubar = bar
        menus = (
            ("文件", (("设置…", self.open_settings), ("退出", self._on_close))),
            ("编辑", (("全选", self.select_all),
                      ("全不选", self.select_none),
                      ("搜索", self._on_focus_search))),
            ("查看", (("外观设置…", self.open_settings),
                      ("刷新列表", lambda: self.list_view.redraw()))),
            ("帮助", (("使用说明", self.show_help),
                      ("检查更新", self.check_updates_manual))),
        )
        for label, items in menus:
            btn = tk.Menubutton(bar, text=label, bg=theme.PANEL,
                                fg=theme.FG, activebackground=theme.ACCENT,
                                activeforeground="#ffffff", relief="flat",
                                bd=0, font=theme.FONT_SMALL, padx=theme.px(8),
                                pady=0, cursor="hand2")
            menu = tk.Menu(btn, tearoff=0, bg=theme.PANEL, fg=theme.FG,
                           activebackground=theme.ACCENT,
                           activeforeground="#ffffff", bd=1,
                           font=theme.FONT_SMALL)
            for text, command in items:
                menu.add_command(label=text, command=command)
            btn.configure(menu=menu)
            btn.pack(side="left", padx=(theme.px(2), 0))
        tk.Frame(bar, bg=theme.BORDER_LIGHT, height=1).pack(
            side="bottom", fill="x")

    # ---------------------------------------------------- 无边框窗口操作
    def _title_press(self, event) -> None:
        frameless.begin_drag(self, event)

    def _title_drag(self, event) -> None:
        glass.set_busy(0.25)
        frameless.do_drag(self, event)

    def _minimize(self) -> None:
        frameless.minimize(self)

    def _toggle_maximize(self) -> None:
        maximized = frameless.toggle_maximize(self)
        self._on_max_state(maximized)

    def _on_max_state(self, maximized: bool) -> None:
        try:
            self.btn_max.set_kind("restore" if maximized else "max")
            self._resizer.set_enabled(not maximized)
        except (AttributeError, tk.TclError):
            pass
        self._apply_window_radius()

    # ------------------------------------------------------------ 地址栏
    def _build_searchbar(self, parent) -> None:
        if self._layout.get("search") == "stacked":
            self._build_glass_search(parent)
        else:
            self._build_inline_search(parent)

    def _make_url_field(self, parent, height: int, bg: str = "") -> tk.Frame:
        bg = bg or theme.CARD
        field = tk.Frame(parent, bg=bg, highlightthickness=1,
                         highlightbackground=theme.BORDER_LIGHT
                         if theme.GLASS else theme.BORDER,
                         highlightcolor=theme.ACCENT)
        icon_size = theme.px(18)
        self._url_border = field
        self._search_icon = tk.Canvas(field, width=icon_size, height=icon_size,
                                      bg=bg, highlightthickness=0)
        self._search_icon.pack(side="left", padx=(theme.px(12), 0))
        self._draw_search_icon(theme.FG_FAINT)
        self.url_entry = PlaceholderEntry(
            field, "输入网页地址或资源直链，例如 https://example.com/page.html",
            bg=bg, fg=theme.FG, insertbackground=theme.FG,
            relief="flat", bd=0, font=(theme.FONT_FAMILY, 10),
            highlightthickness=0)
        ipady = max(4, theme.px(max(0, (height - 22) // 2)))
        self.url_entry.pack(side="left", fill="x", expand=True,
                            ipady=ipady, padx=(theme.px(6), theme.px(10)))
        self.url_entry.bind("<Return>", lambda _e: self.start_sniff())
        self.url_entry.bind("<FocusIn>", lambda _e: self._set_url_focus(True))
        self.url_entry.bind("<FocusOut>", lambda _e: self._set_url_focus(False))
        return field

    def _make_start_button(self, parent, inside: bool = False) -> AccentButton:
        height = theme.px(int(self._layout.get("start_height", 40)))
        width = theme.px(int(self._layout.get("start_width", 100)))
        style = self._layout.get("start_style", "rounded")
        radius = height // 2 if style in ("pill", "classic") else theme.px(10)
        if style == "classic":
            radius = 0
        btn = AccentButton(
            parent, "开始", command=self.start_sniff, width=width,
            height=height, radius=radius,
            bg=theme.CARD if inside else theme.BG,
            font=theme.FONT_BUTTON)
        self.btn_start = btn
        return btn

    def _make_ua(self, parent, height: int) -> DropdownButton:
        self.ua_dropdown = DropdownButton(
            parent,
            options=[(p.key, p.label) for p in useragents.PROFILES],
            command=self._on_ua_change,
            value=self.cfg.user_agent or "auto",
            width=theme.px(150 if theme.LAYOUT.get("header") == "minimal"
                           else 158),
            height=height, prefix="UA: ")
        return self.ua_dropdown

    def _make_toggles(self, parent) -> tk.Frame:
        opts = tk.Frame(parent, bg=theme.BG)
        self.var_dynamic = tk.BooleanVar(value=self.cfg.dynamic)
        self.var_deep = tk.BooleanVar(value=self.cfg.deep)
        for text, var in (("深度", self.var_deep), ("动态", self.var_dynamic)):
            cb = tk.Checkbutton(opts, text=text, variable=var, bg=theme.BG,
                                fg=theme.FG_DIM, selectcolor=theme.CARD,
                                activebackground=theme.BG,
                                activeforeground=theme.FG,
                                font=theme.FONT_SMALL,
                                bd=0, highlightthickness=0, cursor="hand2")
            cb.pack(side="left", padx=2)
        return opts

    def _build_inline_search(self, parent) -> None:
        pad = theme.px(int(self._layout.get("search_pad", 18)))
        height = int(self._layout.get("search_height", 40))
        top = 8 if self._layout.get("header") == "menu" else 12
        wrap = tk.Frame(parent, bg=theme.BG)
        wrap.pack(fill="x", padx=pad, pady=(theme.px(top), theme.px(6)))
        inner = tk.Frame(wrap, bg=theme.BG)
        inner.pack(fill="x")

        field = self._make_url_field(inner, height)
        field.pack(side="left", fill="x", expand=True)

        btn = self._make_start_button(inner)
        btn.pack(side="left", padx=(theme.px(10), 0))

        self._make_ua(inner, theme.px(height)).pack(
            side="left", padx=(theme.px(8), 0))
        self._make_toggles(inner).pack(side="left", padx=(theme.px(10), 0))

    def _build_glass_search(self, parent) -> None:
        """Apple 风格：大号胶囊输入框浮在壁纸上，主按钮嵌在胶囊内部。"""
        pad = theme.px(int(self._layout.get("search_pad", 26)))
        height = theme.px(int(self._layout.get("search_height", 48)))
        gap = theme.px(38)
        hero = tk.Canvas(parent, bg=theme.BG, height=height + gap * 2,
                         highlightthickness=0, bd=0)
        hero.pack(fill="x")
        self._hero_glass = hero
        self._hero_pad = (pad, gap)
        hero.bind("<Configure>", lambda _e: self._defer_hero())

        capsule = tk.Frame(hero, bg=theme.CARD, highlightthickness=1,
                           highlightbackground=theme.BORDER_LIGHT,
                           highlightcolor=theme.ACCENT)
        self._capsule = capsule
        field = self._make_url_field(capsule, height)
        field.pack(side="left", fill="x", expand=True, padx=(0, 0))
        field.configure(highlightthickness=0)
        self._url_border = capsule
        btn = self._make_start_button(capsule, inside=True)
        btn.pack(side="right", padx=theme.px(5), pady=theme.px(5))
        # 胶囊顶部一道亮边 + 底部一条折射色带（浅色玻璃的“厚度”）
        shine = tk.Frame(capsule, bg=theme.blend("#ffffff", theme.CARD, 0.95),
                         height=1)
        shine.place(x=theme.px(26), y=0, relwidth=0.5)
        refract = tk.Canvas(capsule, bg=theme.CARD, height=theme.px(3),
                            highlightthickness=0, bd=0)
        refract.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        refract.create_line(0, 0, 4000, 0,
                            fill=theme.blend(theme.ACCENT, theme.CARD, 0.30),
                            width=max(1, theme.px(1)))
        refract.create_line(0, 1, 4000, 1,
                            fill=theme.blend("#ffffff", theme.CARD, 0.60),
                            width=1)
        # 胶囊左右边缘：左上受光亮边、右下背光暗边
        left_edge = tk.Frame(capsule, width=theme.px(1),
                             bg=theme.blend("#ffffff", theme.CARD, 0.85))
        left_edge.place(x=0, y=0, relheight=1.0)
        right_edge = tk.Frame(capsule, width=theme.px(1),
                              bg=theme.blend("#000000", theme.CARD, 0.10))
        right_edge.place(relx=1.0, x=-theme.px(1), y=0, relheight=1.0)

        self._capsule_item = hero.create_window(
            pad, gap, anchor="nw", window=capsule,
            width=max(200, theme.px(420)), height=height)

        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", padx=pad, pady=(theme.px(4), theme.px(8)))
        self._make_ua(row, theme.px(32)).pack(side="left")
        self._make_toggles(row).pack(side="left", padx=(theme.px(10), 0))
        tk.Label(row, text="粘贴链接，剩下的交给星嗅", bg=theme.BG,
                 fg=theme.FG_FAINT, font=theme.FONT_TINY).pack(side="right")

    def _layout_glass_hero(self) -> None:
        """把胶囊拉伸到可用宽度，并重画它背后的壁纸。"""
        hero = getattr(self, "_hero_glass", None)
        item = getattr(self, "_capsule_item", None)
        if hero is None or item is None:
            return
        try:
            width = hero.winfo_width()
            if width <= 1:
                return
            pad, gap = self._hero_pad
            height = theme.px(int(self._layout.get("search_height", 48)))
            hero.coords(item, pad, gap)
            hero.itemconfigure(item, width=max(200, width - pad * 2),
                               height=height)
            self._paint_wallpaper_canvas(hero, fade=True)
        except tk.TclError:
            pass

    def _draw_search_icon(self, color: str) -> None:
        canvas = getattr(self, "_search_icon", None)
        if canvas is None:
            return
        try:
            canvas.delete("all")
            size = theme.px(18)
            p = theme.px
            canvas.create_oval(p(3), p(3), size - p(6), size - p(6),
                               outline=color, width=max(1, int(round(p(1.6)))))
            canvas.create_line(size - p(7), size - p(7), size - p(3), size - p(3),
                               fill=color, width=max(1, int(round(p(1.6)))),
                               capstyle="round")
        except tk.TclError:
            pass

    def _set_url_focus(self, focused: bool) -> None:
        try:
            border = getattr(self, "_url_border", self.url_entry.master)
            border.configure(highlightbackground=(
                theme.ACCENT if focused else
                theme.BORDER_LIGHT if theme.GLASS else theme.BORDER))
        except tk.TclError:
            pass
        self._draw_search_icon(theme.ACCENT if focused else theme.FG_FAINT)

    # ------------------------------------------------------------ 筛选栏
    def _build_filterbar(self, parent, with_tabs: bool = True) -> None:
        pad = theme.px(int(self._layout.get("search_pad", 18)))
        bar = tk.Frame(parent, bg=theme.BG)
        bar.pack(fill="x", padx=pad, pady=(0, theme.px(6)))
        if with_tabs:
            self.tabs = SegmentedTabs(bar, [
                ("all", "全部"), ("image", "图片"), ("audio", "音频"),
                ("video", "视频"), ("stream", "流媒体"),
            ], command=self._on_filter_change)
            if self._layout.get("filter_align") == "center":
                self.tabs.pack(anchor="center", pady=(0, theme.px(2)))
            else:
                self.tabs.pack(side="left")

        if self._layout.get("filter_align") == "center":
            controls_row = tk.Frame(parent, bg=theme.BG)
            controls_row.pack(fill="x", padx=pad, pady=(0, theme.px(6)))
            right = tk.Frame(controls_row, bg=theme.BG)
            right.pack(side="right")
        else:
            right = tk.Frame(bar, bg=theme.BG)
            right.pack(side="right")
        self._make_filter_controls(right)

    def _make_filter_controls(self, right) -> None:
        link = tk.Label(right, text="全选", bg=theme.BG, fg=theme.FG_DIM,
                        font=theme.FONT_SMALL, cursor="hand2", padx=theme.px(6))
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: self.select_all())
        link.bind("<Enter>", lambda _e: link.configure(fg=theme.ACCENT))
        link.bind("<Leave>", lambda _e: link.configure(fg=theme.FG_DIM))
        link2 = tk.Label(right, text="全不选", bg=theme.BG, fg=theme.FG_DIM,
                         font=theme.FONT_SMALL, cursor="hand2", padx=theme.px(6))
        link2.pack(side="right")
        link2.bind("<Button-1>", lambda _e: self.select_none())
        link2.bind("<Enter>", lambda _e: link2.configure(fg=theme.ACCENT))
        link2.bind("<Leave>", lambda _e: link2.configure(fg=theme.FG_DIM))

        search = tk.Frame(right, bg=theme.CARD, highlightthickness=1,
                          highlightbackground=theme.BORDER)
        search.pack(side="right", padx=(0, theme.px(10)))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        entry = tk.Entry(search, textvariable=self.search_var, bg=theme.CARD,
                         fg=theme.FG, relief="flat", bd=0, width=22,
                         insertbackground=theme.FG, font=theme.FONT_SMALL,
                         highlightthickness=0)
        entry.pack(side="left", padx=(theme.px(8), theme.px(6)),
                   ipady=theme.px(4))
        self.search_entry = entry
        tk.Label(search, text="搜索文件名/链接", bg=theme.CARD, fg=theme.FG_FAINT,
                 font=theme.FONT_TINY).pack(side="left", padx=(0, theme.px(8)))

        self.sort_dropdown = DropdownButton(
            right,
            options=[("default", "默认"), ("size", "大小↓"),
                     ("kind", "类型"), ("name", "名称")],
            command=self._on_sort_change, value="default",
            width=theme.px(126), height=theme.px(30), prefix="排序：")
        self.sort_dropdown.pack(side="right", padx=(0, theme.px(10)))

    # ------------------------------------------------------ 不同主题的壳层
    def _build_sidebar_body(self, parent) -> None:
        """亚克力：左侧竖向导航，右侧是工作区。"""
        body = tk.Frame(parent, bg=theme.BG)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=theme.PANEL, width=theme.px(208))
        side.pack(side="left", fill="y", padx=(theme.px(14), 0),
                  pady=theme.px(10))
        side.pack_propagate(False)
        self.sidebar = side
        tk.Label(side, text="资源类型", bg=theme.PANEL, fg=theme.FG_FAINT,
                 font=theme.FONT_TINY, anchor="w").pack(
            fill="x", padx=theme.px(14), pady=(theme.px(12), theme.px(2)))
        self.tabs = SideTabs(side, [
            ("all", "全部"), ("image", "图片"), ("audio", "音频"),
            ("video", "视频"), ("stream", "流媒体"),
        ], command=self._on_filter_change)
        self.tabs.pack(fill="x", padx=theme.px(8), pady=theme.px(4))
        tk.Frame(side, bg=theme.BORDER, height=1).pack(
            fill="x", padx=theme.px(12), pady=theme.px(10))
        quick = tk.Frame(side, bg=theme.PANEL)
        quick.pack(fill="x", padx=theme.px(12))
        for text, command in (("全选", self.select_all),
                              ("全不选", self.select_none)):
            lbl = tk.Label(quick, text=text, bg=theme.PANEL, fg=theme.FG_DIM,
                           font=theme.FONT_SMALL, cursor="hand2")
            lbl.pack(side="left", padx=(0, theme.px(12)))
            lbl.bind("<Button-1>", lambda _e, c=command: c())
            lbl.bind("<Enter>", lambda _e, w=lbl: w.configure(fg=theme.ACCENT))
            lbl.bind("<Leave>", lambda _e, w=lbl: w.configure(fg=theme.FG_DIM))
        self.side_stats = tk.Label(side, text="共 0 项", bg=theme.PANEL,
                                   fg=theme.FG_FAINT, font=theme.FONT_TINY,
                                   anchor="w")
        self.side_stats.pack(fill="x", padx=theme.px(14), pady=(theme.px(8), 0))
        tk.Label(side, text="亚克力 · 侧栏布局", bg=theme.PANEL,
                 fg=theme.FG_FAINT, font=theme.FONT_TINY, anchor="w").pack(
            side="bottom", fill="x", padx=theme.px(14), pady=theme.px(12))

        main = tk.Frame(body, bg=theme.BG)
        main.pack(side="left", fill="both", expand=True,
                  padx=(theme.px(12), theme.px(14)), pady=theme.px(10))
        self._build_searchbar(main)
        self._build_filterbar(main, with_tabs=False)
        self._build_list(main)
        self._build_footer(main)

    def _build_glass_body(self, parent) -> None:
        """液态玻璃：内容像一整块玻璃浮在壁纸上，四周留一圈壁纸。"""
        host = tk.Canvas(parent, bg=theme.BG, highlightthickness=0, bd=0)
        host.pack(fill="both", expand=True)
        self._glass_body = host
        host.bind("<Configure>", lambda _e: self._defer_body())
        pad = theme.px(26)
        inner = tk.Frame(host, bg=theme.BG)
        inner.place(x=pad, y=0, relwidth=1.0, relheight=1.0,
                    width=-pad * 2, height=-pad)
        self._build_searchbar(inner)
        self._build_filterbar(inner)
        self._build_list(inner)
        self._build_footer(inner)

    # ---- 壁纸绘制
    def _paint_wallpaper_canvas(self, canvas, fade: bool = False,
                                tag: str = "wall") -> None:
        """把当前主题的壁纸画到某个 Canvas 上（按窗口坐标对齐）。"""
        try:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
            if width <= 1 or height <= 1:
                # 刚建好还没布局：等一拍再画，否则壁纸会一直是白的
                tries = getattr(self, "_wall_retry", 0)
                if tries < 20:
                    self._wall_retry = tries + 1
                    self.after(60, lambda: self._paint_wallpaper_canvas(
                        canvas, fade, tag))
                return
            self._wall_retry = 0
            top = canvas.winfo_toplevel()
            offset = (canvas.winfo_rootx() - top.winfo_rootx(),
                      canvas.winfo_rooty() - top.winfo_rooty())
            win = (max(top.winfo_width(), width),
                   max(top.winfo_height(), height))
            if (glass.available() and theme.GLASS and not theme.NEON
                    and not glass.busy()):
                # 有 Pillow 时直接贴真实壁纸位图（和列表里的磨砂玻璃同源）
                image = glass.window_slice(
                    width, height, offset, win, theme.WALLPAPER,
                    fade_bottom=0.42 if fade else 0.0, fade_color=theme.BG)
                photo = glass.photo(
                    image, ("hero", width, height, offset[0] // 48,
                            offset[1] // 48, fade))
                if photo is not None:
                    self._wall_ref = photo
                    canvas.delete(tag)
                    canvas.create_image(0, 0, anchor="nw", image=photo,
                                        tags=tag)
                    return
            if glass.busy():
                canvas.delete(tag)
                canvas.create_rectangle(0, 0, width, height,
                                        fill=theme.BG, outline=theme.BG,
                                        tags=tag)
                return
            paint_wallpaper(canvas, width, height, offset=offset, win=win,
                            tag=tag)
            if not fade:
                return
            # 底部把壁纸淡回面板底色，避免一条生硬的接缝
            bands = 14
            fade_h = min(height * 0.42, theme.px(52))
            y0 = height - fade_h
            aspect = max(0.3, win[0] / max(1, win[1]))
            for index in range(bands):
                t = index / max(1, bands - 1)
                y = y0 + fade_h * t
                wall = theme.wallpaper_color_at(
                    theme.WALLPAPER, 0.5,
                    (offset[1] + y) / max(1.0, win[1]), aspect)
                canvas.create_rectangle(
                    0, y, width, y + fade_h / bands + 1,
                    fill=theme.blend(theme.BG, wall, t ** 1.3),
                    outline="", tags=tag)
        except tk.TclError:
            pass

    def _paint_body_wallpaper(self) -> None:
        host = getattr(self, "_glass_body", None)
        if host is not None:
            self._paint_wallpaper_canvas(host)

    def _defer_hero(self) -> None:
        """缩放时合并 hero 的重排/重绘，避免每个 Configure 都算一张壁纸。"""
        glass.set_busy(0.28)
        job = getattr(self, "_hero_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._hero_job = self.after(250, self._flush_hero)
        except tk.TclError:
            self._hero_job = None

    def _flush_hero(self) -> None:
        self._hero_job = None
        glass.set_busy(0)
        self._layout_glass_hero()

    def _defer_body(self) -> None:
        glass.set_busy(0.28)
        job = getattr(self, "_body_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._body_job = self.after(280, self._flush_body)
        except tk.TclError:
            self._body_job = None

    def _flush_body(self) -> None:
        self._body_job = None
        glass.set_busy(0)
        self._paint_body_wallpaper()

    def _build_neon_body(self, parent) -> None:
        body = tk.Frame(parent, bg=theme.BG)
        body.pack(fill="both", expand=True)
        self._build_searchbar(body)
        self._build_filterbar(body)
        self._build_list(body)
        self._build_footer(body)

    # ------------------------------------------------------------ 列表
    def _build_list(self, parent) -> None:
        list_style = self._layout.get("list", "rows")
        pad = theme.px(int(self._layout.get("content_pad", 18)))
        if list_style == "cards":
            wrap = tk.Frame(parent, bg=theme.BG, highlightthickness=0)
            wrap.pack(fill="both", expand=True, padx=pad,
                      pady=(theme.px(8), theme.px(8)))
        else:
            wrap = tk.Frame(parent, bg=theme.PANEL, highlightthickness=1,
                            highlightbackground=theme.BORDER)
            wrap.pack(fill="both", expand=True, padx=pad,
                      pady=(theme.px(4), 0))
        self.list_view = ResourceList(wrap, on_change=self._update_counts,
                                      on_open=self._open_item,
                                      on_context=self._show_context_menu)
        # 列表的“请求高度”保持很小，由 pack 的 expand 填满剩余空间，
        # 否则在悬浮底栏等更高的布局里会把底栏挤出窗口。
        try:
            self.list_view.configure(height=theme.px(100))
            self.list_view.pack_propagate(False)
            self.list_view.canvas.configure(height=theme.px(100))
        except tk.TclError:
            pass
        self.list_view.pack(fill="both", expand=True)
        self.list_view.canvas.bind("<Button-3>", self._show_context_menu, add="+")

    # ------------------------------------------------------------ 底栏
    def _build_footer(self, parent) -> None:
        style = self._layout.get("footer", "bar")
        pad = theme.px(int(self._layout.get("content_pad", 18)))
        self._progress_value = 0.0
        if style == "floating":
            if theme.GLASS and glass.available():
                self._build_glass_footer(parent)
                return
            panel = tk.Frame(parent, bg=theme.PANEL, highlightthickness=1,
                             highlightbackground=theme.BORDER_LIGHT)
            panel.pack(fill="x", padx=theme.px(24),
                       pady=(theme.px(2), theme.px(18)))
            tk.Frame(panel, bg=theme.blend(theme.PANEL, "#ffffff", 0.10),
                     height=1).pack(fill="x", padx=theme.px(16))
            self._make_status_row(panel, padx=theme.px(16),
                                  pady=(theme.px(8), theme.px(2)),
                                  bg=theme.PANEL)
            tk.Frame(panel, bg=theme.BORDER, height=1).pack(
                fill="x", padx=theme.px(16), pady=(theme.px(2), 0))
            self._make_action_row(panel, padx=theme.px(16),
                                  pady=(theme.px(8), theme.px(10)),
                                  bg=theme.PANEL)
            return
        if style == "dock":
            panel = tk.Frame(parent, bg=theme.PANEL, highlightthickness=1,
                             highlightbackground=theme.BORDER)
            panel.pack(fill="x", padx=pad, pady=(theme.px(4), theme.px(10)))
            tk.Frame(panel, bg=theme.ACCENT, height=1).pack(fill="x")
            self._make_status_row(panel, padx=theme.px(14),
                                  pady=(theme.px(6), theme.px(2)),
                                  bg=theme.PANEL)
            self._make_action_row(panel, padx=theme.px(14),
                                  pady=(theme.px(4), theme.px(8)),
                                  bg=theme.PANEL)
            return
        if style == "status":
            self._make_action_row(parent, padx=pad,
                                  pady=(theme.px(6), theme.px(4)),
                                  retro=True)
            status = tk.Frame(parent, bg=theme.PANEL, relief="sunken",
                              bd=1, highlightthickness=0)
            status.pack(fill="x", padx=pad, pady=(0, theme.px(8)))
            self._make_status_row(status, padx=theme.px(8),
                                  pady=theme.px(3), bg=theme.PANEL)
            return
        self._make_status_row(parent, padx=pad,
                              pady=(theme.px(8), theme.px(2)))
        tk.Frame(parent, bg=theme.BORDER, height=1).pack(
            fill="x", padx=pad, pady=(theme.px(6), 0))
        self._make_action_row(parent, padx=pad,
                              pady=(theme.px(10), theme.px(14)))

    # ---- 液态玻璃底栏：一块浮在壁纸上的磨砂玻璃条 ----
    def _build_glass_footer(self, parent) -> None:
        """底栏不再是实心白条，而是一块圆角磨砂玻璃，内容嵌在玻璃内部。

        Tk 控件没有透明背景，所以玻璃图片画在 Canvas 上，内容用
        ``create_window`` 叠在上面；内容容器的底色随后按玻璃主体色重新
        着色（``_retint``），接缝就看不出来了。
        """
        pad = theme.px(24)
        wrap = tk.Frame(parent, bg=theme.BG)
        wrap.pack(fill="x", padx=pad, pady=(theme.px(2), theme.px(16)))
        canvas = tk.Canvas(wrap, bg=theme.BG, highlightthickness=0, bd=0,
                           height=theme.px(92))
        canvas.pack(fill="x")
        self._footer_glass = canvas
        self._footer_fill = theme.blend(theme.CARD, theme.PANEL, 0.45)

        inner = tk.Frame(canvas, bg=self._footer_fill)
        self._footer_inner = inner
        self._make_status_row(inner, padx=theme.px(20),
                              pady=(theme.px(12), theme.px(2)),
                              bg=self._footer_fill)
        self._footer_sep = tk.Frame(
            inner, bg=theme.blend(theme.BORDER, self._footer_fill, 0.55),
            height=1)
        self._footer_sep.pack(fill="x", padx=theme.px(20),
                              pady=(theme.px(4), 0))
        self._make_action_row(inner, padx=theme.px(20),
                              pady=(theme.px(10), theme.px(12)),
                              bg=self._footer_fill)
        inner.update_idletasks()
        inset = theme.px(7)
        height = max(theme.px(86), inner.winfo_reqheight() + inset * 2)
        canvas.configure(height=height)
        self._footer_inset = inset
        self._footer_item = canvas.create_window(
            inset, inset, anchor="nw", window=inner,
            width=max(80, theme.px(560)), height=max(40, height - inset * 2))
        canvas.bind("<Configure>", lambda _e: self._defer_footer())

    def _draw_footer_glass(self) -> None:
        canvas = getattr(self, "_footer_glass", None)
        item = getattr(self, "_footer_item", None)
        if canvas is None or item is None:
            return
        try:
            width = canvas.winfo_width()
            height = canvas.winfo_height()
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            return
        inset = getattr(self, "_footer_inset", theme.px(7))
        # 内容高度要按当前字体/DPI 重新量一次：构建那一刻可能还没布局完，
        # 否则下载按钮会被玻璃条的下边缘裁掉。
        inner = getattr(self, "_footer_inner", None)
        need = height
        if inner is not None:
            try:
                need = inner.winfo_reqheight() + inset * 2
            except tk.TclError:
                need = height
        if need > height + 2:
            try:
                canvas.configure(height=need)
            except tk.TclError:
                pass
            height = need
        try:
            canvas.coords(item, inset, inset)
            canvas.itemconfigure(item, width=max(80, width - inset * 2),
                                 height=max(40, height - inset * 2))
        except tk.TclError:
            pass
        if glass.busy():
            return
        try:
            top = canvas.winfo_toplevel()
            offset = (canvas.winfo_rootx() - top.winfo_rootx(),
                      canvas.winfo_rooty() - top.winfo_rooty())
            win = (max(top.winfo_width(), width),
                   max(top.winfo_height(), height))
            image = glass.frosted_panel(
                width, height, theme.px(26), theme.WALLPAPER, offset, win,
                white=0.34, blur=max(6.0, theme.px(9)), rim=0.9,
                accent=(theme.ACCENT, 0.05))
            photo = glass.photo(
                image, ("footer", width, height, offset[0] // 24,
                        offset[1] // 24))
            if photo is None:
                return
            self._footer_ref = photo
            canvas.delete("footer")
            canvas.create_image(0, 0, anchor="nw", image=photo,
                                tags=("footer",))
            canvas.tag_lower("footer")
            fill = glass.average_color(image)
            if fill != getattr(self, "_footer_fill", None):
                self._footer_fill = fill
                self._retint(self._footer_inner, fill)
                try:
                    self._footer_sep.configure(
                        bg=theme.blend(theme.BORDER, fill, 0.55))
                except (AttributeError, tk.TclError):
                    pass
        except tk.TclError:
            pass

    def _retint(self, widget, color: str) -> None:
        """把子树里所有实心背景换成玻璃主体色（跳过自绘按钮的边框层）。"""
        try:
            cls = widget.winfo_class()
        except tk.TclError:
            return
        if cls in ("Frame", "Label"):
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass
        elif cls == "Canvas":
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass
        for child in list(widget.winfo_children()):
            self._retint(child, color)

    def _defer_footer(self) -> None:
        glass.set_busy(0.28)
        job = getattr(self, "_footer_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._footer_job = self.after(260, self._flush_footer)
        except tk.TclError:
            self._footer_job = None

    def _flush_footer(self) -> None:
        self._footer_job = None
        glass.set_busy(0)
        self._draw_footer_glass()

    def _make_status_row(self, parent, padx: int, pady,
                         bg: str = "") -> None:
        bg = bg or theme.BG
        status = tk.Frame(parent, bg=bg)
        status.pack(fill="x", padx=padx, pady=pady)
        self.status_label = tk.Label(status, text="等待开始…", bg=bg,
                                     fg=theme.FG_DIM, font=theme.FONT_SMALL,
                                     anchor="w")
        self.status_label.pack(side="left")
        self.progress = tk.Canvas(status, width=theme.px(160),
                                  height=theme.px(4), bg=bg,
                                  highlightthickness=0)
        self.progress.pack(side="right")
        self.route_label = tk.Label(
            status, text="● " + self._idle_route_text(),
            bg=bg, fg=theme.FG_FAINT, font=theme.FONT_SMALL)
        self.route_label.pack(side="right", padx=(0, theme.px(16)))

    def _make_action_row(self, parent, padx: int, pady, bg: str = "",
                         retro: bool = False) -> None:
        bg = bg or theme.BG
        actions = tk.Frame(parent, bg=bg)
        actions.pack(fill="x", padx=padx, pady=pady)
        folder = tk.Frame(actions, bg=bg)
        folder.pack(side="left")
        tk.Label(folder, text="保存到", bg=bg, fg=theme.FG_FAINT,
                 font=theme.FONT_SMALL).pack(side="left")
        self.folder_label = tk.Label(
            folder, text=self._shorten_path(self.download_dir),
            bg=bg, fg=theme.FG_DIM, font=theme.FONT_SMALL, cursor="hand2")
        self.folder_label.pack(side="left", padx=theme.px(8))
        self.folder_label.bind("<Button-1>", lambda _e: self.choose_folder())
        change = tk.Label(folder, text="更改", bg=bg, fg=theme.ACCENT,
                          font=theme.FONT_SMALL, cursor="hand2")
        change.pack(side="left")
        change.bind("<Button-1>", lambda _e: self.choose_folder())
        open_link = tk.Label(folder, text="打开", bg=bg, fg=theme.FG_DIM,
                             font=theme.FONT_SMALL, cursor="hand2")
        open_link.pack(side="left", padx=(theme.px(8), 0))
        open_link.bind("<Button-1>",
                       lambda _e: self._open_path(self.download_dir))
        open_link.bind("<Enter>",
                       lambda _e: open_link.configure(fg=theme.ACCENT))
        open_link.bind("<Leave>",
                       lambda _e: open_link.configure(fg=theme.FG_DIM))

        self.btn_download = AccentButton(
            actions, "下载", command=self.download,
            width=theme.px(112) if not retro else theme.px(96),
            height=theme.px(40) if not retro else theme.px(30),
            radius=theme.button_radius(), bg=bg,
            font=theme.FONT_BUTTON if not retro else theme.FONT_SMALL)
        self.btn_download.pack(side="right", padx=(0, theme.px(10)))
        self.count_label = tk.Label(actions, text="已选 0 项", bg=bg,
                                    fg=theme.FG_FAINT, font=theme.FONT_SMALL)
        self.count_label.pack(side="right", padx=(0, theme.px(14)))

    # ================================================================ 工具
    def _shorten_path(self, path: str, limit: int = 46) -> str:
        if len(path) <= limit:
            return path
        return path[:16] + "…" + path[-(limit - 18):]

    def _log(self, message: str) -> None:
        self.status_label.configure(text=message)

    def _idle_route_text(self) -> str:
        mode = smartproxy.normalize_mode(getattr(self.cfg, "proxy_mode", "auto"))
        return {smartproxy.AUTO: "线路：自动测速",
                smartproxy.PROXY: "线路：强制代理",
                smartproxy.DIRECT: "线路：强制直连"}.get(mode, "线路：自动测速")

    def _set_route_label(self, text: str, color: str = theme.FG_FAINT) -> None:
        try:
            self.route_label.configure(text="● " + text, fg=color)
        except (AttributeError, tk.TclError):
            pass

    def _log_raw(self, message: str) -> None:
        self.events.put(("log", message))

    def _set_progress(self, value: float) -> None:
        self._progress_value = max(0.0, min(100.0, value))
        self.progress.delete("all")
        w = int(self.progress.cget("width"))
        from .widgets import round_rect

        round_rect(self.progress, 0, 0, w, theme.px(4), theme.px(2),
                   fill=theme.BORDER, outline="")
        if self._progress_value > 0:
            round_rect(self.progress, 0, 0, max(3, w * self._progress_value / 100),
                       theme.px(4), theme.px(2), fill=theme.ACCENT, outline="")

    def _set_start_running(self, running: bool) -> None:
        if running:
            self.btn_start.set_text("停止")
            self.btn_start._color = theme.ERROR        # noqa: SLF001
            self.btn_start._hover_color = "#ff5b5b"    # noqa: SLF001
            self.btn_start._fill = theme.ERROR         # noqa: SLF001
            self.btn_start.set_command(self.stop_sniff)
            self.btn_start.set_state("normal")
        else:
            self.btn_start.set_text("开始")
            self.btn_start._color = theme.ACCENT       # noqa: SLF001
            self.btn_start._hover_color = theme.ACCENT_HOVER  # noqa: SLF001
            self.btn_start._fill = theme.ACCENT        # noqa: SLF001
            self.btn_start.set_command(self.start_sniff)
            self.btn_start.set_state("normal")

    # ================================================================ 事件泵
    def _pump_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    self._handle_event(kind, payload)
                except Exception as exc:                # 单条事件异常不影响后续
                    self._log(f"界面更新异常: {exc}")
        except queue.Empty:
            pass
        try:
            self._pump_job = self.after(80, self._pump_events)
        except tk.TclError:
            self._pump_job = None

    def _handle_event(self, kind: str, payload) -> None:
        if kind == "log":
            self._log(payload)
        elif kind == "item":
            # 列表自身会把批量插入的变更通知合并到空闲时执行，
            # 这里不再逐条重算统计，避免上千条资源时的二次方开销。
            self.list_view.add_item(payload)
        elif kind == "item_update":
            self.list_view.update_item(payload)
        elif kind == "item_remove":
            self.list_view.remove_item(payload)
        elif kind == "progress":
            self._log(payload)
            if not self.list_view.all_items():
                self.list_view.set_empty_hint(payload)
        elif kind == "sniff_done":
            self._on_sniff_done(payload)
        elif kind == "dl_start":
            self._log(f"开始下载：{payload.title}")
            self._sync_download_button()
            self._update_download_status()
        elif kind == "dl_progress":
            item = getattr(payload, "_item", None)
            if item is not None:
                item.state = "downloading"
                item.progress = payload.progress
                self.list_view.update_item(item)
            self._update_download_status()
        elif kind == "dl_finish":
            self._on_dl_finish(payload)
            self._sync_download_button()
        elif kind == "ytdlp_done":
            self._handle_ytdlp(payload)
        elif kind == "update_result":
            self._handle_update_result(payload)

    def _update_download_status(self) -> None:
        """底栏显示整个下载队列的进度（不覆盖嗅探状态）。"""
        if self.sniffer.running:
            return
        stats = self.downloader.stats()
        if not stats["total"]:
            return
        if stats["active"] or stats["pending"]:
            text = (f"下载中 {stats['active']} 个 · 已完成 {stats['finished']}"
                    f"/{stats['total']} · {stats['percent']:.0f}%")
            if stats["speed"]:
                text += f" · {fmt_size(stats['speed'])}/s"
            if stats["failed"]:
                text += f" · 失败 {stats['failed']}"
            self._log(text)
            self._set_progress(stats["percent"])
        elif stats["finished"] == stats["total"]:
            self._set_progress(100)

    # ================================================================ 嗅探
    def _current_options(self) -> SniffOptions:
        cfg = self.cfg
        return SniffOptions(
            deep=bool(self.var_deep.get()) if hasattr(self, "var_deep") else cfg.deep,
            dynamic=bool(self.var_dynamic.get()) if hasattr(self, "var_dynamic") else cfg.dynamic,
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
            ua_key=self.ua_dropdown.value() if hasattr(self, "ua_dropdown")
            else cfg.user_agent,
        )

    def _on_ua_change(self, key: str) -> None:
        """切换 User-Agent 伪装。"""
        self.cfg.user_agent = key
        cfgmod.save(self.cfg, self.base_dir)
        label = useragents.label_of(key)
        if key == "auto":
            self._log("User-Agent：默认（每次请求随机桌面 UA）")
        else:
            self._log(f"User-Agent 已伪装为：{label}")
            profile = useragents.get(key)
            if profile.mobile:
                self._log(f"User-Agent 已伪装为：{label}（移动端，部分站点会返回手机版页面）")

    def start_sniff(self) -> None:
        if self.sniffer.running or (self._ytdlp_thread is not None
                                    and self._ytdlp_thread.is_alive()):
            self.stop_sniff()
            return
        url = self.url_entry.value()
        if not url:
            self._log("请先输入要嗅探的网址")
            self.url_entry.focus_set()
            return
        url = normalize_url(url)
        if not url:
            self._log("网址格式不正确，请检查后重试")
            self.url_entry.focus_set()
            return
        if url != self.url_entry.value():
            self.url_entry.set_value(url)
        cfgmod.save(self.cfg, self.base_dir)
        self.list_view.clear()
        self.list_view.set_empty_hint("正在获取页面…")
        self._kind_filter = "all"
        self.tabs.select("all")
        self._set_progress(0)
        self._set_start_running(True)
        self._start_time = time.time()
        # 支持站点优先走 yt-dlp（能直接列出剧集）；否则普通嗅探
        if (self.cfg.use_ytdlp and ytdlp.available()
                and not mediainfo.looks_like_media_url(url)):
            self._start_ytdlp(url)
        else:
            self._begin_sniff(url)

    def _start_ytdlp(self, url: str) -> None:
        self._ytdlp_cancel.clear()
        self._set_route_label("线路：yt-dlp", theme.ACCENT)
        self._log("正在用 yt-dlp 解析…")
        self.list_view.set_empty_hint("正在用 yt-dlp 解析剧集 / 清晰度…")
        cancel = self._ytdlp_cancel

        def work() -> None:
            try:
                data = ytdlp.extract(url, cookie=self.cfg.cookie, timeout=45,
                                     cancel=cancel.is_set)
                self.events.put(("ytdlp_done", (url, data, "")))
            except Exception as exc:
                self.events.put(("ytdlp_done", (url, None, str(exc))))

        self._ytdlp_thread = threading.Thread(target=work, daemon=True,
                                              name="ytdlp")
        self._ytdlp_thread.start()

    def _handle_ytdlp(self, payload) -> None:
        url, data, error = payload
        self._ytdlp_thread = None
        if self._ytdlp_cancel.is_set():
            self._ytdlp_cancel.clear()
            self._set_start_running(False)
            self._set_route_label(self._idle_route_text())
            self._log("已停止")
            return
        entries = (data or {}).get("entries") or []
        if error or not entries:
            if error:
                self._log(f"yt-dlp 未支持该地址（{error[:60]}），改用普通嗅探")
            else:
                self._log("yt-dlp 没有返回条目，改用普通嗅探")
            self._begin_sniff(url)
            return
        self.sniffer.resources.clear()
        self.list_view.clear()
        count = 0
        for entry in entries:
            info = ytdlp.entry_to_item_data(entry, url)
            item = ResourceItem(
                url=info["url"], page_url=url, kind="video", fmt="",
                filename=info["title"], duration=info["duration"],
                size=info["size"], source="yt-dlp",
                extra=info.get("uploader") or "yt-dlp")
            stored, is_new = self.sniffer.resources.add(item)
            if is_new:
                self.list_view.add_item(stored)
                count += 1
        self._set_start_running(False)
        self._set_progress(100)
        self.list_view.set_empty_hint("还没有嗅探到资源\n在上方输入网址后点击「开始」")
        self._set_route_label("线路：yt-dlp", theme.ACCENT)
        self._log(f"yt-dlp：解析到 {count} 个条目（双击列表行可打开原页面）")
        self._update_counts()
        if self.cfg.auto_check_all and count:
            self.list_view.select_all()

    def _begin_sniff(self, url: str) -> None:
        self._set_route_label("线路：测速中…", theme.ACCENT)
        self._log("开始嗅探…")
        try:
            self.sniffer.start(url, self._current_options(),
                               proxy=self.cfg.proxy, cookie=self.cfg.cookie,
                               proxy_mode=self.cfg.proxy_mode)
        except Exception as exc:
            self._log(f"启动失败: {exc}")
            self._set_start_running(False)

    def stop_sniff(self) -> None:
        if self._ytdlp_thread is not None and self._ytdlp_thread.is_alive():
            self._ytdlp_cancel.set()
        self.sniffer.stop()
        self._log("正在停止…")
        self.btn_start.set_state("disabled")

    # ---- Sniffer 回调（工作线程）
    def _sniff_item(self, item: ResourceItem) -> None:
        self.events.put(("item", item))

    def _sniff_log(self, message: str) -> None:
        self.events.put(("log", message))

    def _sniff_progress(self, message: str) -> None:
        self.events.put(("progress", message))

    def _sniff_done(self, summary: dict) -> None:
        self.events.put(("sniff_done", summary))

    def _sniff_remove(self, item: ResourceItem) -> None:
        self.events.put(("item_remove", item))

    def _on_sniff_done(self, summary: dict) -> None:
        import time as _t

        self._set_start_running(False)
        self._set_progress(100)
        self.list_view.set_empty_hint("还没有嗅探到资源\n在上方输入网址后点击「开始」")
        elapsed = summary.get("elapsed", _t.time() - self._start_time)
        if summary.get("canceled"):
            message = f"已停止，共 {len(self.sniffer.resources)} 个资源"
        elif summary.get("error") and not len(self.sniffer.resources):
            message = f"嗅探失败：{summary['error']}"
        else:
            resources = self.sniffer.resources.items()
            known_size = sum(r.size or 0 for r in resources)
            message = (f"嗅探完成，共 {len(resources)} 个资源"
                       f"（用时 {elapsed:.1f} 秒）")
            if known_size:
                message += f" · 已知大小共 {fmt_size(known_size)}"
            if summary.get("browser_missing"):
                message += " · 未找到浏览器，仅静态嗅探"
        self._log(message)
        route = summary.get("route") or {}
        if route.get("label"):
            self._set_route_label(
                "线路：" + str(route["label"]),
                theme.ACCENT if route.get("used_proxy") else theme.FG_DIM)
        else:
            self._set_route_label(self._idle_route_text())
        self._update_counts()
        if self.cfg.auto_check_all and len(self.sniffer.resources):
            self.list_view.select_all()
        # 没有结果时给出针对性提示
        if not len(self.sniffer.resources) and not summary.get("canceled"):
            if summary.get("login_wall"):
                message += ("。该站点需要登录：请在「设置」里填入浏览器 Cookie 后重试")
            elif not summary.get("dynamic"):
                message += "。若是动态网站，请勾选「动态」后重试"
            else:
                message += "。未发现媒体资源，可尝试填入 Cookie 或换个 UA"
            self._log(message)

    # ---------------------------------------------------------------- 列表
    def _update_counts(self) -> None:
        items = self.list_view.all_items()
        counts: dict[str, int] = {}
        checked_items = []
        for item in items:
            counts[item.kind] = counts.get(item.kind, 0) + 1
            if getattr(item, "checked", False):
                checked_items.append(item)
        counts["all"] = len(items)
        self.tabs.set_counts(counts)
        checked = len(checked_items)
        total = len(items)
        side_stats = getattr(self, "side_stats", None)
        if side_stats is not None:
            try:
                side_stats.configure(text=f"共 {total} 项 · 已选 {checked}")
            except tk.TclError:
                self.side_stats = None
        if checked:
            size = sum(i.size or 0 for i in checked_items if i.size)
            self.count_label.configure(
                text=f"已选 {checked} 项 · {fmt_size(size) if size else '大小未知'}",
                fg=theme.FG)
        else:
            self.count_label.configure(text=f"已选 0 项 / 共 {total} 项",
                                       fg=theme.FG_FAINT)

    def _on_filter_change(self, kind: str) -> None:
        self._kind_filter = kind
        self._apply_filter()

    def _apply_filter(self) -> None:
        self._query = self.search_var.get() if hasattr(self, "search_var") else ""
        self.list_view.apply_filter(self._kind_filter, self._query)
        self._update_counts()

    def _on_sort_change(self, key: str) -> None:
        self.list_view.apply_sort(key)
        labels = {"default": "恢复嗅探顺序", "size": "按大小从大到小",
                  "kind": "按类型", "name": "按文件名"}
        self._log("排序：" + labels.get(key, key))

    def select_all(self) -> None:
        self.list_view.select_all()

    def select_none(self) -> None:
        self.list_view.select_none()

    def _on_select_all_key(self, _event=None):
        """Ctrl+A 全选当前筛选结果；在输入框里保留默认行为。"""
        if isinstance(self.focus_get(), (tk.Entry, tk.Spinbox)):
            return None
        self.select_all()
        return "break"

    def _on_focus_search(self, _event=None):
        """Ctrl+F 定位搜索框。"""
        entry = getattr(self, "search_entry", None)
        if entry is not None:
            entry.focus_set()
            entry.select_range(0, "end")
        return "break"

    def _open_item(self, item: ResourceItem) -> None:
        """双击：已下载的文件直接打开本地文件，否则在浏览器里打开链接。"""
        if item.state == "done" and item.save_path and os.path.exists(
                item.save_path):
            self._open_path(item.save_path)
            return
        if item.url:
            webbrowser.open(item.url)

    def _open_path(self, path: str) -> None:
        path = (path or "").strip()
        if not path:
            self._log("路径为空")
            return
        if not os.path.exists(path):
            self._log(f"路径不存在：{path}")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)        # type: ignore[attr-defined]
            else:
                target = os.path.abspath(path).replace("\\", "/")
                webbrowser.open("file:///" + target)
        except OSError as exc:
            self._log(f"打开失败：{exc}")

    def _open_file(self, item: ResourceItem) -> None:
        self._open_path(item.save_path)

    def _open_folder(self, item: ResourceItem) -> None:
        folder = (os.path.dirname(os.path.abspath(item.save_path))
                  if item.save_path else self.download_dir)
        self._open_path(folder)

    def _cancel_item(self, item: ResourceItem) -> None:
        for task in list(self.downloader.tasks):
            if (getattr(task, "_item", None) is item
                    and task.state in ("pending", "running")):
                self.downloader.cancel_task(task)
                self._log(f"正在取消：{task.title}")
                return
        self._log("该资源没有正在进行的下载")

    def _export_links(self, items) -> None:
        items = [i for i in (items or []) if i.url]
        if not items:
            self._log("没有可导出的链接")
            return
        initial = time.strftime("links_%Y%m%d_%H%M%S.txt")
        path = filedialog.asksaveasfilename(
            parent=self, title="导出链接", initialfile=initial,
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(i.url for i in items) + "\n")
        except OSError as exc:
            self._log(f"导出失败：{exc}")
            return
        self._log(f"已导出 {len(items)} 条链接 → {path}")

    def _show_context_menu(self, event, item=None) -> None:
        if item is None:
            index = self.list_view._row_at(event.y)   # noqa: SLF001
            if index is None:
                return
            item = self.list_view.items[index]
        selected = self.list_view.selected_items() or [item]
        if item not in selected:
            selected = [item]
        menu = tk.Menu(self, tearoff=0, bg=theme.PANEL, fg=theme.FG,
                       activebackground=theme.ACCENT, activeforeground="#ffffff",
                       bd=0, font=theme.FONT_SMALL)
        menu.add_command(label=f"下载选中（{len(selected)} 项）",
                         command=lambda: self.download(selected))
        menu.add_separator()
        menu.add_command(label="复制链接",
                         command=lambda: self._copy_links(selected))
        menu.add_command(label="在浏览器中打开",
                         command=lambda: self._open_item(item))
        menu.add_command(label="复制文件名",
                         command=lambda: self._copy_text(item.filename))
        menu.add_command(label="导出选中链接…",
                         command=lambda: self._export_links(selected))
        if item.state == "done" and item.save_path:
            menu.add_separator()
            menu.add_command(label="打开文件",
                             command=lambda: self._open_file(item))
            menu.add_command(label="打开所在文件夹",
                             command=lambda: self._open_folder(item))
            menu.add_command(label="复制保存路径",
                             command=lambda: self._copy_text(item.save_path))
        elif item.state == "downloading":
            menu.add_separator()
            menu.add_command(label="取消此下载",
                             command=lambda: self._cancel_item(item))
        elif item.state == "error":
            menu.add_separator()
            menu.add_command(label="重试下载",
                             command=lambda: self.download([item]))
            if item.message:
                menu.add_command(label="复制错误信息",
                                 command=lambda: self._copy_text(item.message))
        if item.kind == "stream":
            menu.add_separator()
            menu.add_command(label="展开清单（解析清晰度）",
                             command=lambda: self._expand_item(item))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self._log("已复制到剪贴板")

    def _copy_links(self, items) -> None:
        self._copy_text("\n".join(i.url for i in items))

    def _expand_item(self, item: ResourceItem) -> None:
        """手动展开一个 m3u8/mpd 清单。"""
        from . import streams
        from .models import ResourceItem as RI

        def work():
            try:
                resp = self.sniffer._ctx.session.peep(  # noqa: SLF001
                    item.url, referer=item.page_url, nbytes=2 << 20)
                text = resp.body.decode(resp.charset, errors="replace")
                pl = streams.parse_manifest(text, resp.url or item.url,
                                            resp.content_type)
                count = 0
                for idx, variant in enumerate(pl.variants):
                    child = RI(url=variant.url, page_url=item.page_url,
                               kind=variant.kind, fmt=variant.container,
                               filename=streams.variant_display_name(variant, idx)
                               + "." + streams.guess_extension(variant),
                               width=variant.width, height=variant.height,
                               bitrate=variant.bandwidth, duration=pl.duration,
                               source="manifest", child_of=item.url,
                               extra=variant.codecs)
                    stored, is_new = self.sniffer.resources.add(child)
                    if is_new:
                        self.events.put(("item", stored))
                        count += 1
                self.events.put(("log", f"清单解析完成，新增 {count} 个清晰度"))
            except Exception as exc:
                self.events.put(("log", f"清单解析失败：{exc}"))

        threading.Thread(target=work, daemon=True).start()
        self._log("正在解析清单…")

    # ================================================================ 下载
    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.download_dir,
                                         title="选择保存目录")
        if folder:
            self.download_dir = folder
            self.cfg.download_dir = folder
            cfgmod.save(self.cfg, self.base_dir)
            self.folder_label.configure(text=self._shorten_path(folder))

    def _sync_download_route(self) -> None:
        """下载复用最近一次嗅探选中的线路；没有嗅探记录时按设置选一条。"""
        route = getattr(self.sniffer, "route", None)
        if route is not None and route.mode != smartproxy.normalize_mode(
                self.cfg.proxy_mode):
            route = None                     # 设置里改过模式，旧线路作废
        if route is None:
            route = smartproxy.quick_route(
                self.cfg.proxy_mode, self.cfg.proxy,
                normalize_url(self.url_entry.value()))
        smartproxy.apply_route(self.downloader.session, route)

    def download(self, items=None) -> None:
        items = items or self.list_view.checked_items()
        if not items:
            self._log("请先勾选要下载的资源（点击列表行左侧复选框）")
            return
        # 跳过已在队列里的同一链接，以及已经下载完成的条目，避免重复文件
        active = self.downloader.active_urls()
        ready = []
        skipped = 0
        for item in items:
            if item.state == "downloading" or item.url in active:
                skipped += 1
                continue
            if item.state == "done" and item.save_path and os.path.exists(
                    item.save_path):
                skipped += 1
                continue
            ready.append(item)
        if not ready:
            self._log(f"所选的 {len(items)} 项已在下载队列或已下载，无需重复下载")
            return
        self._sync_download_route()
        os.makedirs(self.download_dir, exist_ok=True)
        self.downloader.max_workers = max(
            1, min(DownloadManager.MAX_WORKERS, int(self.cfg.concurrency or 1)))
        self.downloader.segments = max(
            1, min(8, int(getattr(self.cfg, "download_segments", 4) or 1)))
        tasks = self.downloader.enqueue(ready, self.download_dir,
                                        namer=self._name_for)
        # 资源条目与下载任务关联，便于在列表里展示进度
        for task, item in zip(tasks, ready):
            setattr(task, "_item", item)
            if getattr(item, "source", "") == "yt-dlp" and ytdlp.available():
                task.engine = "ytdlp"
                task.cookie = self.cfg.cookie
            item.state = "downloading"
            item.progress = 0.0
        self.downloader.start()
        total = sum(t.total_bytes for t in tasks)
        message = (f"已加入下载队列：{len(tasks)} 个文件"
                   f"{'，共 ' + fmt_size(total) if total else ''}"
                   f" → {self.download_dir}")
        if skipped:
            message += f"（跳过 {skipped} 项重复/已下载）"
        self._log(message)
        self.list_view.redraw()
        self._sync_download_button()

    def stop_downloads(self) -> None:
        """「停止下载」：取消排队任务，并让进行中的任务尽快退出。"""
        if not self.downloader.busy:
            self._sync_download_button()
            return
        self.downloader.cancel_all()
        self._log("正在停止下载…")
        self._sync_download_button()

    def _sync_download_button(self) -> None:
        """下载队列空闲时显示「下载」，有任务时变成「停止下载」。"""
        busy = self.downloader.busy
        if busy:
            self.btn_download.set_text("停止下载")
            self.btn_download._color = theme.ERROR          # noqa: SLF001
            self.btn_download._hover_color = "#ff5b5b"      # noqa: SLF001
            self.btn_download._fill = theme.ERROR           # noqa: SLF001
            self.btn_download.set_command(self.stop_downloads)
        else:
            self.btn_download.set_text("下载")
            self.btn_download._color = theme.ACCENT         # noqa: SLF001
            self.btn_download._hover_color = theme.ACCENT_HOVER  # noqa: SLF001
            self.btn_download._fill = theme.ACCENT          # noqa: SLF001
            self.btn_download.set_command(self.download)
        self.btn_download.set_state("normal")

    def _name_for(self, item: ResourceItem) -> str:
        from .htmlparse import ensure_extension, guess_filename

        name = item.filename or guess_filename(item.url)
        # yt-dlp 标题里可能有 Windows 非法字符，统一清洗并限制长度
        name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
        if not name:
            name = "download"
        if item.kind == "stream" and item.fmt in ("m3u8", "mpd"):
            base = os.path.splitext(name)[0] or "stream"
            name = base + (".ts" if item.fmt == "m3u8" else ".mp4")
        name = ensure_extension(name, item.kind, item.fmt)
        root, ext = os.path.splitext(name)
        if len(name) > 120:
            name = root[:max(1, 120 - len(ext))] + ext
        return name

    def _dl_start(self, task: DownloadTask) -> None:
        self.events.put(("dl_start", task))

    def _dl_progress(self, task: DownloadTask) -> None:
        self.events.put(("dl_progress", task))

    def _dl_finish(self, task: DownloadTask) -> None:
        self.events.put(("dl_finish", task))

    def _on_dl_finish(self, task: DownloadTask) -> None:
        item = getattr(task, "_item", None)
        if item is not None:
            if task.state == "done":
                item.state = "done"
                item.progress = 100.0
                item.save_path = task.filepath
                item.message = ""
            elif task.state == "error":
                item.state = "error"
                item.message = task.error
            else:
                item.state = "ready"
                item.progress = 0.0
            self.list_view.update_item(item)
        if task.state == "done":
            self._log(f"下载完成：{os.path.basename(task.filepath)}"
                      f"（{fmt_size(task.total_bytes)}）")
        elif task.state == "error":
            self._log(f"下载失败：{os.path.basename(task.filepath)} · {task.error}")
        elif task.state == "canceled":
            self._log(f"已取消：{os.path.basename(task.filepath)}")
        self._update_counts()
        self._update_download_status()

    # ================================================================ 更新
    def _auto_check_updates(self) -> None:
        if self._closing or not getattr(self.cfg, "check_updates", True):
            return
        self._start_update_check(silent=True)

    def check_updates_manual(self) -> None:
        self._start_update_check(silent=False)

    def _start_update_check(self, silent: bool) -> None:
        if self._update_thread is not None and self._update_thread.is_alive():
            if not silent:
                self._log("正在检查更新，请稍候…")
            return
        self._set_update_badge("检查中…")
        if not silent:
            self._log("正在检查 GitHub 更新…")

        def work() -> None:
            try:
                info = updater.check_latest(timeout=8)
                self.events.put(("update_result", (info, None, silent)))
            except Exception as exc:
                self.events.put(("update_result", (None, exc, silent)))

        self._update_thread = threading.Thread(target=work, daemon=True,
                                               name="update-check")
        self._update_thread.start()

    def _handle_update_result(self, payload) -> None:
        info, error, silent = payload
        self._update_thread = None
        if silent and not getattr(self.cfg, "check_updates", True):
            return
        if error is not None:
            self._set_update_badge("")
            message = updater.describe_error(error)
            if not silent:
                self._log("检查更新失败：" + message)
                messagebox.showwarning(
                    "检查更新", f"暂时无法检查更新。\n\n{message}",
                    parent=self)
            return
        self._update_info = info
        if not info.is_newer_than():
            self._set_update_badge("")
            if not silent:
                self._log(f"已是最新版本 v{APP_VERSION}")
                messagebox.showinfo(
                    "检查更新",
                    f"当前已是最新版本 v{APP_VERSION}。\n"
                    f"最新 Release：{info.tag}",
                    parent=self)
            return
        if (self.cfg.skipped_version or "").lstrip("vV") == info.version:
            self._set_update_badge("")
            if not silent:
                self._log(f"新版本 v{info.version} 已跳过")
                messagebox.showinfo(
                    "检查更新",
                    f"新版本 v{info.version} 已被设置为跳过。\n"
                    "可在「设置 → 关于」里重新启用。",
                    parent=self)
            return
        self._set_update_badge(f"↑ v{info.version}")
        if not silent:
            self._log(f"发现新版本 v{info.version}（当前 v{APP_VERSION}）")
        # 自动检查只点亮标题栏徽标，不弹模态窗口、不抢焦点；
        # 手动检查才直接展开更新说明。
        if not silent:
            self._show_update_dialog(info)

    def _show_update_dialog(self, info) -> None:
        dialog = getattr(self, "_update_dialog", None)
        try:
            if dialog is not None and dialog.winfo_exists():
                dialog.lift()
                return
        except tk.TclError:
            pass
        from .prefs import UpdateDialog

        self._update_dialog = UpdateDialog(
            self, info, self.cfg, self._save_config_silent)

    def _on_update_badge_click(self) -> None:
        if self._update_info is not None:
            self._show_update_dialog(self._update_info)
        else:
            self.check_updates_manual()

    def _set_update_badge(self, text: str) -> None:
        badge = getattr(self, "btn_update", None)
        if badge is None:
            return
        try:
            if text:
                badge.configure(text=text)
                if not badge.winfo_ismapped():
                    badge.pack(side="right", padx=(0, theme.px(4)))
            else:
                badge.pack_forget()
                badge.configure(text="")
        except tk.TclError:
            pass

    def _save_config_silent(self) -> None:
        try:
            cfgmod.save(self.cfg, self.base_dir)
        except Exception:
            pass
        if not getattr(self.cfg, "check_updates", True):
            self._set_update_badge("")
        elif (self._update_info is not None
              and (self.cfg.skipped_version or "").lstrip("vV")
              == self._update_info.version):
            self._set_update_badge("")

    # ================================================================ 其他
    def open_settings(self) -> None:
        if self.sniffer.running or (self._ytdlp_thread is not None
                                    and self._ytdlp_thread.is_alive()):
            self._log("嗅探进行中，请等结束后再修改设置")
            return
        existing = getattr(self, "_settings_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        self._settings_dialog = SettingsDialog(
            self, self.cfg, self._apply_preferences,
            self._save_config_silent)

    def _apply_preferences(self, _cfg: Config) -> None:
        cfgmod.save(self.cfg, self.base_dir)
        theme.apply_style(self.cfg.theme_preset, self.cfg.accent,
                          self.cfg.radius_button, self.cfg.radius_window,
                          self.cfg.animations, self.cfg.anim_fps)
        theme.apply_scale(self)
        mediainfo.THUMBNAIL_SIDE = theme.THUMB_SIZE
        self.download_dir = (self.cfg.download_dir
                             or cfgmod.default_download_dir(self.base_dir))
        try:
            os.makedirs(self.download_dir, exist_ok=True)
        except OSError:
            pass
        self.sniffer.route = None
        self.downloader.segments = max(
            1, min(8, int(getattr(self.cfg, "download_segments", 4) or 1)))
        self._rebuild_ui()
        self._log("设置已应用")

    def _rebuild_ui(self) -> None:
        """换肤：保留资源/筛选/输入，销毁并重建全部子控件。"""
        items = self.list_view.all_items() if hasattr(self, "list_view") else []
        sort_key = (self.sort_dropdown.value()
                    if hasattr(self, "sort_dropdown") else "default")
        kind_filter = self._kind_filter
        query = self._query
        url_text = self.url_entry.value() if hasattr(self, "url_entry") else ""
        progress_value = getattr(self, "_progress_value", 0.0)
        status_text = (str(self.status_label.cget("text"))
                       if hasattr(self, "status_label") else "")
        maximized = self._maximized
        if self._header_job is not None:
            try:
                self.after_cancel(self._header_job)
            except tk.TclError:
                pass
            self._header_job = None
        for child in list(self.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass
        self.configure(bg=theme.BG)
        mediainfo.THUMBNAIL_SIDE = theme.THUMB_SIZE
        self._build_ui()
        self._resizer = frameless.WindowResizer(
            self, theme.px(860), theme.px(560),
            is_maximized=lambda: self._maximized)
        self.list_view.set_source(items)
        if query:
            self.search_var.set(query)
        self.tabs.select(kind_filter)
        self.list_view.apply_sort(sort_key)
        self.sort_dropdown.set_value(sort_key)
        if url_text:
            self.url_entry.set_value(url_text)
        self._set_progress(progress_value)
        if status_text:
            self._log(status_text)
        self._update_counts()
        self._on_max_state(maximized)
        self._sync_download_button()
        self.after(300, self._apply_window_glass)
        if (self._update_info is not None
                and self._update_info.is_newer_than()
                and (self.cfg.skipped_version or "").lstrip("vV")
                != self._update_info.version):
            self._set_update_badge(f"↑ v{self._update_info.version}")

    def _apply_window_radius(self) -> None:
        radius = 0 if self._maximized else theme.window_radius()
        try:
            frameless.apply_window_radius(self, radius, theme.BG, theme.BORDER)
        except Exception:
            pass

    def _apply_window_glass(self) -> None:
        """亚克力主题：开启 Windows 真模糊 + 半透明窗口。

        液态玻璃保持不透明窗口，只在控件内部绘制反射/折射，避免桌面
        内容干扰“光从左上角来”的固定光照模型。
        """
        if self._closing:
            return
        try:
            if theme.ACRYLIC:
                self.attributes("-alpha", 0.975)
                frameless.enable_acrylic(self, True, 0xCC20150b)
            elif theme.ACTIVE_PRESET == "glass":
                # 液态玻璃是浅色的，保持不透明才能保证深色文字清楚；
                # 玻璃的透光和折射全部在控件内部自绘。
                self.attributes("-alpha", 1.0)
                frameless.enable_acrylic(self, False)
            else:
                self.attributes("-alpha", 1.0)
                frameless.enable_acrylic(self, False)
        except tk.TclError:
            pass

    def show_help(self) -> None:
        text = (
            "【基本用法】\n"
            "1. 顶部输入网址，点「开始」。支持站点会优先用 yt-dlp 拉取剧集列表；\n"
            "   不支持的站点自动回到普通嗅探。勾选「动态」会启动内置无头浏览器。\n"
            "2. 列表左侧是预览：图片显示缩略图，音频显示时长和码率，"
            "视频显示分辨率和时长。\n"
            "3. 点击列表行（或最左侧复选框）勾选，点底部「下载」保存到本地。\n\n"
            "【提示】\n"
            "· 双击资源在浏览器中打开；已下载的文件双击直接打开。\n"
            "· 右键可复制链接、导出选中链接、展开 m3u8 清单；已下载的条目可以\n"
            "  打开文件 / 打开所在文件夹。\n"
            "· 下载时按钮会变成「停止下载」，右键单个任务也可以只取消它。\n"
            "· Ctrl+A 全选可见资源，Ctrl+F 定位搜索框；筛选栏的「排序」可按\n"
            "  大小 / 类型 / 文件名重排（只影响显示，不影响勾选）。\n"
            "· 「开始」右边的 UA 下拉框可把请求伪装成不同浏览器。\n"
            "· 「设置 → 外观」可以换主题、主题色、按钮 / 窗口圆角，并实时预览。\n"
            "· 「设置 → 网络」填好代理后，每次嗅探前会自动测速选线路。\n"
            "· 默认下载到软件目录下的 Download 文件夹，点底栏「更改」可换目录。\n"
        )
        messagebox.showinfo("使用说明", text, parent=self)

    def _on_close(self) -> None:
        # Alt+F4 与系统 WM_CLOSE 可能同时触发，保证只关一次
        if getattr(self, "_closing", False):
            return
        self._closing = True
        try:
            self._remember_geometry()
            cfgmod.save(self.cfg, self.base_dir)
        except Exception:
            pass
        try:
            self._ytdlp_cancel.set()
            self.sniffer.stop()
            self.downloader.stop()
        except Exception:
            pass
        for attr in ("_pump_job", "_update_job", "_header_job",
                     "_hero_job", "_body_job", "_footer_job"):
            try:
                job = getattr(self, attr, None)
                if job is not None:
                    self.after_cancel(job)
                    setattr(self, attr, None)
            except tk.TclError:
                pass
        try:
            self.attributes("-alpha", 1.0)
            frameless.enable_acrylic(self, False)
        except tk.TclError:
            pass
        theme.end_timer_resolution()
        self.destroy()


def run() -> None:
    # 必须在创建 Tk 窗口前声明 DPI 感知，否则 150% 缩放下界面会被系统位图拉伸
    theme.enable_dpi_awareness()
    app = MainWindow()
    app.mainloop()
