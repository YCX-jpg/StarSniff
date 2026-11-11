"""HTML 解析与媒体地址提取（不依赖第三方解析库）。

HTML 用「正则切标签 + 属性解析」的方式处理：对于嗅探这种「尽量多抓」的场景，
正则的容错性反而比严格解析器更好（能处理残缺标签），而且不会因为页面里有
奇怪的脚本就整页解析失败。CSS 则用 url(...) 和 @import 抽取。
"""

from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from .mediainfo import (AUDIO_EXT, IMAGE_EXT, STREAM_EXT, VIDEO_EXT,
                        ext_of, looks_like_media_url)

_URL_RE = re.compile(
    r"""(?:https?:)?//[^\s"'<>()\\]+?\.(?:jpg|jpeg|png|gif|webp|bmp|svg|ico|avif|
         mp3|m4a|aac|flac|wav|ogg|oga|opus|mp4|m4v|mov|webm|mkv|flv|ts|m3u8|mpd|3gp)
         (?:\?[^\s"'<>()\\]*)?\b""",
    re.IGNORECASE | re.VERBOSE,
)
# 脚本/JSON 里的相对路径直链，如 "/files/a.mp4" 或 "media/a.mp3"
_RELATIVE_MEDIA_RE = re.compile(
    r"""["']((?:\.{0,2}/)?[^"'<>\s\\]{0,180}?\.(?:jpg|jpeg|png|gif|webp|bmp|svg|
         ico|avif|mp3|m4a|aac|flac|wav|ogg|oga|opus|mp4|m4v|mov|webm|mkv|flv|
         m3u8|mpd|3gp))(?:[?#][^"'<>\s\\]*)?["']""",
    re.IGNORECASE | re.VERBOSE,
)
_SCHEME_URL_RE = re.compile(r"https?://[^\s\"'<>()\\]+", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^)'"]+)\1\s*\)""", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?\s*['"]?([^'")]+)""", re.IGNORECASE)

_MEDIA_ATTRS = ("src", "href", "data-src", "data-original", "data-lazy-src",
                "data-lazy", "data-url", "data-video", "data-file", "data-hi-res-src",
                "data-full-src", "data-echo", "poster", "content", "data-href")

_ALL_MEDIA_EXT = IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | STREAM_EXT


class _AttrParser(HTMLParser):
    """收集所有标签的 (attrs, srcset) 以及页面标题。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[dict] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        self.tags.append({"tag": tag, "attrs": {k.lower(): (v or "") for k, v in attrs}})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


def _clean_url(raw: str, base: str) -> str:
    if not raw:
        return ""
    value = html_mod.unescape(raw).strip().strip("\"'")
    if not value or value.startswith(("data:", "javascript:", "mailto:", "tel:",
                                      "about:", "blob:", "#")):
        return ""
    if value.startswith("//"):
        scheme = urlsplit(base).scheme or "https"
        return f"{scheme}:{value}"
    try:
        return urljoin(base, value)
    except ValueError:
        return ""


def _looks_like_media(url: str) -> bool:
    """路径扩展名或查询参数（?format=jpg 之类）像媒体即可。"""
    return looks_like_media_url(url)


def parse_srcset(value: str, base: str) -> list[str]:
    out = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        candidate = part.split()[0]
        url = _clean_url(candidate, base)
        if url:
            out.append(url)
    return out


def extract_from_html(html: str, base_url: str,
                      want_all_links: bool = False) -> dict:
    """从 HTML 中提取媒体地址。

    返回 {"images": [...], "audio": [...], "video": [...], "streams": [...],
          "css": [...], "scripts": [...], "all": [...], "title": str}
    """
    result = {
        "images": [], "audio": [], "video": [], "streams": [],
        "css": [], "scripts": [], "other": [], "title": "",
    }
    seen: set[str] = set()

    def add(url: str, bucket: str | None = None) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        ext = ext_of(url)
        if bucket is None:
            if ext in IMAGE_EXT:
                bucket = "images"
            elif ext in AUDIO_EXT:
                bucket = "audio"
            elif ext in STREAM_EXT:
                bucket = "streams"
            elif ext in VIDEO_EXT:
                bucket = "video"
            else:
                bucket = "other"
        if url not in result.setdefault(bucket, []):
            result[bucket].append(url)

    parser = _AttrParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    result["title"] = (parser.title or "").strip()

    for item in parser.tags:
        tag, attrs = item["tag"], item["attrs"]
        # srcset
        if attrs.get("srcset"):
            for url in parse_srcset(attrs["srcset"], base_url):
                add(url)
        if attrs.get("data-srcset"):
            for url in parse_srcset(attrs["data-srcset"], base_url):
                add(url)
        # 常规属性
        for name in _MEDIA_ATTRS:
            raw = attrs.get(name)
            if not raw:
                continue
            url = _clean_url(raw, base_url)
            if not url:
                continue
            if name in ("content",):
                # <meta property="og:video" content="...">  只取媒体
                prop = (attrs.get("property") or attrs.get("name") or "").lower()
                if tag == "meta" and ("video" in prop or "audio" in prop
                                      or "image" in prop):
                    add(url)
                continue
            add(url)
        # <link rel=stylesheet|icon>
        if tag == "link":
            rel = (attrs.get("rel") or "").lower()
            href = _clean_url(attrs.get("href") or "", base_url)
            if href and "stylesheet" in rel:
                result["css"].append(href)
            elif href and "icon" in rel:
                add(href, "images")
        if tag == "script" and attrs.get("src"):
            url = _clean_url(attrs["src"], base_url)
            if url:
                result["scripts"].append(url)
        if tag in ("a", "iframe", "embed", "object") and want_all_links:
            href = attrs.get("href") or attrs.get("data") or attrs.get("src")
            url = _clean_url(href or "", base_url)
            if url and _looks_like_media(url):
                add(url)
        if tag == "meta":
            prop = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content") or ""
            if prop in ("og:image", "og:image:url", "twitter:image") and content:
                add(_clean_url(content, base_url))
            elif prop in ("og:video", "og:video:url", "og:video:secure_url",
                          "og:audio", "og:audio:url", "twitter:player:stream"):
                add(_clean_url(content, base_url))

    # 内联脚本 / 内联样式里的 URL
    for url in _find_urls_in_text(html, base_url):
        add(url)
    return result


def _find_urls_in_text(text: str, base_url: str) -> list[str]:
    """从脚本/样式中抽取媒体直链（含转义过的 \\/ 写法和相对路径）。"""
    found: list[str] = []
    if not text:
        return found
    normalized = (text.replace("\\/", "/").replace("\\u002F", "/")
                  .replace("\\u002f", "/"))
    for match in _URL_RE.finditer(normalized):
        raw = match.group(0)
        if raw.startswith("//"):
            raw = (urlsplit(base_url).scheme or "https") + ":" + raw
        found.append(raw)
    for match in _RELATIVE_MEDIA_RE.finditer(normalized):
        candidate = match.group(1).strip()
        if candidate and not candidate.startswith(("data:", "#", "//")):
            url = _clean_url(candidate, base_url)
            if url:
                found.append(url)
    for match in _CSS_URL_RE.finditer(normalized):
        candidate = match.group(2).strip()
        if candidate and not candidate.startswith(("data:", "#")):
            found.append(_clean_url(candidate, base_url))
    for match in _CSS_IMPORT_RE.finditer(normalized):
        found.append(_clean_url(match.group(1), base_url))
    return [f for f in found if f]


def extract_from_css(css: str, base_url: str) -> dict:
    """从 CSS 里提取图片/字体等资源地址。"""
    out = {"images": [], "streams": [], "other": [], "imports": []}
    text = (css or "").replace("\\/", "/")
    for match in _CSS_URL_RE.finditer(text):
        url = _clean_url(match.group(2), base_url)
        if not url:
            continue
        ext = ext_of(url)
        if ext in IMAGE_EXT:
            out["images"].append(url)
        elif ext in ("woff", "woff2", "ttf", "otf", "eot"):
            out["other"].append(url)
        elif ext in _ALL_MEDIA_EXT:
            out["streams"].append(url)
    for match in _CSS_IMPORT_RE.finditer(text):
        url = _clean_url(match.group(1), base_url)
        if url:
            out["imports"].append(url)
    return out


def extract_json_media(text: str, base_url: str) -> list[str]:
    """从 JSON 接口响应里找媒体直链。"""
    urls = _find_urls_in_text(text, base_url)
    out = []
    for url in urls:
        if _looks_like_media(url):
            out.append(url)
    return out


def guess_filename(url: str, content_disposition: str = "") -> str:
    """从 URL / Content-Disposition 推断文件名。"""
    if content_disposition:
        for pattern in (r"filename\*=UTF-8''([^;]+)", r'filename="?([^";]+)"?'):
            match = re.search(pattern, content_disposition, re.IGNORECASE)
            if match:
                try:
                    name = unquote(match.group(1))
                except Exception:
                    name = match.group(1)
                name = re.split(r"[\\/]", name)[-1].strip()
                if name:
                    return name
    parts = urlsplit(url)
    raw = parts.path.rstrip("/").rsplit("/", 1)[-1]
    name = unquote(raw)
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    if not name:
        name = "unnamed"
    if "." not in name:
        ext = ext_of(url)
        if ext:
            name += "." + ext
    return name[:180]


_SAFE_EXT_FALLBACK = {
    "image": ".jpg", "audio": ".mp3", "video": ".mp4",
    "stream": ".m3u8", "other": ".bin",
}


def ensure_extension(name: str, kind: str, fmt: str = "") -> str:
    """保证文件名带正确扩展名。"""
    if "." in name.rsplit("/", 1)[-1] and ext_of(name):
        return name
    ext = fmt or (kind and _SAFE_EXT_FALLBACK.get(kind, ""))
    if not ext:
        return name
    return f"{name}.{ext.lstrip('.')}"
