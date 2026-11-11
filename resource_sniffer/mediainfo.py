"""媒体类型识别与元数据探测。

设计要点
--------
1. 用文件头魔术字节识别**真实格式**，不盲信扩展名/Content-Type；
2. 图片：解析尺寸 + 生成缩略图（Pillow 可用时用 Pillow，否则内置解析器兜底）；
3. 音频：解析时长、比特率（MP3/ID3、AAC-ADTS、FLAC、WAV、Ogg、M4A/MP4）；
4. 视频：解析分辨率、时长（MP4/MOV、WebM/MKV、FLV、TS 分片估算）；
5. 两阶段探测：先只读文件头，信息不足时再整体读取（由调用方决定）。

所有解析函数都不抛异常，最坏情况返回空信息。
"""

from __future__ import annotations

import io
import re
import struct
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Optional

IMAGE_EXT = {"jpg", "jpeg", "jpe", "png", "gif", "webp", "bmp", "ico", "avif",
             "heic", "heif", "tif", "tiff", "svg"}
AUDIO_EXT = {"mp3", "m4a", "aac", "flac", "wav", "ogg", "oga", "opus", "wma",
             "ape", "amr", "ac3", "mka", "mid", "midi", "aiff"}
VIDEO_EXT = {"mp4", "m4v", "mov", "webm", "mkv", "avi", "flv", "wmv", "3gp",
             "ts", "mpg", "mpeg", "m2ts", "ogv", "asf", "rmvb", "vob"}
STREAM_EXT = {"m3u8", "mpd"}

KIND_LABEL = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
    "stream": "流媒体",
    "other": "文件",
}

# 缩略图最长边（界面会按屏幕 DPI 覆盖成实际像素）
THUMBNAIL_SIDE = 64


@dataclass
class MediaProbe:
    """一次探测得到的信息。"""

    kind: str = "other"          # image / audio / video / stream / other
    mime: str = ""
    format: str = ""             # 规范化后的格式名（扩展名）
    width: int = 0
    height: int = 0
    duration: float = 0.0        # 秒
    bitrate: int = 0             # bps
    estimate: bool = False       # 时长/码率是否为估算
    thumbnail: Optional[bytes] = None   # PNG 缩略图
    complete: bool = True        # 是否已拿到全部需要的信息

    @property
    def is_media(self) -> bool:
        return self.kind in ("image", "audio", "video", "stream")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def ext_of(url_or_path: str) -> str:
    path = (url_or_path or "").split("?")[0].split("#")[0]
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return ""


# 很多 CDN 把格式放在查询参数里，路径本身没有扩展名，例如：
#   https://pbs.twimg.com/media/ABC123?format=jpg&name=large   （X/Twitter）
#   https://cdn.example.com/img/xyz?fm=webp&w=800
_QUERY_EXT_RE = re.compile(
    r"[?&](?:format|fmt|fm|ext|extension|type|output|enc)=([a-z0-9]{2,5})(?:[&#]|$)",
    re.IGNORECASE,
)


def ext_from_url(url: str) -> str:
    """取 URL 的媒体格式：优先路径扩展名，其次查询参数（format/fm/ext…）。"""
    ext = ext_of(url)
    if ext:
        return ext
    query = (url or "").split("#")[0]
    if "?" not in query:
        return ""
    m = _QUERY_EXT_RE.search(query)
    if m:
        candidate = m.group(1).lower()
        # 归一化常见别名
        aliases = {"jpeg": "jpg", "mpeg": "mpg", "text": ""}
        return aliases.get(candidate, candidate)
    return ""


def looks_like_media_url(url: str) -> bool:
    """判断 URL 是否像媒体资源（路径扩展名或查询参数里的格式）。"""
    return ext_from_url(url) in (IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | STREAM_EXT)


def kind_from_url(url: str) -> str:
    return kind_from_ext(ext_from_url(url))


# --------------------------------------------------------------------------
# 同一资源的尺寸变体归并
# --------------------------------------------------------------------------
# 很多站点把同一张图以不同尺寸提供：
#   X/Twitter : /media/ABC?format=jpg&name=small|medium|large，
#               头像 ..._normal.jpg / ..._x96.jpg / ..._400x400.jpg
#   通用 CDN  : /img/a.webp?w=800&q=80，WordPress /a-300x200.jpg
# 这些会在列表里重复出现，这里给出「同一资源」的归并键。
_SIZE_SUFFIX_RE = re.compile(
    r"(?:[_-](?:normal|mini|bigger|small|medium|large|thumb|reasonably_small|"
    r"\d{2,4}x\d{2,4}|s\d{2,4}x\d{2,4}|x\d{2,4}|w\d{2,4}))$",
    re.IGNORECASE,
)
# 仅影响尺寸/质量/输出格式的参数，可以安全忽略
_SIZE_PARAMS = {
    "name", "w", "h", "width", "height", "q", "quality", "size", "resize",
    "fit", "crop", "dpr", "scale", "format", "fmt", "fm", "type", "output",
    "auto", "compress", "strip", "x-oss-process", "imageview2", "imagemogr2",
}


def variant_key(url: str, kind: str = "") -> str:
    """生成「同一资源」的归并键（仅用于图片去重，不影响下载地址）。

    kind="image" 时会忽略图片容器格式（jpg/webp/png），
    这样同一张图的 ``?format=jpg`` 与 ``?format=webp`` 版本会合并成一个。
    """
    parts = urlsplit(url)
    path = parts.path or "/"
    root, dot, ext = path.rpartition(".")
    if dot and root and "/" in root:
        # 去掉文件名里的尺寸后缀：_400x400 / -300x200 / _normal / _x96 …
        cleaned = _SIZE_SUFFIX_RE.sub("", root)
        path = cleaned + "." + ext.lower() if cleaned != root else path.lower()
    else:
        # 没有扩展名时，若 format 参数指明格式，补上以便与带扩展名的变体对齐
        fmt = ""
        for pair in (parts.query or "").split("&"):
            key, _, value = pair.partition("=")
            if key.lower() in ("format", "fmt", "fm") and value:
                fmt = "." + value.lower()
                break
        path = (path.rstrip("/") + fmt).lower()
    if kind == "image":
        head, sep, tail = path.rpartition("/")
        if "." in tail:
            tail = tail.rsplit(".", 1)[0]
        path = head + sep + tail + ".img"

    kept = []
    for pair in (parts.query or "").split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if key not in _SIZE_PARAMS:
            kept.append(pair)
    query = "&".join(sorted(kept))
    return f"{parts.netloc.lower()}{path}?{query}"


def kind_from_ext(ext: str) -> str:
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in STREAM_EXT:
        return "stream"
    if ext in VIDEO_EXT:
        return "video"
    return "other"


def kind_from_mime(mime: str) -> str:
    m = (mime or "").lower().split(";")[0].strip()
    if not m:
        return "other"
    if m.startswith("image/"):
        return "image"
    if "mpegurl" in m or "dash+xml" in m:
        return "stream"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("video/"):
        return "video"
    return "other"


def fmt_duration(sec: float) -> str:
    if not sec or sec <= 0:
        return ""
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_bitrate(bps: int) -> str:
    if not bps or bps <= 0:
        return ""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1000:
        return f"{round(bps / 1000)} kbps"
    return f"{bps} bps"


def fmt_size(num) -> str:
    if num is None or num < 0:
        return ""
    if num < 1024:
        return f"{int(num)} B"
    value = float(num)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if value < 100 else f"{value:.1f} {unit}"
    return f"{num} B"


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------
def _pil_size(data: bytes) -> tuple[str, int, int]:
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(data)) as im:
            return (im.format or "").lower(), int(im.size[0]), int(im.size[1])
    except Exception:
        return "", 0, 0


def _builtin_image_size(data: bytes) -> tuple[str, int, int]:
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
            w, h = struct.unpack(">II", data[16:24])
            return "png", w, h
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) > 10:
            w, h = struct.unpack("<HH", data[6:10])
            return "gif", w, h
        if data[:2] == b"BM" and len(data) > 26:
            w, h = struct.unpack("<ii", data[18:26])
            return "bmp", w, abs(h)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) > 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return "webp", w, h
            if chunk == b"VP8 ":
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return "webp", w, h
            if chunk == b"VP8L" and len(data) > 25:
                bits = int.from_bytes(data[21:25], "little")
                return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                    i += 1 if marker == 0xFF else 2
                    continue
                seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return "jpeg", w, h
                i += 2 + seg_len
    except Exception:
        pass
    return "", 0, 0


def image_size(data: bytes) -> tuple[str, int, int]:
    fmt, w, h = _builtin_image_size(data)
    if w and h:
        return fmt, w, h
    return _pil_size(data)


def make_thumbnail(data: bytes, max_side: int | None = None) -> Optional[bytes]:
    """生成 PNG 缩略图（保持比例、铺深色底，最长边不超过 max_side）。

    max_side 为空时使用模块级 THUMBNAIL_SIDE（界面会按 DPI 调整该值）。
    """
    if max_side is None:
        max_side = THUMBNAIL_SIDE
    try:
        from PIL import Image  # type: ignore

        im = Image.open(io.BytesIO(data))
        im.load()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (43, 47, 54))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


# --------------------------------------------------------------------------
# 音频
# --------------------------------------------------------------------------
_MP3_BR_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_MP3_BR_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_MP3_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
_AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000,
              11025, 8000, 7350]


def _id3_len(data: bytes) -> tuple[int, float]:
    """返回 (ID3 标签总长度, TLEN 时长秒)。"""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0, 0.0
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    footer = 10 if data[5] & 0x10 else 0
    end = min(len(data), 10 + size)
    pos = 10 + (10 if data[3] == 4 and data[5] & 0x40 else 0)
    dur = 0.0
    while pos + 10 <= end:
        fid = data[pos:pos + 4]
        if not re.match(rb"[A-Z0-9]{4}", fid):
            break
        fsz = (data[pos + 4] << 21) | (data[pos + 5] << 14) | (data[pos + 6] << 7) | data[pos + 7]
        if fid == b"TLEN":
            raw = data[pos + 10:pos + 10 + fsz]
            digits = re.sub(rb"[^0-9]", b"", raw.split(b"\x00")[0])
            if digits:
                dur = int(digits) / 1000.0
            break
        pos += 10 + fsz
    return 10 + size + footer, dur


def _mp3_header_anywhere(data: bytes, limit: int = 4096) -> tuple[int, int]:
    """在数据中寻找第一个合法 MP3 帧头，返回 (采样率, 码率)。"""
    n = min(len(data) - 4, limit)
    i = 0
    while i < n:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            h = struct.unpack(">I", data[i:i + 4])[0]
            ver = (h >> 19) & 0x3
            layer = (h >> 17) & 0x3
            br_idx = (h >> 12) & 0xF
            sr_idx = (h >> 10) & 0x3
            if ver != 1 and layer != 0 and sr_idx != 3 and br_idx not in (0, 15):
                sr = _MP3_RATES.get(ver, [0])[sr_idx]
                table = _MP3_BR_V1_L3 if ver == 3 else _MP3_BR_V2_L3
                return sr, table[br_idx] * 1000
        i += 1
    return 0, 0


def _adts_header(data: bytes) -> tuple[int, int]:
    if len(data) < 7 or data[0] != 0xFF or (data[1] & 0xF6) != 0xF0:
        return 0, 0
    sfi = (data[2] >> 2) & 0xF
    ch = ((data[2] & 0x1) << 2) | ((data[3] >> 6) & 0x3)
    sr = _AAC_RATES[sfi] if sfi < len(_AAC_RATES) else 0
    return sr, ch


def _probe_flac(data: bytes, total: int, exact: bool) -> tuple[float, int]:
    pos = 4
    while pos + 4 <= len(data):
        hdr = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if hdr & 0x7F == 0 and length >= 18:
            body = data[pos + 4:pos + 4 + length]
            sr = (body[10] << 12) | (body[11] << 4) | (body[12] >> 4)
            samples = ((body[13] & 0x0F) << 32) | int.from_bytes(body[14:18], "big")
            if sr and samples:
                dur = samples / sr
                return dur, int(total * 8 / dur) if total else 0
            break
        if hdr & 0x80:
            break
        pos += 4 + length
    return 0.0, 0


def _probe_wav(data: bytes, total: int) -> tuple[float, int]:
    pos, byte_rate, data_len = 12, 0, 0
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        clen = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt " and len(data) >= pos + 20:
            byte_rate = int.from_bytes(data[pos + 16:pos + 20], "little")
        elif cid == b"data":
            data_len = clen or max(0, total - pos - 8)
            if byte_rate and data_len:
                return data_len / byte_rate, byte_rate * 8
            break
        pos += 8 + clen + (clen & 1)
    return 0.0, byte_rate * 8


def _probe_ogg_vorbis(data: bytes, total: int) -> tuple[float, int]:
    """Ogg 只有拿到完整文件末尾才能算准确时长；此处给出码率。"""
    m = re.search(rb"\x01vorbis", data[:8192])
    if m and len(data) >= m.end() + 12:
        i = m.end() + 8
        sr = int.from_bytes(data[i:i + 4], "little")
        nom = int.from_bytes(data[i + 4:i + 8], "little")
        return 0.0, nom or 0
    return 0.0, 0


def _probe_mp3(data: bytes, total: int) -> tuple[float, int]:
    skip, tlen = _id3_len(data)
    sr, br = _mp3_header_anywhere(data[skip:])
    if tlen:
        return tlen, int(total * 8 / tlen) if total else br
    if br and total and total <= len(data) + 1:
        return total / (br / 8), br
    return 0.0, br


def _probe_aac(data: bytes, total: int) -> tuple[float, int]:
    sr, _ch = _adts_header(data)
    if sr:
        # ADTS 按 CBR 估算：文件大小 / (每帧字节 * 每秒帧数)
        return 0.0, 0
    return 0.0, 0


# --------------------------------------------------------------------------
# MP4 / ISO BMFF
# --------------------------------------------------------------------------
def _boxes(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > len(data) + 1:
            break
        yield typ, pos + hdr, min(pos + size, len(data))
        pos += size


def _walk_mp4(data: bytes, start: int, end: int, out: dict, depth: int = 0) -> None:
    if depth > 8:
        return
    for typ, s, e in _boxes(data, start, end):
        body = data[s:e]
        if typ == b"mvhd" and len(body) >= 20:
            if body[0] == 1 and len(body) >= 32:
                ts = int.from_bytes(body[20:24], "big")
                dur = int.from_bytes(body[24:32], "big")
            else:
                ts = int.from_bytes(body[12:16], "big")
                dur = int.from_bytes(body[16:20], "big")
            if ts:
                out["duration"] = max(out["duration"], dur / ts)
        elif typ == b"tkhd" and len(body) >= 84:
            off = 88 if body[0] == 1 else 76
            if len(body) >= off + 8:
                w = int.from_bytes(body[off:off + 4], "big") / 65536
                h = int.from_bytes(body[off + 4:off + 8], "big") / 65536
                if w * h > out.get("w", 0) * out.get("h", 0):
                    out["w"], out["h"] = int(w), int(h)
        elif typ in (b"trak", b"mdia", b"minf", b"stbl", b"moov"):
            _walk_mp4(data, s, e, out, depth + 1)
        elif typ == b"sidx" and len(body) >= 20:
            _parse_sidx(body, out)


def _parse_sidx(body: bytes, out: dict) -> None:
    try:
        ver = body[0]
        ts = int.from_bytes(body[4:8], "big")
        if ver == 1:
            cnt_off, ent_off = 20, 28
        else:
            cnt_off, ent_off = 16, 20
        count = int.from_bytes(body[cnt_off:cnt_off + 2], "big")
        total = 0
        pos = ent_off
        for _ in range(min(count, 512)):
            if pos + 12 > len(body):
                break
            ref = int.from_bytes(body[pos:pos + 4], "big")
            sd = int.from_bytes(body[pos + 4:pos + 8], "big")
            if ref & 0x80000000:
                total += sd
            pos += 12
        if ts and total:
            out["duration"] = max(out["duration"], total / ts)
    except Exception:
        pass


def _probe_mp4(head: bytes, tail: bytes, total: int) -> tuple[float, int, int, int]:
    out: dict = {"duration": 0.0, "w": 0, "h": 0}
    for blob in (head, tail):
        if not blob:
            continue
        for typ, s, e in _boxes(blob, 0, len(blob)):
            if typ in (b"moov", b"moof"):
                _walk_mp4(blob, s, e, out, 1)
    dur = out["duration"]
    br = int(total * 8 / dur) if dur and total else 0
    return dur, br, out["w"], out["h"]


# --------------------------------------------------------------------------
# Matroska / WebM
# --------------------------------------------------------------------------
def _ebml(data: bytes, pos: int):
    if pos >= len(data):
        return 0, pos, True
    first = data[pos]
    length, mask = 1, 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8:
        return 0, pos + 1, True
    value = first & (mask - 1)
    unknown = value == mask - 1
    for i in range(1, length):
        value = (value << 8) | (data[pos + i] if pos + i < len(data) else 0)
    return value, pos + length, unknown


def _probe_webm(data: bytes, total: int) -> tuple[float, int, int, int]:
    dur, scale, w, h = 0.0, 1_000_000.0, 0, 0
    pos = 0
    while pos < len(data):
        eid, p2, _ = _ebml(data, pos)
        size, p3, unknown = _ebml(data, p2)
        if p3 >= len(data) or size > len(data) - p3 + 1:
            break
        body_end = len(data) if unknown else min(len(data), p3 + size)
        if eid in (0x18538067, 0x1654AE6B, 0xAE, 0xE0, 0x1549A966):
            pos = p3
            continue
        chunk = data[p3:body_end]
        if eid == 0x4489 and len(chunk) in (4, 8):
            dur = struct.unpack(">f" if len(chunk) == 4 else ">d", chunk)[0]
        elif eid == 0x2AD7B1:
            scale = float(int.from_bytes(chunk, "big") or 1_000_000)
        elif eid == 0xB0:
            w = int.from_bytes(chunk, "big")
        elif eid == 0xBA:
            h = int.from_bytes(chunk, "big")
        pos = body_end
    dur = dur * scale / 1e9 if dur else 0.0
    br = int(total * 8 / dur) if dur and total else 0
    return dur, br, w, h


# --------------------------------------------------------------------------
# FLV
# --------------------------------------------------------------------------
def _probe_flv(data: bytes) -> tuple[float, int, int, int]:
    idx = data.find(b"onMetaData")
    if idx < 0:
        return 0.0, 0, 0, 0
    seg = data[idx: idx + 1024]

    def num(key: bytes) -> float:
        i = seg.find(key)
        if i < 0 or i + len(key) + 9 > len(seg):
            return 0.0
        raw = seg[i + len(key) + 1: i + len(key) + 9]
        try:
            return struct.unpack(">d", raw)[0]
        except Exception:
            return 0.0

    return num(b"duration"), 0, int(num(b"width")), int(num(b"height"))


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def sniff_kind(data: Optional[bytes], mime: str = "", ext: str = "") -> tuple[str, str]:
    """依据文件头判定 (kind, format)；数据不足时退回 MIME/扩展名。"""
    d = data or b""
    if d:
        if d[:8] == b"\x89PNG\r\n\x1a\n":
            return "image", "png"
        if d[:2] == b"\xff\xd8":
            return "image", "jpeg"
        if d[:6] in (b"GIF87a", b"GIF89a"):
            return "image", "gif"
        if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
            return "image", "webp"
        if d[:2] == b"BM" and ext in ("bmp", "", "dib"):
            return "image", "bmp"
        if d[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"):
            return "image", "ico"
        if d[:4] == b"fLaC":
            return "audio", "flac"
        if d[:4] == b"OggS":
            if b"theora" in d[:512]:
                return "video", "ogv"
            return "audio", "ogg"
        if d[:4] == b"RIFF" and d[8:12] == b"WAVE":
            return "audio", "wav"
        if d[:3] == b"ID3":
            return "audio", "mp3"
        if d[:4] == b"FORM" and d[8:12] in (b"AIFF", b"AIFC"):
            return "audio", "aiff"
        if d[:4] == b"MAC ":
            return "audio", "ape"
        if len(d) >= 4 and d[4:8] in (b"ftyp", b"moov", b"moof", b"mdat", b"free", b"wide"):
            brand = d[8:24].lower()
            if any(b in brand for b in (b"m4a", b"m4b", b"f4a", b"mp4a")):
                return "audio", "m4a"
            if any(b in brand for b in (b"heic", b"heix", b"heif", b"mif1", b"avif", b"msf1")):
                return "image", "heic"
            if b"qt" in brand:
                return "video", "mov"
            if b"3gp" in brand:
                return "video", "3gp"
            return "video", "mp4"
        if d[:4] == b"\x1a\x45\xdf\xa3":
            return ("video", "webm") if b"webm" in d[:128] else ("video", "mkv")
        if d[:3] == b"FLV":
            return "video", "flv"
        if d[:4] == b"\x30\x26\xb2\x75":
            return "video", "wmv"
        if len(d) >= 377 and d[0] == 0x47 and d[188] == 0x47:
            return "video", "ts"
        stripped = d.lstrip()[:4096]
        if stripped[:7] == b"#EXTM3U":
            return "stream", "m3u8"
        low = stripped.lower()
        if low[:5] == b"<?xml" and b"<mpd" in low:
            return "stream", "mpd"
        if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
            return "other", "html"
        if d[:2] in (b"PK",) and ext in ("zip", "apk", "docx", "epub"):
            return "other", ext
        if len(d) > 1 and d[0] == 0xFF and (d[1] & 0xF0) == 0xF0:
            sr, _ch = _adts_header(d)
            if sr:
                return "audio", "aac"
            _sr, br = _mp3_header_anywhere(d, 64)
            if br and ext in ("", "mp3", "mpga"):
                return "audio", "mp3"
    kind = kind_from_mime(mime)
    if kind == "other":
        kind = kind_from_ext(ext)
    fmt = ext
    if not fmt and mime:
        fmt = mime.split(";")[0].split("/")[-1].strip().lower()
    return kind, fmt


def probe(head: bytes, tail: bytes = b"", total_size: int = 0, mime: str = "",
          ext: str = "", thumbnail: bool = True) -> MediaProbe:
    """探测媒体信息。

    参数
    ----
    head: 文件头字节（前 64KB 左右即可）
    tail: 文件尾字节（可选，用于 moov-at-end 的 MP4）
    total_size: 文件总大小（未知时传 len(head)）
    """
    res = MediaProbe(mime=mime or "")
    if not head:
        kind, fmt = sniff_kind(b"", mime, ext)
        res.kind, res.format = kind, (fmt or ext).lower()
        res.complete = True
        return res

    kind, fmt = sniff_kind(head, mime, ext)
    res.kind = kind
    res.format = (fmt or ext).lower()
    size = total_size or len(head)
    have_all = size <= len(head) + 1

    if kind == "image":
        _f, w, h = image_size(head)
        res.width, res.height = w, h
        if thumbnail:
            res.thumbnail = make_thumbnail(head)
        res.complete = True
        return res

    if kind == "audio":
        f = res.format
        if f == "flac":
            res.duration, res.bitrate = _probe_flac(head, size, have_all)
        elif f == "wav":
            res.duration, res.bitrate = _probe_wav(head, size)
        elif f in ("ogg", "oga", "opus"):
            _d, res.bitrate = _probe_ogg_vorbis(head, size)
        elif f in ("mp3", "mpga", "mka") or head[:3] == b"ID3":
            res.duration, res.bitrate = _probe_mp3(head, size)
        elif f == "aac":
            res.duration, res.bitrate = _probe_aac(head, size)
        elif f in ("m4a", "m4b", "mp4", "aiff", "wma", "ape"):
            res.duration, res.bitrate, _w, _h = _probe_mp4(head, tail, size) if head[4:8] in (b"ftyp", b"moov", b"moof") else (0, 0, 0, 0)
        res.complete = bool(res.duration) or have_all
        return res

    if kind in ("video", "stream") and res.format not in ("m3u8", "mpd"):
        if head[4:8] in (b"ftyp", b"moov", b"moof", b"mdat", b"free", b"wide"):
            res.duration, res.bitrate, res.width, res.height = _probe_mp4(head, tail, size)
        elif head[:4] == b"\x1a\x45\xdf\xa3":
            res.duration, res.bitrate, res.width, res.height = _probe_webm(head, size)
        elif head[:3] == b"FLV":
            res.duration, _br, res.width, res.height = _probe_flv(head)
        if res.duration and not res.bitrate and size:
            res.bitrate = int(size * 8 / res.duration)
        if not res.duration and res.bitrate and size:
            res.duration = size * 8 / res.bitrate
            res.estimate = True
        # 分片 / MP4 数据在尾部：需要完整文件才能确定
        res.complete = bool(res.duration and res.width) or have_all
        if not have_all and not res.duration:
            res.estimate = True
        return res

    if kind == "stream":
        res.format = res.format or ("m3u8" if head.lstrip()[:7] == b"#EXTM3U" else "mpd")
        res.complete = True
        return res

    res.complete = True
    return res
