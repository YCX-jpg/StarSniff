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


def _set_option(widget, option: str, value) -> None:
    try:
        widget.configure(**{option: value})
    except tk.TclError:
        pass


def _set_text(widget, text: str) -> None:
    try:
        widget.configure(text=text)
    except tk.TclError:
        pass


def fade_swap(window, action, out_duration: int = 110,
              in_duration: int = 180, low_ratio: float = 0.45) -> None:
    """淡出 → 执行 action（重建控件）→ 淡入，用于切换主题。

    换肤时控件是整体重建的，直接换会「啪」地跳一下；这里让窗口先暗下去，
    重建完成后再亮回来，视觉上像一次干净的切换。
    """
    if not theme.ANIMATIONS or not _alive(window):
        action()
        return
    target = getattr(window, "_motion_alpha", None)
    if not target:
        target = _current_alpha(window) or 1.0
    if target <= 0.05:
        target = 1.0
    try:
        window._motion_alpha = target
    except tk.TclError:
        pass
    start = _current_alpha(window)
    low = max(0.05, target * max(0.05, min(0.95, low_ratio)))
    out_steps = max(2, int(out_duration / max(10, _delay())))
    in_steps = max(3, int(in_duration / max(10, _delay())))

    def fade_in(i: int = 0) -> None:
        if not _alive(window):
            return
        t = min(1.0, i / in_steps)
        _alpha(window, low + (target - low) * _ease_out(t))
        if i >= in_steps:
            _alpha(window, target)
            return
        try:
            window.after(_delay(), lambda: fade_in(i + 1))
        except tk.TclError:
            _alpha(window, target)

    def fade_out(i: int = 0) -> None:
        if not _alive(window):
            return
        t = min(1.0, i / out_steps)
        _alpha(window, start + (low - start) * _ease_out(t))
        if i >= out_steps:
            try:
                action()
            except Exception:  # noqa: BLE001 - 重建失败也要把窗口亮回来
                pass
            fade_in(0)
            return
        try:
            window.after(_delay(), lambda: fade_out(i + 1))
        except tk.TclError:
            action()
            fade_in(0)

    fade_out(0)


_TWEEN_JOBS: dict[str, str] = {}


def _cancel_tween(widget, key: str) -> None:
    job = _TWEEN_JOBS.pop(key, None)
    if job is not None:
        try:
            widget.after_cancel(job)
        except tk.TclError:
            pass


def tween_color(widget, option: str, end: str, duration: int = 140) -> None:
    """把控件某个颜色选项从当前值平滑过渡到 ``end``（清空/选择输入框等）。"""
    try:
        start = str(widget.cget(option))
    except tk.TclError:
        return
    key = f"{id(widget)}:{option}"
    _cancel_tween(widget, key)
    if not theme.ANIMATIONS or not start or start == end:
        _set_option(widget, option, end)
        return
    steps = max(3, int(duration / max(10, _delay())))

    def frame(i: int = 0) -> None:
        _TWEEN_JOBS.pop(key, None)
        t = min(1.0, i / steps)
        # theme.blend(a, b, w)：w=0 是 b，w=1 是 a，正好从 start 走到 end
        _set_option(widget, option, theme.blend(end, start, _ease_out(t)))
        if i >= steps:
            _set_option(widget, option, end)
            return
        try:
            _TWEEN_JOBS[key] = widget.after(_delay(), lambda: frame(i + 1))
        except tk.TclError:
            _TWEEN_JOBS.pop(key, None)

    frame(0)


def tween_numbers(label, targets: dict, render, duration: int = 280) -> None:
    """让 label 上的几个数字一起滚动到目标值（只用于纯数字计数）。"""
    end = {key: int(value) for key, value in targets.items()}
    state = getattr(label, "_tween_state", None) or {}
    start = {key: int(state.get(key, value)) for key, value in end.items()}
    job = getattr(label, "_tween_job", None)
    if job is not None:
        try:
            label.after_cancel(job)
        except tk.TclError:
            pass
        label._tween_job = None
    if not theme.ANIMATIONS or start == end:
        label._tween_state = dict(end)
        try:
            label.configure(text=render(end))
        except tk.TclError:
            pass
        return
    steps = max(4, int(duration / max(10, _delay())))

    def frame(i: int = 0) -> None:
        label._tween_job = None
        t = min(1.0, i / steps)
        eased = _ease_out(t)
        values = {key: int(round(start[key] + (end[key] - start[key]) * eased))
                  for key in end}
        label._tween_state = dict(values)
        try:
            label.configure(text=render(values))
        except tk.TclError:
            return
        if i >= steps:
            label._tween_state = dict(end)
            try:
                label.configure(text=render(end))
            except tk.TclError:
                pass
            return
        try:
            label._tween_job = label.after(_delay(), lambda: frame(i + 1))
        except tk.TclError:
            label._tween_job = None

    frame(0)


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
