"""界面配色、字体、圆角与 DPI 缩放。

支持多套视觉预设（默认 / 亚克力 / 液态玻璃 / 复古 Win2000 / 霓虹），
``apply_style()`` 会把预设写进模块级常量，所有自绘控件读取这些常量，
因此在切换主题后重建界面即可整体换肤（含自绘标题栏）。
"""

from __future__ import annotations

import math
import os

# 当前生效的样式（由 apply_style 写入）
BG = "#0f1216"
PANEL = "#151a21"
PANEL_ALT = "#181e26"
CARD = "#1f2630"
CARD_HOVER = "#29323e"
BORDER = "#2a3340"
BORDER_LIGHT = "#3a4657"
FG = "#eef2f8"
FG_DIM = "#a2adbd"
FG_FAINT = "#6f7b8c"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9dff"
ACCENT_DIM = "#3568cf"
SUCCESS = "#34d399"
WARN = "#fbbf24"
ERROR = "#f87171"
SELECTED_ROW = "#182a44"
ROW_HOVER = "#1b222c"
SEGMENT_BG = "#1b222c"
SEGMENT_ACTIVE = "#22304a"
CHIP_BG = "#1b2431"
THUMB_BG = "#0b0f14"
TITLE_BG = BG
TITLE_FG = FG
TITLE_SUB = FG_FAINT

# 固定光照模型（所有玻璃控件共用，避免每个组件各画一套光）：
# * 主光来自左上：(-0.55, -0.83)，顶边/左上角最亮；
# * 环境光从顶部进入：上明下暗；
# * 地面反光从下方进入：底部折射带；
# * 右下背光：暗边 + 投影。
LIGHT_KEY_DIR = (-0.55, -0.83)
LIGHT_TOP = "#ffffff"
LIGHT_GROUND = "#ffffff"
LIGHT_SHADOW = "#000000"

KIND_COLORS = {
    "image": "#38bdf8",
    "audio": "#a78bfa",
    "video": "#f472b6",
    "stream": "#fbbf24",
    "other": "#94a3b8",
}

# 视觉预设 ---------------------------------------------------------------
_DEFAULT_COLORS = {
    "BG": "#0f1216", "PANEL": "#151a21", "PANEL_ALT": "#181e26",
    "CARD": "#1f2630", "CARD_HOVER": "#29323e", "BORDER": "#2a3340",
    "BORDER_LIGHT": "#3a4657", "FG": "#eef2f8", "FG_DIM": "#a2adbd",
    "FG_FAINT": "#6f7b8c", "SUCCESS": "#34d399", "WARN": "#fbbf24",
    "ERROR": "#f87171", "SELECTED_ROW": "#182a44", "ROW_HOVER": "#1b222c",
    "SEGMENT_BG": "#1b222c", "SEGMENT_ACTIVE": "#22304a",
    "CHIP_BG": "#1b2431", "THUMB_BG": "#0b0f14",
}

PRESETS = {
    "default": {
        "label": "默认",
        "description": "现代深色，克制的蓝灰层次",
        "colors": dict(_DEFAULT_COLORS),
        "accent": "#4f8cff",
        "title_bg": "#0f1216", "title_fg": "#eef2f8",
        "title_sub": "#6f7b8c",
        "font": "Microsoft YaHei UI", "base": 10, "h1": 16,
        "kind_colors": {
            "image": "#38bdf8", "audio": "#a78bfa",
            "video": "#f472b6", "stream": "#fbbf24", "other": "#94a3b8",
        },
        "button_radius": 10, "window_radius": 0,
    },
    "acrylic": {
        "label": "亚克力",
        "description": "深蓝雾面，层叠的半透明质感",
        "colors": {
            "BG": "#0b1520", "PANEL": "#102030", "PANEL_ALT": "#152838",
            "CARD": "#1a3044", "CARD_HOVER": "#23405a", "BORDER": "#294660",
            "BORDER_LIGHT": "#3d6485", "FG": "#eaf4ff", "FG_DIM": "#a4bdd6",
            "FG_FAINT": "#6f8da8", "SUCCESS": "#4ade80", "WARN": "#fbbf24",
            "ERROR": "#fb7185", "SELECTED_ROW": "#123b5a", "ROW_HOVER": "#162a3b",
            "SEGMENT_BG": "#132433", "SEGMENT_ACTIVE": "#1c405e",
            "CHIP_BG": "#14293a", "THUMB_BG": "#071019",
        },
        "accent": "#38bdf8",
        "title_bg": "#0b1520", "title_fg": "#eaf4ff",
        "title_sub": "#6f8da8",
        "font": "Microsoft YaHei UI", "base": 10, "h1": 16,
        "kind_colors": {
            "image": "#67e8f9", "audio": "#c4b5fd",
            "video": "#f9a8d4", "stream": "#fcd34d", "other": "#94a3b8",
        },
        "button_radius": 14, "window_radius": 18,
    },
    "glass": {
        "label": "液态玻璃",
        "description": "浅色通透，壁纸从玻璃里透出来",
        "colors": {
            "BG": "#eef4fd", "PANEL": "#e7eefa", "PANEL_ALT": "#f7faff",
            "CARD": "#fbfdff", "CARD_HOVER": "#eff5ff", "BORDER": "#d7e2f2",
            "BORDER_LIGHT": "#ffffff", "FG": "#1c2836", "FG_DIM": "#52627a",
            "FG_FAINT": "#8695aa", "SUCCESS": "#0e9a6a", "WARN": "#a9761a",
            "ERROR": "#c8455a", "SELECTED_ROW": "#dae7ff", "ROW_HOVER": "#eef5ff",
            "SEGMENT_BG": "#e1eaf8", "SEGMENT_ACTIVE": "#ffffff",
            "CHIP_BG": "#edf3fd", "THUMB_BG": "#e5edf9",
        },
        "accent": "#3f7ce0",
        "title_bg": "#f6faff", "title_fg": "#17212f",
        "title_sub": "#6c7d94",
        "font": "Microsoft YaHei UI", "base": 10, "h1": 17,
        "kind_colors": {
            "image": "#1f78c8", "audio": "#7856d2",
            "video": "#bf3479", "stream": "#a46c0c", "other": "#5b6a7f",
        },
        "button_radius": 20, "window_radius": 24,
    },
    "neon": {
        "label": "霓虹",
        "description": "夜色微光，紫青柔和不刺眼",
        "colors": {
            "BG": "#120d1d", "PANEL": "#1a1329", "PANEL_ALT": "#201830",
            "CARD": "#271d3b", "CARD_HOVER": "#33264c", "BORDER": "#3d2c5e",
            "BORDER_LIGHT": "#5f4691", "FG": "#f3ecff", "FG_DIM": "#bfa9de",
            "FG_FAINT": "#8b78a9", "SUCCESS": "#5eead4", "WARN": "#fbbf24",
            "ERROR": "#fb7185", "SELECTED_ROW": "#2f1a54", "ROW_HOVER": "#221733",
            "SEGMENT_BG": "#1c1330", "SEGMENT_ACTIVE": "#37205f",
            "CHIP_BG": "#221636", "THUMB_BG": "#0d0916",
        },
        "accent": "#b98cf5",
        "title_bg": "#120d1d", "title_fg": "#f3ecff",
        "title_sub": "#8b78a9",
        "font": "Microsoft YaHei UI", "base": 10, "h1": 17,
        "kind_colors": {
            "image": "#63d7e8", "audio": "#d8b4fe",
            "video": "#f472b6", "stream": "#facc15", "other": "#a5b4c3",
        },
        "button_radius": 16, "window_radius": 12,
    },
    "retro": {
        "label": "复古 2000",
        "description": "Windows 2000 灰阶与蓝色标题栏",
        "colors": {
            "BG": "#d4d0c8", "PANEL": "#d4d0c8", "PANEL_ALT": "#d9d6cf",
            "CARD": "#ffffff", "CARD_HOVER": "#e9e6de", "BORDER": "#808080",
            "BORDER_LIGHT": "#404040", "FG": "#000000", "FG_DIM": "#333333",
            "FG_FAINT": "#666666", "SUCCESS": "#006600", "WARN": "#8a5a00",
            "ERROR": "#bb0000", "SELECTED_ROW": "#c1d2ee", "ROW_HOVER": "#e8e5dc",
            "SEGMENT_BG": "#bdb9b2", "SEGMENT_ACTIVE": "#ffffff",
            "CHIP_BG": "#ece9d8", "THUMB_BG": "#ffffff",
        },
        "accent": "#0a246a",
        "title_bg": "#0a246a", "title_fg": "#ffffff",
        "title_sub": "#c8d4f0",
        "font": "Tahoma", "base": 9, "h1": 13,
        "kind_colors": {
            "image": "#006080", "audio": "#6a0dad",
            "video": "#a00040", "stream": "#806000", "other": "#404040",
        },
        "button_radius": 0, "window_radius": 0,
    },
}

PRESET_KEYS = tuple(PRESETS)
PRESET_NAMES = {key: preset["label"] for key, preset in PRESETS.items()}

# 每套主题不只是换色：壳层、标题栏、地址栏、筛选栏、底栏和列表排布都不同。
# 数值均以 96 DPI 为基准，由 ``px()`` 换算；新增主题时只需在这里加一项。
LAYOUTS = {
    "default": {
        "shell": "standard", "header": "bar", "header_height": 54,
        "title_align": "left", "show_subtitle": True, "brand": "full",
        "search": "inline", "search_height": 40, "search_pad": 18,
        "start_width": 100, "start_height": 40, "start_style": "rounded",
        "filters": "segmented", "filter_align": "left", "filter_in_sidebar": False,
        "footer": "bar", "list": "rows", "row_height": 80, "thumb": 68,
        "content_pad": 18, "card_gap": 0, "motion": "fade",
        "traffic_lights": False,
    },
    "acrylic": {
        "shell": "sidebar", "header": "bar", "header_height": 52,
        "title_align": "left", "show_subtitle": True, "brand": "full",
        "search": "inline", "search_height": 40, "search_pad": 16,
        "start_width": 96, "start_height": 40, "start_style": "rounded",
        "filters": "sidebar", "filter_align": "left", "filter_in_sidebar": True,
        "footer": "bar", "list": "rows", "row_height": 82, "thumb": 68,
        "content_pad": 16, "card_gap": 0, "motion": "fade",
        "traffic_lights": False, "sidebar_width": 208,
    },
    "glass": {
        "shell": "floating", "header": "floating", "header_height": 58,
        "title_align": "center", "show_subtitle": False, "brand": "compact",
        "search": "stacked", "search_height": 48, "search_pad": 26,
        "start_width": 92, "start_height": 48, "start_style": "pill",
        "filters": "capsule", "filter_align": "center", "filter_in_sidebar": False,
        "footer": "floating", "list": "cards", "row_height": 92, "thumb": 72,
        "content_pad": 24, "card_gap": 10, "motion": "spring",
        "traffic_lights": True,
    },
    "neon": {
        "shell": "standard", "header": "minimal", "header_height": 42,
        "title_align": "left", "show_subtitle": False, "brand": "compact",
        "search": "compact", "search_height": 42, "search_pad": 16,
        "start_width": 94, "start_height": 42, "start_style": "rounded",
        "filters": "chips", "filter_align": "left", "filter_in_sidebar": False,
        "footer": "dock", "list": "rows", "row_height": 78, "thumb": 62,
        "content_pad": 16, "card_gap": 0, "motion": "glow",
        "traffic_lights": False,
    },
    "retro": {
        "shell": "standard", "header": "menu", "header_height": 30,
        "title_align": "left", "show_subtitle": False, "brand": "full",
        "search": "inline", "search_height": 32, "search_pad": 10,
        "start_width": 86, "start_height": 32, "start_style": "classic",
        "filters": "tabs", "filter_align": "left", "filter_in_sidebar": False,
        "footer": "status", "list": "flat", "row_height": 72, "thumb": 58,
        "content_pad": 10, "card_gap": 0, "motion": "none",
        "traffic_lights": False,
    },
}

for _key, _preset in PRESETS.items():
    _preset["layout"] = LAYOUTS[_key]

ACCENTS = (
    ("蓝", "#4f8cff"),
    ("青", "#22d3ee"),
    ("紫", "#c084fc"),
    ("粉", "#f472b6"),
    ("绿", "#34d399"),
    ("橙", "#fb923c"),
    ("红", "#f87171"),
    ("灰", "#94a3b8"),
)

# 壁纸 -----------------------------------------------------------------
# 每套主题配一张自绘壁纸：上/中/下三段渐变 + 几团柔和的彩色光斑。
# u / v 是相对窗口的比例坐标，r 是光斑半径（相对窗口宽度），
# strength 是光斑中心最浓的混色比例——Tk 没有真正的透明度，
# 光斑用一圈圈同心椭圆模拟径向渐隐。
WALLPAPERS = {
    "default": {
        "top": "#18222e", "base": "#0f151c", "bottom": "#0a0f14",
        "blobs": (
            (0.14, 0.08, 0.58, "#1d4553", 0.50),
            (0.86, 0.20, 0.50, "#242c55", 0.42),
            (0.62, 0.92, 0.60, "#123039", 0.38),
        ),
    },
    "acrylic": {
        "top": "#12283c", "base": "#0a1622", "bottom": "#060e17",
        "blobs": (
            (0.12, 0.12, 0.60, "#15526e", 0.55),
            (0.86, 0.26, 0.55, "#1c3d70", 0.48),
            (0.52, 0.94, 0.62, "#0f4a55", 0.34),
        ),
    },
    # 浅色液态玻璃：奶油白 → 天空蓝 → 淡紫，彩色光斑像 iOS 壁纸
    "glass": {
        "top": "#ffede0", "base": "#e7f0ff", "bottom": "#cfdcff",
        "blobs": (
            (0.14, 0.02, 0.55, "#ffc6d6", 0.62),
            (0.84, 0.10, 0.50, "#b9d6ff", 0.62),
            (0.26, 0.80, 0.55, "#cdbcff", 0.54),
            (0.80, 0.72, 0.52, "#ffd0a2", 0.52),
            (0.55, 0.42, 0.46, "#b3e6db", 0.42),
            (0.06, 0.96, 0.50, "#c9b7ff", 0.45),
        ),
    },
    "neon": {
        "top": "#160d28", "base": "#0d0817", "bottom": "#08050f",
        "blobs": (
            (0.14, 0.14, 0.60, "#5c1466", 0.55),
            (0.84, 0.28, 0.55, "#2b1663", 0.50),
            (0.54, 0.92, 0.60, "#0d4d5e", 0.38),
        ),
    },
    "retro": {
        "top": "#2e807b", "base": "#0d6d69", "bottom": "#075450",
        "blobs": (
            (0.18, 0.08, 0.60, "#3f9a93", 0.42),
            (0.82, 0.80, 0.68, "#085f5b", 0.48),
        ),
    },
}

ACTIVE_PRESET = "default"
CLASSIC = False
GLASS = False
ACRYLIC = False
NEON = False
DARK_UI = True
LAYOUT = LAYOUTS["default"]
MOTION = "fade"
WALLPAPER = dict(WALLPAPERS["default"])
RADIUS_BUTTON = 10
RADIUS_WINDOW = 0
ANIMATIONS = True
ANIM_FPS = 60

FONT_FAMILY = "Microsoft YaHei UI"
FONT = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_TINY = (FONT_FAMILY, 8)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_H1 = (FONT_FAMILY, 16, "bold")
FONT_BUTTON = (FONT_FAMILY, 10, "bold")
FONT_MONO = ("Consolas", 9)

# ---- 未缩放时的基准度量（以 96 DPI 为基准） ----
BASE_ROW_HEIGHT = 80
BASE_THUMB_SIZE = 68
BASE_ROW_HEIGHT = int(LAYOUT.get("row_height", BASE_ROW_HEIGHT))
BASE_THUMB_SIZE = int(LAYOUT.get("thumb", BASE_THUMB_SIZE))

SCALE = 1.0
DPI = 96
ROW_HEIGHT = BASE_ROW_HEIGHT
THUMB_SIZE = BASE_THUMB_SIZE


def px(value: float) -> int:
    """把 96 DPI 下的像素值换算成当前缩放下的像素值。"""
    return int(round(value * SCALE))


def blend(color_a: str, color_b: str, weight: float = 0.5) -> str:
    """把 color_a 按 weight 混到 color_b 上，用于浅色徽标/描边。"""
    weight = max(0.0, min(1.0, weight))

    def _rgb(value: str) -> tuple[int, int, int]:
        value = (value or "").lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        if len(value) != 6:
            return (0, 0, 0)
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return (0, 0, 0)

    a = _rgb(color_a)
    b = _rgb(color_b)
    mixed = tuple(int(round(a[i] * weight + b[i] * (1.0 - weight))) for i in range(3))
    return "#%02x%02x%02x" % mixed


def frame_delay() -> int:
    """当前动画帧间隔（毫秒），由设置里的 5–120 FPS 控制。"""
    return max(4, int(round(1000.0 / max(5, min(120, ANIM_FPS)))))


def aurora_color(phase: float, offset: float = 0.0) -> str:
    """在强调色、雾蓝、淡紫、浅粉、暖黄之间循环，模拟环境彩色光。"""
    palette = (ACCENT, "#8ec5ff", "#c3b5ff", "#ffb9d0", "#ffe2ad")
    t = ((phase + offset) % 1.0) * len(palette)
    index = int(t) % len(palette)
    fraction = t - int(t)
    return blend(palette[(index + 1) % len(palette)], palette[index],
                 fraction)


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def luma(color: str) -> float:
    """0（黑）~1（白）的感知亮度，用来判断当前是不是浅色主题。"""
    value = (color or "").lstrip("#")
    try:
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 0.0
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def wallpaper_spec(preset: str = "") -> dict:
    """返回某套主题的壁纸定义（不修改当前全局样式）。"""
    key = preset or ACTIVE_PRESET
    return WALLPAPERS.get(key) or WALLPAPERS["default"]


def wallpaper_color_at(spec: dict, u: float, v: float,
                       aspect: float = 1.7, blobs: bool = True) -> str:
    """取壁纸在 (u, v) 比例位置的颜色；aspect = 窗口宽高比，保证光斑是圆的。

    纯计算，不碰 Canvas——自绘控件可以用它算出“玻璃后面那块壁纸”的颜色，
    再把卡片调成半透明玻璃的样子。
    """
    spec = spec or WALLPAPER
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    if v <= 0.5:
        color = blend(spec["top"], spec["base"], _smoothstep(v * 2.0))
    else:
        color = blend(spec["bottom"], spec["base"],
                      _smoothstep((v - 0.5) * 2.0))
    for buoy, buv, radius, blob_color, strength in spec.get("blobs", ()):
        if not blobs:
            break
        distance = math.hypot((u - buoy) * max(0.3, aspect), v - buv)
        if distance >= radius:
            continue
        falloff = (1.0 - distance / radius)
        color = blend(blob_color, color, strength * falloff ** 1.8)
    return color


def button_radius() -> int:
    return px(RADIUS_BUTTON)


def card_radius() -> int:
    return px(max(6, RADIUS_BUTTON + 2))


def small_radius() -> int:
    return 0 if CLASSIC else px(6)


def window_radius() -> int:
    return px(RADIUS_WINDOW)


def palette_for(preset: str, accent: str = "") -> dict:
    """返回某预设的完整配色（不修改当前全局样式），用于预览。"""
    base = PRESETS.get(preset) or PRESETS["default"]
    data = dict(base)
    data["colors"] = dict(base["colors"])
    chosen = (accent or base.get("accent") or ACCENT).strip()
    if chosen:
        try:
            int(chosen.lstrip("#"), 16)
        except ValueError:
            chosen = base.get("accent") or ACCENT
    data["accent"] = chosen
    data["accent_hover"] = blend(chosen, "#ffffff", 0.18)
    data["accent_dim"] = blend(chosen, "#000000", 0.30)
    data["wallpaper"] = wallpaper_spec(preset)
    return data


def apply_style(preset: str = "default", accent: str = "",
                radius_button: int | None = None,
                radius_window: int | None = None,
                animations: bool = True,
                anim_fps: int = 60) -> None:
    """切换视觉预设（写进模块级常量，调用后需要重建界面）。"""
    global ACTIVE_PRESET, CLASSIC, GLASS, ACRYLIC, NEON, ANIMATIONS, ANIM_FPS
    global DARK_UI, WALLPAPER
    global LAYOUT, MOTION, BASE_ROW_HEIGHT, BASE_THUMB_SIZE, ROW_HEIGHT, THUMB_SIZE
    global BG, PANEL, PANEL_ALT, CARD, CARD_HOVER, BORDER, BORDER_LIGHT
    global FG, FG_DIM, FG_FAINT, ACCENT, ACCENT_HOVER, ACCENT_DIM
    global SUCCESS, WARN, ERROR, SELECTED_ROW, ROW_HOVER
    global SEGMENT_BG, SEGMENT_ACTIVE, CHIP_BG, THUMB_BG
    global TITLE_BG, TITLE_FG, TITLE_SUB, KIND_COLORS
    global FONT_FAMILY, FONT, FONT_SMALL, FONT_TINY, FONT_BOLD, FONT_H1
    global FONT_BUTTON, FONT_MONO
    global RADIUS_BUTTON, RADIUS_WINDOW

    key = preset if preset in PRESETS else "default"
    data = palette_for(key, accent)
    colors = data["colors"]
    ACTIVE_PRESET = key
    CLASSIC = key == "retro"
    GLASS = key in ("glass", "neon")
    NEON = key == "neon"
    ACRYLIC = key == "acrylic"
    WALLPAPER = dict(wallpaper_spec(key))
    DARK_UI = luma(colors["BG"]) < 0.45
    LAYOUT = dict(LAYOUTS.get(key) or LAYOUTS["default"])
    MOTION = str(LAYOUT.get("motion") or "fade")
    BASE_ROW_HEIGHT = int(LAYOUT.get("row_height") or 80)
    BASE_THUMB_SIZE = int(LAYOUT.get("thumb") or 68)
    ROW_HEIGHT = px(BASE_ROW_HEIGHT)
    THUMB_SIZE = px(BASE_THUMB_SIZE)

    BG = colors["BG"]
    PANEL = colors["PANEL"]
    PANEL_ALT = colors["PANEL_ALT"]
    CARD = colors["CARD"]
    CARD_HOVER = colors["CARD_HOVER"]
    BORDER = colors["BORDER"]
    BORDER_LIGHT = colors["BORDER_LIGHT"]
    FG = colors["FG"]
    FG_DIM = colors["FG_DIM"]
    FG_FAINT = colors["FG_FAINT"]
    SUCCESS = colors["SUCCESS"]
    WARN = colors["WARN"]
    ERROR = colors["ERROR"]
    SELECTED_ROW = colors["SELECTED_ROW"]
    ROW_HOVER = colors["ROW_HOVER"]
    SEGMENT_BG = colors["SEGMENT_BG"]
    SEGMENT_ACTIVE = colors["SEGMENT_ACTIVE"]
    CHIP_BG = colors["CHIP_BG"]
    THUMB_BG = colors["THUMB_BG"]
    ACCENT = data["accent"]
    ACCENT_HOVER = data["accent_hover"]
    ACCENT_DIM = data["accent_dim"]
    TITLE_BG = data.get("title_bg", BG)
    TITLE_FG = data.get("title_fg", FG)
    TITLE_SUB = data.get("title_sub", FG_FAINT)
    KIND_COLORS = dict(data.get("kind_colors") or KIND_COLORS)

    family = data.get("font") or "Microsoft YaHei UI"
    base = int(data.get("base") or 10)
    h1 = int(data.get("h1") or 16)
    FONT_FAMILY = family
    FONT = (family, base)
    FONT_SMALL = (family, max(7, base - 1))
    FONT_TINY = (family, max(7, base - 2))
    FONT_BOLD = (family, base, "bold")
    FONT_H1 = (family, h1, "bold")
    FONT_BUTTON = (family, base, "bold")
    FONT_MONO = ("Consolas", max(8, base - 1))

    RADIUS_BUTTON = max(0, min(28, int(radius_button if radius_button is not None
                                     else data.get("button_radius", 10))))
    RADIUS_WINDOW = max(0, min(30, int(radius_window if radius_window is not None
                                     else data.get("window_radius", 0))))
    ANIMATIONS = bool(animations)
    try:
        ANIM_FPS = max(5, min(120, int(anim_fps)))
    except (TypeError, ValueError):
        ANIM_FPS = 60


def enable_dpi_awareness() -> None:
    """在创建 Tk 窗口之前声明进程 DPI 感知，避免系统位图拉伸导致的模糊。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_TIMER_RAISED = False


def begin_timer_resolution() -> None:
    """把 Windows 定时器精度提到 1ms。

    系统默认精度约 15.6ms，此时 ``after(17)`` 实际要等到下一个时间片，
    60 FPS 只能跑出 ~30 FPS，120 FPS 更是不可能。动画期间提高精度，
    退出时恢复。
    """
    global _TIMER_RAISED
    if os.name != "nt" or _TIMER_RAISED:
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
        _TIMER_RAISED = True
    except Exception:
        _TIMER_RAISED = False


def end_timer_resolution() -> None:
    """恢复系统定时器精度（begin_timer_resolution 的配对调用）。"""
    global _TIMER_RAISED
    if not _TIMER_RAISED:
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass
    _TIMER_RAISED = False


def enable_dark_titlebar(window) -> None:
    """让 Windows 标题栏跟随深色主题（Win10 1809+ / Win11）。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            if result == 0:
                break
    except Exception:
        pass


def _system_dpi(root) -> int:
    """取当前窗口所在显示器的 DPI。"""
    env = os.environ.get("RS_UI_SCALE", "").strip()
    if env:
        try:
            return int(round(float(env) * 96))
        except ValueError:
            pass
    if os.name == "nt":
        try:
            import ctypes

            hwnd = root.winfo_id()
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi:
                return int(dpi)
        except Exception:
            pass
        try:
            import ctypes

            hdc = ctypes.windll.user32.GetDC(0)
            try:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            finally:
                ctypes.windll.user32.ReleaseDC(0, hdc)
            if dpi:
                return int(dpi)
        except Exception:
            pass
    try:
        return int(round(root.winfo_fpixels("1i")))
    except Exception:
        return 96


def apply_scale(root, force: float | None = None) -> float:
    """按真实 DPI 调整字号与像素度量，返回缩放系数（1.0 = 100%）。"""
    global SCALE, DPI, ROW_HEIGHT, THUMB_SIZE

    dpi = int(round(force * 96)) if force else _system_dpi(root)
    dpi = max(72, min(dpi, 480))
    SCALE = max(0.75, min(dpi / 96.0, 4.0))
    DPI = dpi

    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass

    ROW_HEIGHT = px(BASE_ROW_HEIGHT)
    THUMB_SIZE = px(BASE_THUMB_SIZE)
    return SCALE


def scale_factor() -> float:
    return SCALE
