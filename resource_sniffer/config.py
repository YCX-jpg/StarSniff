"""配置读写（config.json，放在程序目录）。

配置文件可能是旧版本、手改过的，甚至是写坏的：读取时统一做类型转换和范围
裁剪，只保留认识的字段，保证任何脏数据都不会让程序崩溃；保存时先写临时文件
再原子替换，避免中途断电留下半个 JSON。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

KINDS = ("image", "audio", "video", "stream")

# 数值字段 -> (最小值, 最大值)：超出范围按边界处理，类型不对则用默认值
_INT_RANGES = {
    "max_items": (50, 10000),
    "workers": (1, 32),
    "concurrency": (1, 8),
    "download_segments": (1, 8),
    "anim_fps": (5, 120),
    "min_image_side": (0, 8192),
    "radius_button": (0, 28),
    "radius_window": (0, 30),
}
_FLOAT_RANGES = {
    "dynamic_wait": (1.0, 60.0),
    "timeout": (1.0, 120.0),
    "probe_deadline": (5.0, 300.0),
}
_BOOL_FIELDS = ("dynamic", "deep", "scroll", "headless",
                "auto_check_all", "animations", "use_ytdlp",
                "check_updates")
_STR_FIELDS = ("download_dir", "proxy", "cookie", "browser_path",
               "user_agent", "window_geometry", "theme_preset", "accent",
               "skipped_version")
THEME_PRESETS = ("default", "acrylic", "glass", "neon", "retro")


def app_dir() -> str:
    """软件所在目录（config.json 与默认的 Download 文件夹都放这里）。

    PyInstaller 单文件 exe 解压后 ``__file__`` 位于临时目录，必须改用
    ``sys.executable`` 所在目录，否则配置会写进每次启动都变的临时目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_download_dir(base_dir: str = "") -> str:
    """默认下载目录：软件目录下的 ``Download`` 文件夹，首次下载时自动创建。"""
    base = (base_dir or "").strip() or app_dir()
    return os.path.join(base, "Download")


def user_download_dir() -> str:
    """软件目录不可写时的兜底：系统「下载」目录。"""
    for name in ("Downloads", "下载"):
        path = os.path.join(os.path.expanduser("~"), name)
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~")


def _as_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default


@dataclass
class Config:
    download_dir: str = ""                # 空 = 软件目录下的 Download 文件夹
    proxy: str = ""
    proxy_mode: str = "auto"              # auto / proxy / direct（见 smartproxy）
    cookie: str = ""
    dynamic: bool = True
    deep: bool = True
    scroll: bool = True
    dynamic_wait: float = 8.0
    headless: bool = True
    browser_path: str = ""
    min_image_side: int = 0
    max_items: int = 1500
    workers: int = 10
    timeout: float = 15.0
    probe_deadline: float = 35.0     # 元数据探测总时限（秒）
    kinds: list = field(default_factory=lambda: list(KINDS))
    auto_check_all: bool = False
    concurrency: int = 4
    download_segments: int = 4       # 大文件单文件多连接数（1 = 关闭加速）
    user_agent: str = "auto"          # UA 伪装配置 key（见 useragents）
    window_geometry: str = ""         # 上次窗口位置/大小，如 1080x720+100+50
    theme_preset: str = "default"     # 视觉预设（见 theme.PRESETS）
    accent: str = ""                  # 自定义主题色，空 = 跟随预设
    radius_button: int = 10            # 按钮圆角（96 DPI 基准）
    radius_window: int = 0             # 窗口圆角（96 DPI 基准）
    animations: bool = True            # 悬停/主题切换动画
    anim_fps: int = 60                 # 动画帧率（5–120 FPS）
    use_ytdlp: bool = True             # 优先尝试 yt-dlp（若已安装）
    check_updates: bool = True         # 启动时检查 GitHub Release
    skipped_version: str = ""          # 用户选择跳过的版本

    # -------------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {
            "download_dir": self.download_dir,
            "proxy": self.proxy,
            "proxy_mode": self.proxy_mode,
            "cookie": self.cookie,
            "dynamic": self.dynamic,
            "deep": self.deep,
            "scroll": self.scroll,
            "dynamic_wait": self.dynamic_wait,
            "headless": self.headless,
            "browser_path": self.browser_path,
            "min_image_side": self.min_image_side,
            "max_items": self.max_items,
            "workers": self.workers,
            "timeout": self.timeout,
            "probe_deadline": self.probe_deadline,
            "kinds": list(self.kinds),
            "auto_check_all": self.auto_check_all,
            "concurrency": self.concurrency,
            "download_segments": self.download_segments,
            "user_agent": self.user_agent,
            "window_geometry": self.window_geometry,
            "theme_preset": self.theme_preset,
            "accent": self.accent,
            "radius_button": self.radius_button,
            "radius_window": self.radius_window,
            "animations": self.animations,
            "anim_fps": self.anim_fps,
            "use_ytdlp": self.use_ytdlp,
            "check_updates": self.check_updates,
            "skipped_version": self.skipped_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        for key, value in data.items():
            if not hasattr(cfg, key):
                continue
            if key in _INT_RANGES:
                low, high = _INT_RANGES[key]
                number = _as_int(value, _as_int(getattr(cfg, key), low))
                setattr(cfg, key, max(low, min(high, number)))
            elif key in _FLOAT_RANGES:
                low, high = _FLOAT_RANGES[key]
                number = _as_float(value, _as_float(getattr(cfg, key), low))
                setattr(cfg, key, max(low, min(high, number)))
            elif key in _BOOL_FIELDS:
                setattr(cfg, key, _as_bool(value, getattr(cfg, key)))
            elif key in _STR_FIELDS:
                if isinstance(value, str):
                    setattr(cfg, key, value)
            elif key == "kinds":
                if isinstance(value, (list, tuple)):
                    kinds = [str(k) for k in value if str(k) in KINDS]
                    cfg.kinds = kinds or list(KINDS)
            elif key == "proxy_mode":
                if isinstance(value, str):
                    cfg.proxy_mode = value
            else:
                setattr(cfg, key, value)
        if cfg.theme_preset not in THEME_PRESETS:
            cfg.theme_preset = "default"
        accent = (cfg.accent or "").strip()
        if accent:
            value = accent.lstrip("#")
            if not (len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value)):
                accent = ""
            else:
                accent = "#" + value.lower()
        cfg.accent = accent
        # 旧配置没有 proxy_mode，或手改成了非法值：一律回到智能模式
        if cfg.proxy_mode not in ("auto", "proxy", "direct"):
            cfg.proxy_mode = "auto"
        return cfg


def config_path(base_dir: str) -> str:
    return os.path.join(base_dir, "config.json")


def load(base_dir: str) -> Config:
    path = config_path(base_dir)
    cfg = Config()
    try:
        # utf-8-sig：兼容记事本等编辑器可能写入的 BOM
        with open(path, "r", encoding="utf-8-sig") as fh:
            cfg = Config.from_dict(json.load(fh))
    except (OSError, ValueError):
        cfg = Config()
    # 旧配置没有 download_dir（或为空）时，落到软件目录下的 Download
    if not (cfg.download_dir or "").strip():
        cfg.download_dir = default_download_dir(base_dir)
    return cfg


def save(cfg: Config, base_dir: str) -> bool:
    """原子写入：先写 .tmp 再替换，避免写一半留下损坏的配置。"""
    path = config_path(base_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
