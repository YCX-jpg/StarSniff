"""窗口动画：淡入淡出 + 轻微位移，只用 after 定时器，不开新线程、不碰 GPU。

所有动画都受 ``theme.ANIMATIONS`` 控制：设置里关掉「动态」后立刻变成直接显示，
用来省电 / 省显卡。
"""

from __future__ import annotations

import tkinter as tk

from . import theme

# 单帧间隔的最小值：60 FPS 对窗口属性来说已经够顺，再快只是白烧 CPU
_MIN_FRAME = 16


def _delay() -> int:
    return max(_MIN_FRAME, theme.frame_delay())


def _alive(widget) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _alpha(window, value: float) -> None:
    try:
        window.attributes("-alpha", max(0.0, min(1.0, value)))
    except tk.TclError:
        pass


def _current_alpha(window) -> float:
    try:
        return float(window.attributes("-alpha"))
    except (tk.TclError, ValueError):
        return 1.0


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _geometry_safe(window) -> bool:
    """最大化/全屏时不要动 geometry，否则会把窗口拽回普通大小。"""
    try:
        return not window.state().startswith(("zoomed", "iconic"))
    except tk.TclError:
        return False


def open_window(window, dy: int = 0, duration: int = 170,
                min_alpha: float = 0.0, target: float | None = None) -> None:
    """淡入 + 从下方滑入；动画结束后恢复成 target（默认取当前 alpha）。

    即使关了「动态」，也保证把窗口 alpha 设回目标值——否则调用方在创建时
    把 alpha 设成 0 会留下一个看不见的窗口。
    """
    if target is None:
        target = getattr(window, "_motion_alpha", None)
        if not target:
            target = _current_alpha(window)
    if target <= 0.05:
        target = 1.0
    try:
        window._motion_alpha = target
    except tk.TclError:
        pass
    if not theme.ANIMATIONS or not _alive(window):
        _alpha(window, target)
        return
    try:
        window.update_idletasks()
    except tk.TclError:
        return
    width, height = window.winfo_width(), window.winfo_height()
    if width <= 1 or height <= 1:
        _alpha(window, target)
        return
    base_x, base_y = window.winfo_x(), window.winfo_y()
    slide = dy and _geometry_safe(window)
    steps = max(4, int(duration / max(10, _delay())))

    def frame(i: int = 0) -> None:
        if not _alive(window):
            return
        t = i / steps
        eased = _ease_out(t)
        _alpha(window, min_alpha + (target - min_alpha) * eased)
        if slide and i < steps:
            try:
                window.geometry(
                    "%dx%d+%d+%d" % (width, height, base_x,
                                     base_y + int(dy * (1.0 - eased))))
            except tk.TclError:
                return
        if i >= steps:
            _alpha(window, target)
            if slide:
                try:
                    window.geometry("%dx%d+%d+%d"
                                    % (width, height, base_x, base_y))
                except tk.TclError:
                    pass
            return
        try:
            window.after(_delay(), lambda: frame(i + 1))
        except tk.TclError:
            pass

    _alpha(window, min_alpha)
    if slide:
        try:
            window.geometry("%dx%d+%d+%d"
                            % (width, height, base_x, base_y + dy))
        except tk.TclError:
            pass
    frame(0)


def close_window(window, on_done, dy: int = 0, duration: int = 130) -> None:
    """淡出 + 轻微下沉，结束后再真正 destroy。"""
    if not theme.ANIMATIONS or not _alive(window):
        on_done()
        return
    start = _current_alpha(window)
    width, height = window.winfo_width(), window.winfo_height()
    base_x, base_y = window.winfo_x(), window.winfo_y()
    slide = dy and width > 1 and height > 1 and _geometry_safe(window)
    steps = max(3, int(duration / max(10, _delay())))

    def frame(i: int = 0) -> None:
        if not _alive(window):
            return
        t = i / steps
        eased = _ease_out(t)
        _alpha(window, start * (1.0 - eased))
        if slide and i < steps:
            try:
                window.geometry("%dx%d+%d+%d"
                                % (width, height, base_x,
                                   base_y + int(dy * eased)))
            except tk.TclError:
                return
        if i >= steps:
            _alpha(window, start)
            if slide:
                try:
                    window.geometry("%dx%d+%d+%d"
                                    % (width, height, base_x, base_y))
                except tk.TclError:
                    pass
            on_done()
            return
        try:
            window.after(_delay(), lambda: frame(i + 1))
        except tk.TclError:
            on_done()

    frame(0)


def minimize_window(window, do_minimize, duration: int = 120) -> None:
    """先轻轻缩小淡出，再交给系统最小化；还原时由调用方播放打开动画。"""
    if not theme.ANIMATIONS or not _alive(window):
        do_minimize()
        return
    target = getattr(window, "_motion_alpha", _current_alpha(window)) or 1.0
    steps = max(3, int(duration / max(10, _delay())))

    def frame(i: int = 0) -> None:
        if not _alive(window):
            return
        t = _ease_out(i / steps)
        _alpha(window, target * (1.0 - 0.92 * t))
        if i >= steps:
            _alpha(window, target)
            do_minimize()
            return
        try:
            window.after(_delay(), lambda: frame(i + 1))
        except tk.TclError:
            do_minimize()

    frame(0)
