"""液态玻璃的真实渲染（Pillow）。

Tk 画不出“背景模糊 + 半透明”的玻璃：靠叠色块永远像塑料片。这里改用
Pillow 生成位图：

* ``wallpaper_image``  – 主题壁纸（竖向渐变 + 柔光斑 + 轻微噪点），大图缓存；
* ``frosted_panel``    – 从壁纸上裁一块、模糊、按比例混白、切圆角、给亮边，
  得到真正的磨砂玻璃面板（能透出后面的颜色，只是被糊掉了）；
* ``lens_image``       – 圆形的玻璃球，把后面的壁纸放大 + 模糊，做凸透镜观感。

Pillow 是可选依赖（requirements.txt 里列了，打包时会带上）；没有 Pillow 时
``available()`` 返回 False，调用方回退到原来的矢量画法。
"""

from __future__ import annotations

import math
import os
import time

from . import theme

try:  # pragma: no cover - 取决于运行环境
    from PIL import Image, ImageDraw, ImageFilter, ImageTk

    _PIL = True
except Exception:  # noqa: BLE001
    Image = None  # type: ignore[assignment]
    _PIL = False

_CACHE: dict[tuple, object] = {}
_CACHE_LIMIT = 48

# 磨砂面板图片四周留出的阴影边距（绘制时按这个值往外偏）
PAD = 10
_WALLPAPERS: dict[tuple, object] = {}


def available() -> bool:
    return _PIL


def clear_cache() -> None:
    _CACHE.clear()
    _WALLPAPERS.clear()


def _rgb(color: str) -> tuple[int, int, int]:
    value = (color or "").lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except (ValueError, IndexError):
        return (255, 255, 255)


def _remember(key: tuple, image):
    if len(_CACHE) > _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = image
    return image


def _blob_mask(size: int, power: float = 1.7):
    """径向渐隐的圆形遮罩（比椭圆叠色干净得多）。"""
    key = ("mask", size, round(power, 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    small = 96
    img = Image.new("L", (small, small), 0)
    draw = ImageDraw.Draw(img)
    center = (small - 1) / 2
    for step in range(small // 2, 0, -1):
        t = 1.0 - step / (small / 2)
        value = int(255 * (1.0 - t) ** power)
        draw.ellipse((center - step, center - step,
                      center + step, center + step), fill=value)
    img = img.resize((size, size), Image.LANCZOS)
    return _remember(key, img)


def wallpaper_image(width: int, height: int, spec: dict):
    """当前主题的壁纸位图（按尺寸缓存）。

    为了拖动窗口时不卡，这里始终**按一半分辨率**渲染再放大：壁纸本来就是
    一堆模糊的渐变和光斑，放大后看不出差别，但开销降到四分之一。
    """
    if not _PIL:
        return None
    # 尺寸按 64 对齐：拖动窗口时不会每变 1px 就重算一张大图
    width = max(64, int(math.ceil(width / 64.0)) * 64)
    height = max(64, int(math.ceil(height / 64.0)) * 64)
    key = (width, height, id(spec), spec.get("top"), spec.get("base"),
           spec.get("bottom"), len(spec.get("blobs", ())))
    cached = _WALLPAPERS.get(key)
    if cached is not None:
        return cached
    sw = max(160, width // 2)
    sh = max(120, height // 2)
    aspect = width / max(1.0, height)
    # 竖向三段渐变：采 48 个控制点，再拉伸成整幅（比逐行取色快一个数量级）
    steps = 48
    column = Image.new("RGB", (1, steps))
    pixels = column.load()
    for i in range(steps):
        v = i / (steps - 1)
        pixels[0, i] = _rgb(theme.wallpaper_color_at(spec, 0.5, v, aspect,
                                                     blobs=False))
    img = column.resize((sw, sh), Image.BILINEAR)
    # 柔光斑
    for buoy, buv, radius, color, strength in spec.get("blobs", ()):
        size = max(16, int(radius * sw * 2))
        mask = _blob_mask(size)
        layer = Image.new("RGB", (size, size), _rgb(color))
        pos = (int(buoy * sw - size / 2), int(buv * sh - size / 2))
        alpha = mask.point(lambda value, s=strength: int(value * s * 1.6))
        try:
            img.paste(layer, pos, alpha)
        except ValueError:
            continue
    img = img.filter(ImageFilter.GaussianBlur(max(2.0, sw / 120.0)))
    try:
        # 细颗粒：玻璃把它糊掉，边缘又能看出被压扁的纹理
        grain = Image.effect_noise((sw, sh), 12).convert("L")
        grain = Image.merge("RGB", (grain, grain, grain))
        img = Image.blend(img, grain, 0.035)
    except Exception:  # noqa: BLE001
        pass
    img = img.resize((width, height), Image.BILINEAR)
    if len(_WALLPAPERS) > 6:
        _WALLPAPERS.clear()
    _WALLPAPERS[key] = img
    return img


def window_slice(width: int, height: int, offset: tuple[int, int] = (0, 0),
                 win: tuple[int, int] = (0, 0), spec: dict = None,
                 fade_bottom: float = 0.0, fade_color: str = "#ffffff"):
    """取窗口壁纸在某个矩形范围内的切片（给列表/搜索区当背景）。

    ``fade_bottom`` 让底部一段淡向 ``fade_color``，用在紧挨内容区的地方，
    避免壁纸和下面的纯色面板之间出现一条硬边。
    """
    if not _PIL:
        return None
    spec = spec or theme.wallpaper_spec()
    win_w, win_h = (win if win and win[0] > 8 else
                    (max(width, 1), max(height, 1)))
    wall = wallpaper_image(win_w, win_h, spec)
    if wall is None:
        return None
    img = _crop(wall, int(offset[0]), int(offset[1]), int(width), int(height))
    if img is None or fade_bottom <= 0:
        return img
    img = img.convert("RGB")
    w, h = img.size
    fade_h = max(2, int(h * min(1.0, fade_bottom)))
    mask = Image.new("L", (w, h), 0)
    ramp = Image.linear_gradient("L").resize((1, fade_h))
    flipped = ramp.transpose(Image.FLIP_TOP_BOTTOM)
    mask.paste(flipped, (0, h - fade_h))
    overlay = Image.new("RGB", (w, h), _rgb(fade_color))
    return Image.composite(overlay, img, mask)


def _rounded_mask(width: int, height: int, radius: int):
    key = ("round", width, height, radius)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius,
                           fill=255)
    return _remember(key, img)


def _crop(wall, x: int, y: int, width: int, height: int):
    """从壁纸上取一块；越界时用边缘像素补齐（不会露黑）。"""
    if wall is None:
        return None
    wall_w, wall_h = wall.size
    width = max(1, min(width, wall_w))
    height = max(1, min(height, wall_h))
    x = max(0, min(x, max(0, wall_w - width)))
    y = max(0, min(y, max(0, wall_h - height)))
    return wall.crop((x, y, x + width, y + height))


def frosted_panel(width: int, height: int, radius: int, spec: dict,
                  offset: tuple[int, int] = (0, 0),
                  win: tuple[int, int] = (0, 0),
                  white: float = 0.26, blur: float = None,
                  rim: float = 0.85, shadow: bool = True,
                  accent: tuple = ()):
    """一块磨砂玻璃：模糊的壁纸 + 混白 + 圆角 + 亮边 + 底部阴影。

    ``white`` 是往白色里掺多少（0 基本透明、1 纯白）；0.3 左右能明显看到
    后面的颜色，又足够让深色文字保持对比。
    """
    if not _PIL:
        return None
    width, height = max(4, int(width)), max(4, int(height))
    radius = max(0, min(int(radius), width // 2, height // 2))
    blur = (max(2.0, min(width, height) / 26.0) if blur is None else blur)
    ox, oy = offset
    win_w, win_h = (win if win and win[0] > 8 else (width * 3, height * 3))
    key = ("frost", width, height, radius, int(ox) // 24, int(oy) // 24,
           round(white, 2), int(blur), round(rim, 2), bool(shadow),
           accent, id(spec), win_w, win_h)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    wall = wallpaper_image(win_w, win_h, spec)
    crop = _crop(wall, int(ox), int(oy), width, height)
    if crop is None:
        return None
    img = crop.filter(ImageFilter.GaussianBlur(blur))
    img = Image.blend(img, Image.new("RGB", (width, height), (255, 255, 255)),
                      max(0.0, min(1.0, white)))
    if accent:
        color, weight = accent
        img = Image.blend(img, Image.new("RGB", (width, height), _rgb(color)),
                          max(0.0, min(1.0, weight)))
    body = img.convert("RGBA")
    # 边缘折射：靠边一圈换成"外面一点"的颜色，像厚玻璃把背景压弯
    band = max(5, min(width, height) // 9)
    if band * 2 < min(width, height):
        ring = Image.new("L", (width, height), 0)
        rdraw = ImageDraw.Draw(ring)
        rdraw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius,
                                fill=255)
        rdraw.rounded_rectangle((band, band, width - 1 - band,
                                 height - 1 - band),
                                radius=max(1, radius - band), fill=0)
        shifted = _crop(wall, int(ox) + band, int(oy) + band, width, height)
        if shifted is not None:
            shifted = shifted.filter(ImageFilter.GaussianBlur(blur * 0.7))
            shifted = Image.blend(
                shifted, Image.new("RGB", (width, height), (255, 255, 255)),
                max(0.0, min(1.0, white * 0.8)))
            ring = ring.point(lambda v: int(v * 0.62))
            body.paste(shifted.convert("RGBA"), (0, 0), ring)
    # 圆角遮罩
    mask = _rounded_mask(width, height, radius)
    body.putalpha(mask)
    # 内侧一圈淡淡的冷色 + 顶部亮线，做出玻璃的厚度
    inner = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    idraw = ImageDraw.Draw(inner)
    idraw.rounded_rectangle((1, 1, width - 2, height - 2), radius=radius,
                            outline=(118, 140, 172, 44), width=2)
    body = Image.alpha_composite(body, inner)
    # 亮边（贴在圆角内侧）
    edge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    edraw = ImageDraw.Draw(edge)
    edraw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius,
                            outline=(255, 255, 255, int(235 * rim)), width=2)
    edraw.line((radius, 1, width - radius, 1),
               fill=(255, 255, 255, int(245 * rim)), width=1)
    body = Image.alpha_composite(body, edge)
    if not shadow:
        return _remember(key, body)
    pad = 10
    canvas = Image.new("RGBA", (width + pad * 2, height + pad * 2),
                       (0, 0, 0, 0))
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=radius, fill=(58, 78, 108, 84))
    shade = shade.filter(ImageFilter.GaussianBlur(7))
    canvas.alpha_composite(shade, (pad, pad + 3))
    canvas.alpha_composite(body, (pad, pad))
    return _remember(key, canvas)


def average_color(image) -> str:
    """一张玻璃图主体区域的平均色（忽略四周阴影留白）。

    玻璃面板上要嵌实心控件（Label / Frame），Tk 没有透明背景，只能把这层
    实心底色取成玻璃的“主色”，视觉上就接不上缝。
    """
    if image is None or not _PIL:
        return "#ffffff"
    width, height = image.size
    pad = min(12, max(1, min(width, height) // 8))
    box = (pad, pad, max(pad + 1, width - pad), max(pad + 1, height - pad))
    region = image.crop(box)
    if region.mode == "RGBA":
        base = Image.new("RGB", region.size, (255, 255, 255))
        base.paste(region, (0, 0), region)
        region = base
    small = region.convert("RGB").resize((1, 1), Image.BOX)
    red, green, blue = small.getpixel((0, 0))
    return "#%02x%02x%02x" % (red, green, blue)


_BUSY_UNTIL = 0.0


def set_busy(seconds: float = 0.28) -> None:
    """标记“正在拖动/缩放窗口”。

    缩放时 Configure 会连发几十次，每次都算一张磨砂玻璃会直接卡死；
    这段时间内先用纯色顶住，等停了再画真玻璃。
    """
    global _BUSY_UNTIL
    _BUSY_UNTIL = time.monotonic() + max(0.0, seconds)


def busy() -> bool:
    return time.monotonic() < _BUSY_UNTIL


_PHOTOS = {}


def photo(image, key):
    """把 PIL 图转成 PhotoImage 并跨控件复用（同一块壁纸只转一次）。"""
    if not _PIL or image is None:
        return None
    cached = _PHOTOS.get(key)
    if cached is not None:
        return cached
    try:
        tk_image = ImageTk.PhotoImage(image)
    except Exception:  # noqa: BLE001
        return None
    if len(_PHOTOS) > 40:
        _PHOTOS.clear()
    _PHOTOS[key] = tk_image
    return tk_image


def to_photo(image, key=None):
    """把 PIL 图转成 Tk 可用的 PhotoImage（必须在主线程调用）。"""
    if key is not None:
        return photo(image, key)
    if not _PIL or image is None:
        return None
    try:
        return ImageTk.PhotoImage(image)
    except Exception:  # noqa: BLE001
        return None


def lens_image(size: int, spec: dict, offset: tuple[int, int] = (0, 0),
               win: tuple[int, int] = (0, 0), zoom: float = 1.35):
    """圆形玻璃球：把后面的壁纸放大、模糊，再套一个亮边和镜面高光。"""
    if not _PIL:
        return None
    size = max(12, int(size))
    key = ("lens", size, int(offset[0]) // 24, int(offset[1]) // 24,
           round(zoom, 2), win[0], win[1], id(spec))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    win_w, win_h = (win if win and win[0] > 8 else (size * 4, size * 4))
    wall = wallpaper_image(win_w, win_h, spec)
    if wall is None:
        return None
    ox, oy = offset
    inner = int(size / max(1.0, zoom))
    crop = _crop(wall, int(ox + (size - inner) / 2),
                 int(oy + (size - inner) / 2), inner, inner)
    if crop is None:
        return None
    body = crop.resize((size, size), Image.LANCZOS)
    body = body.filter(ImageFilter.GaussianBlur(max(1.0, size / 40.0)))
    body = Image.blend(body, Image.new("RGB", (size, size), (255, 255, 255)),
                       0.22)
    rgba = body.convert("RGBA")
    # 边缘加一圈更亮的折射环
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(ring)
    rdraw.ellipse((1, 1, size - 2, size - 2),
                  outline=(255, 255, 255, 210), width=max(2, size // 40))
    rdraw.ellipse((size * 0.10, size * 0.10, size * 0.90, size * 0.90),
                  outline=(255, 255, 255, 60), width=1)
    rgba = Image.alpha_composite(rgba, ring)
    # 左上镜面高光 + 右下焦散
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((size * 0.16, size * 0.12, size * 0.44, size * 0.28),
                  fill=(255, 255, 255, 190))
    gdraw.ellipse((size * 0.20, size * 0.15, size * 0.34, size * 0.22),
                  fill=(255, 255, 255, 240))
    gdraw.ellipse((size * 0.66, size * 0.70, size * 0.84, size * 0.80),
                  fill=(255, 246, 220, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1.0, size / 60.0)))
    rgba = Image.alpha_composite(rgba, glow)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(rgba, (0, 0), mask)
    return _remember(key, out)
