"""流媒体清单解析：HLS(.m3u8) 与 MPEG-DASH(.mpd)。

嗅探到的流媒体清单需要再解析一层，才能拿到真正的分辨率 / 码率 / 分片地址，
也才能在下载时把分片合并成一个完整文件。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin


@dataclass
class Variant:
    """一个可独立播放的媒体流（HLS 变体 / DASH Representation）。"""

    url: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    codecs: str = ""
    name: str = ""
    kind: str = "video"          # video / audio
    container: str = "m3u8"      # m3u8 / mpd / mp4 / ts
    audio_group: str = ""
    group_id: str = ""
    frame_rate: float = 0.0
    lang: str = ""


@dataclass
class Playlist:
    """解析后的播放列表。"""

    is_master: bool = False
    variants: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    keys: dict = field(default_factory=dict)        # 分片索引 -> 密钥信息
    init_segment: str = ""
    encrypted: bool = False
    duration: float = 0.0
    segment_type: str = "ts"
    error: str = ""
    has_endlist: bool = False
    media_sequence: int = 0

    @property
    def has_variants(self) -> bool:
        return bool(self.variants)


# --------------------------------------------------------------------------
# HLS
# --------------------------------------------------------------------------
def is_m3u8(text: str, url: str = "") -> bool:
    head = (text or "").lstrip()[:16]
    return head.startswith("#EXTM3U") or url.split("?")[0].lower().endswith(".m3u8")


def _parse_attr_list(line: str) -> dict:
    """解析 #EXT-X-STREAM-INF: 后面的 KEY=VALUE 列表（支持带引号的逗号）。"""
    out: dict[str, str] = {}
    key = ""
    buf = ""
    quoted = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quoted:
            if ch == '"':
                quoted = False
            else:
                buf += ch
        elif ch == '"':
            quoted = True
        elif ch == "=" and not key:
            key, buf = buf.strip(), ""
        elif ch == "," and key:
            out[key.upper()] = buf.strip()
            key, buf = "", ""
        else:
            buf += ch
        i += 1
    if key:
        out[key.upper()] = buf.strip()
    return out


def _parse_iv(raw: str) -> bytes:
    """解析 #EXT-X-KEY 的 IV=0x… （16 字节十六进制）。"""
    text = (raw or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        return b""
    try:
        data = bytes.fromhex(text)
    except ValueError:
        return b""
    if len(data) < 16:
        data = b"\x00" * (16 - len(data)) + data
    return data[:16]


def parse_m3u8(text: str, base_url: str) -> Playlist:
    """解析 HLS 清单；主清单返回 variants，媒体清单返回 segments。"""
    pl = Playlist()
    lines = [ln.strip() for ln in (text or "").splitlines()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        pl.error = "不是有效的 M3U8 清单"
        return pl

    i = 0
    pending_inf: dict = {}
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_inf = _parse_attr_list(line.split(":", 1)[1])
            # 下一行非注释行是变体地址
            j = i + 1
            while j < len(lines) and (not lines[j] or lines[j].startswith("#")):
                j += 1
            if j < len(lines):
                url = urljoin(base_url, lines[j])
                res = pending_inf.get("RESOLUTION", "")
                w = h = 0
                m = re.match(r"(\d+)\s*[xX]\s*(\d+)", res or "")
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
                codecs = pending_inf.get("CODECS", "")
                kind = "audio" if (codecs and "avc1" not in codecs.lower()
                                   and "hvc1" not in codecs.lower()
                                   and "hev1" not in codecs.lower()
                                   and w == 0) else "video"
                pl.variants.append(Variant(
                    url=url,
                    bandwidth=int(pending_inf.get("BANDWIDTH") or 0),
                    width=w, height=h, codecs=codecs,
                    name=pending_inf.get("NAME", "") or pending_inf.get("VIDEO", ""),
                    kind=kind, container="m3u8",
                    audio_group=pending_inf.get("AUDIO", ""),
                    frame_rate=float(pending_inf.get("FRAME-RATE") or 0),
                ))
                i = j + 1
                continue
            i += 1
            continue
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attr_list(line.split(":", 1)[1])
            uri = attrs.get("URI")
            if uri and attrs.get("TYPE", "").upper() == "AUDIO":
                pl.variants.append(Variant(
                    url=urljoin(base_url, uri),
                    name=attrs.get("NAME", "") or attrs.get("LANGUAGE", "音轨"),
                    kind="audio", container="m3u8",
                    group_id=attrs.get("GROUP-ID", ""), lang=attrs.get("LANGUAGE", ""),
                ))
            i += 1
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending_inf["duration"] = float(line.split(":", 1)[1].split(",")[0])
            except ValueError:
                pending_inf["duration"] = 0.0
            i += 1
            continue
        if line.startswith("#EXT-X-KEY:"):
            attrs = _parse_attr_list(line.split(":", 1)[1])
            method = (attrs.get("METHOD") or "NONE").upper()
            if method != "NONE":
                pl.encrypted = True
                pl.keys[len(pl.segments)] = {
                    "method": method,
                    "uri": urljoin(base_url, attrs["URI"]) if attrs.get("URI") else "",
                    "iv": _parse_iv(attrs.get("IV", "")),
                    "keyformat": attrs.get("KEYFORMAT", ""),
                }
            else:
                pl.keys[len(pl.segments)] = {"method": "NONE"}
            i += 1
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                pl.media_sequence = int(line.split(":", 1)[1].strip())
            except ValueError:
                pl.media_sequence = 0
            i += 1
            continue
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attr_list(line.split(":", 1)[1])
            if attrs.get("URI"):
                pl.init_segment = urljoin(base_url, attrs["URI"])
            i += 1
            continue
        if line.startswith("#EXT-X-BYTERANGE:"):
            pending_inf["byterange"] = line.split(":", 1)[1].strip()
            i += 1
            continue
        if line.startswith("#EXT-X-ENDLIST"):
            pl.has_endlist = True
            i += 1
            continue
        if line.startswith("#"):
            i += 1
            continue
        # 普通行 = 分片地址
        seg_url = urljoin(base_url, line)
        pl.segments.append(seg_url)
        if pending_inf.get("duration"):
            pl.duration += pending_inf["duration"]
        pending_inf = {}
        i += 1

    if pl.segments:
        pl.is_master = False
        seg = pl.segments[0].split("?")[0].lower()
        if seg.endswith(".m4s") or seg.endswith(".mp4") or pl.init_segment:
            pl.segment_type = "m4s"
        elif seg.endswith(".aac"):
            pl.segment_type = "aac"
        else:
            pl.segment_type = "ts"
    else:
        pl.is_master = True
    return pl


# --------------------------------------------------------------------------
# MPEG-DASH
# --------------------------------------------------------------------------
def _ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def is_mpd(text: str, url: str = "") -> bool:
    head = (text or "").lstrip()[:400].lower()
    return "<mpd" in head or url.split("?")[0].lower().endswith(".mpd")


def _iso_duration(value: str) -> float:
    """PT1H2M3.5S -> 秒。"""
    if not value:
        return 0.0
    m = re.match(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+(?:\.\d+)?)H)?"
        r"(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?", value.strip())
    if not m:
        return 0.0
    y, mo, d, h, mi, s = m.groups()
    days = (int(y or 0) * 365) + (int(mo or 0) * 30) + int(d or 0)
    return days * 86400 + float(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)


def parse_mpd(text: str, base_url: str) -> Playlist:
    """解析 DASH MPD，把每个 Representation 暴露为一个可下载项。"""
    pl = Playlist()
    try:
        root = ET.fromstring(text.encode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        pl.error = f"MPD 解析失败: {exc}"
        return pl

    pl.is_master = True
    media_pres = _iso_duration(root.get("mediaPresentationDuration", ""))

    for period in [e for e in root.iter() if _ns(e.tag) == "Period"]:
        period_base = base_url
        for child in period:
            if _ns(child.tag) == "BaseURL" and (child.text or "").strip():
                period_base = urljoin(base_url, child.text.strip())
        for aset in [e for e in period if _ns(e.tag) == "AdaptationSet"]:
            aset_base = period_base
            for child in aset:
                if _ns(child.tag) == "BaseURL" and (child.text or "").strip():
                    aset_base = urljoin(aset_base, child.text.strip())
            aset_mime = aset.get("mimeType", "") or aset.get("contentType", "")
            aset_lang = aset.get("lang", "")
            reps = [e for e in aset if _ns(e.tag) == "Representation"]
            for rep in reps:
                rep_base = aset_base
                for child in rep:
                    if _ns(child.tag) == "BaseURL" and (child.text or "").strip():
                        rep_base = urljoin(rep_base, child.text.strip())
                bw = int(rep.get("bandwidth") or aset.get("bandwidth") or 0)
                w = int(rep.get("width") or aset.get("width") or 0)
                h = int(rep.get("height") or aset.get("height") or 0)
                mime = rep.get("mimeType") or aset_mime
                codecs = rep.get("codecs") or aset.get("codecs") or ""
                kind = "audio" if "audio" in mime.lower() else "video"
                # 只有当 Representation 直接指向单个文件时才可独立下载
                direct = ""
                seg_template = next((c for c in rep if _ns(c.tag) == "SegmentTemplate"), None)
                if seg_template is None:
                    seg_template = next((c for c in aset if _ns(c.tag) == "SegmentTemplate"), None)
                media_tpl = ""
                if seg_template is not None:
                    media_tpl = seg_template.get("media", "")
                is_single = (not media_tpl) or ("$Number$" not in media_tpl
                                                and "$Time$" not in media_tpl)
                if is_single:
                    direct = rep_base
                pl.variants.append(Variant(
                    url=direct or rep_base,
                    bandwidth=bw, width=w, height=h, codecs=codecs,
                    name=rep.get("id", "") or mime,
                    kind=kind,
                    container="mpd" if not is_single else (
                        "mp4" if "mp4" in mime else "webm"),
                    group_id=rep.get("id", ""),
                    lang=aset_lang,
                ))
    pl.duration = media_pres
    if not pl.variants:
        pl.error = "MPD 中没有找到可用的 Representation"
    return pl


def parse_manifest(text: str, url: str, content_type: str = "") -> Playlist:
    """根据内容自动选择 HLS / DASH 解析器。"""
    ct = (content_type or "").lower()
    stripped = (text or "").lstrip()
    if is_m3u8(stripped, url) or "mpegurl" in ct:
        return parse_m3u8(stripped, url)
    if is_mpd(stripped, url) or "dash+xml" in ct:
        return parse_mpd(stripped, url)
    if stripped.startswith("<"):
        return parse_mpd(stripped, url)
    return parse_m3u8(stripped, url)


# --------------------------------------------------------------------------
# 展示辅助
# --------------------------------------------------------------------------
def variant_label(v: Variant) -> str:
    """给列表展示用的简短描述，例如 1920x1080 · 4.5 Mbps。"""
    from .mediainfo import fmt_bitrate

    bits = []
    if v.width and v.height:
        bits.append(f"{v.width}x{v.height}")
    if v.bandwidth:
        bits.append(fmt_bitrate(v.bandwidth))
    if v.lang:
        bits.append(v.lang)
    return " · ".join(bits)


def variant_display_name(v: Variant, index: int = 0) -> str:
    if v.width and v.height:
        base = f"{v.height}p"
    elif v.kind == "audio":
        # 多音轨时用 GROUP-ID / NAME 区分，避免列表里一堆同名「音频」
        tag = v.name or v.group_id or v.lang or ""
        base = f"音轨 {tag}" if tag else "音频"
    else:
        base = f"流 {index + 1}"
    if v.bandwidth:
        from .mediainfo import fmt_bitrate

        return f"{base} ({fmt_bitrate(v.bandwidth)})"
    return base


def master_playlist_summary(pl: Playlist) -> str:
    if not pl.variants:
        return ""
    return f"{len(pl.variants)} 个清晰度可选"


def guess_extension(v: Variant) -> str:
    if v.container in ("m3u8", "mpd"):
        return v.container
    if v.container in ("mp4", "webm"):
        return v.container
    return "ts"


def merge_variants(variants: list) -> list:
    """去重（同地址只留一条）。"""
    seen = set()
    out = []
    for v in variants:
        if v.url in seen:
            continue
        seen.add(v.url)
        out.append(v)
    return out


def best_variant(variants: list) -> Optional[Variant]:
    if not variants:
        return None
    return max(variants, key=lambda v: (v.height or 0, v.bandwidth or 0))
