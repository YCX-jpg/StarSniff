"""本地测试站点：生成真实可解析的图片 / 音频 / 视频 / HLS 清单，用于端到端自测。

运行：python tests/site.py 8765
"""

from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import struct
import sys
import threading
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ASSETS = os.path.join(HERE, "_assets")

# 记录最近一次请求的头部，便于自检断言 UA 等请求头
LAST_HEADERS: dict = {}

# HLS AES-128 加密测试用的固定向量（NIST SP 800-38A AES-128-CBC）
ENC_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
ENC_IV = bytes(range(16))
ENC_CIPHERTEXT = bytes.fromhex(
    "7649abac8119b246cee98e9b12e9197d"
    "5086cb9b507219ee95db113a917678b2")
ENC_PLAINTEXT = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51")


# --------------------------------------------------------------------------
# 素材生成
# --------------------------------------------------------------------------
def make_png(path: str, w: int = 320, h: int = 240, color=(59, 130, 246)) -> None:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            r = int(color[0] * (0.4 + 0.6 * x / w))
            g = int(color[1] * (0.4 + 0.6 * y / h))
            b = int(color[2] * (0.5 + 0.5 * (x + y) / (w + h)))
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def make_mp3(path: str, seconds: float = 4.0, bitrate_kbps: int = 128) -> None:
    """生成结构合法（帧头正确、长度符合时长）的 MP3 静音流。"""
    sr = 44100
    bitrate = bitrate_kbps * 1000
    frame_size = int(144 * bitrate / sr)      # 每帧字节数
    samples_per_frame = 1152
    total_frames = int(seconds * sr / samples_per_frame)
    header = bytes([
        0xFF,
        0xFB,                                     # MPEG1 Layer3, no CRC
        (0b00 << 4) | (0b0100 << 0) | (0b00 << 2) | 0b00,  # idx 9 = 128k, 44.1k
        0x00,
    ])
    # 修正 bitrate index
    br_index = int(bitrate_kbps / 8)          # V1L3 表: idx 4=64, 9=128, 11=192
    br_index = {32: 1, 40: 2, 48: 3, 56: 4, 64: 5, 80: 6, 96: 7, 112: 8,
                128: 9, 160: 10, 192: 11, 224: 12, 256: 13, 320: 14}[bitrate_kbps]
    header = bytes([0xFF, 0xFB, (br_index << 4) | (0 << 2) | 0x00, 0x00])
    frame = header + b"\x00" * (frame_size - 4)
    with open(path, "wb") as fh:
        for _ in range(total_frames):
            fh.write(frame)


def make_mp4(path: str, width: int = 1280, height: int = 720,
             seconds: float = 12.0) -> None:
    """生成仅含 ftyp + moov(mvhd/trak/tkhd) + 空 mdat 的 MP4，供元数据解析。"""
    timescale = 1000
    duration = int(seconds * timescale)

    def box(tag: bytes, *payload: bytes) -> bytes:
        body = b"".join(payload)
        return struct.pack(">I", len(body) + 8) + tag + body

    def full(tag: bytes, version: int, flags: int, *payload: bytes) -> bytes:
        return box(tag, struct.pack(">B", version) + struct.pack(">I", flags)[1:],
                   *payload)

    ftyp = box(b"ftyp", b"isom", struct.pack(">I", 512), b"isomiso2avc1mp41")
    mvhd = full(b"mvhd", 0, 0,
                struct.pack(">IIII", 0, 0, timescale, duration),
                struct.pack(">IHH", 0x00010000, 0x0100, 0),
                b"\x00" * 10,
                struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000),
                b"\x00" * 24,
                struct.pack(">I", 2))
    tkhd = full(b"tkhd", 0, 3,
                struct.pack(">IIIII", 0, 0, 0, 0, duration),
                b"\x00" * 8,
                struct.pack(">HHHH", 0, 0, 0, 0),
                struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000),
                struct.pack(">II", width << 16, height << 16))
    mdhd = full(b"mdhd", 0, 0,
                struct.pack(">IIII", 0, 0, timescale, duration),
                struct.pack(">HH", 0x55C4, 0))
    hdlr = full(b"hdlr", 0, 0, struct.pack(">I", 0), b"vide", b"\x00" * 12,
                b"VideoHandler\x00")
    mdia = box(b"mdia", mdhd, hdlr)
    trak = box(b"trak", tkhd, mdia)
    moov = box(b"moov", mvhd, trak)
    mdat = box(b"mdat", b"\x00" * 4096)
    with open(path, "wb") as fh:
        fh.write(ftyp + moov + mdat)


def make_wav(path: str, seconds: float = 3.0, rate: int = 8000) -> None:
    channels, bits = 2, 16
    frame_bytes = channels * bits // 8
    data = b"\x00" * int(seconds * rate) * frame_bytes
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                    rate * frame_bytes, frame_bytes, bits)
    header += b"data" + struct.pack("<I", len(data))
    with open(path, "wb") as fh:
        fh.write(header + data)


def build_assets() -> None:
    os.makedirs(os.path.join(ASSETS, "img"), exist_ok=True)
    os.makedirs(os.path.join(ASSETS, "media"), exist_ok=True)
    make_png(os.path.join(ASSETS, "img", "landscape.png"), 320, 240, (59, 130, 246))
    make_png(os.path.join(ASSETS, "img", "square.png"), 160, 160, (34, 197, 94))
    make_png(os.path.join(ASSETS, "img", "lazy.png"), 240, 180, (244, 114, 182))
    make_png(os.path.join(ASSETS, "img", "icon_16.png"), 16, 16, (148, 163, 184))
    # dynamic.js 里会注入这张图，属于页面自身资源
    make_png(os.path.join(ASSETS, "img", "injected.png"), 200, 150, (250, 204, 21))
    # 无扩展名的直链（用于验证"按内容识别格式"）
    shutil.copyfile(os.path.join(ASSETS, "img", "square.png"),
                    os.path.join(ASSETS, "img", "unknown_binary"))
    make_mp3(os.path.join(ASSETS, "media", "song.mp3"), 4.0, 128)
    make_mp4(os.path.join(ASSETS, "media", "clip.mp4"), 1280, 720, 12.0)
    make_wav(os.path.join(ASSETS, "media", "tone.wav"), 3.0)

    # HLS：主清单 + 3 个清晰度 + 媒体清单 + 分片
    hls = os.path.join(ASSETS, "hls")
    os.makedirs(hls, exist_ok=True)
    for name, (w, h, bw) in {"360p": (640, 360, 800000),
                             "720p": (1280, 720, 2500000),
                             "1080p": (1920, 1080, 5000000)}.items():
        folder = os.path.join(hls, name)
        os.makedirs(folder, exist_ok=True)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4",
                 "#EXT-X-MEDIA-SEQUENCE:0"]
        for i in range(3):
            lines.append("#EXTINF:4.000000,")
            lines.append(f"segment{i}.ts")
            seg = os.path.join(folder, f"segment{i}.ts")
            with open(seg, "wb") as fh:
                fh.write(b"\x47" * (188 * 20))
        lines.append("#EXT-X-ENDLIST")
        with open(os.path.join(folder, "index.m3u8"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    master = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2"',
        "360p/index.m3u8",
        '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"',
        "720p/index.m3u8",
        '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"',
        "1080p/index.m3u8",
    ]
    with open(os.path.join(hls, "master.m3u8"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(master) + "\n")

    # 加密 HLS：用于验证 AES-128-CBC 解密链路
    enc = os.path.join(hls, "enc")
    os.makedirs(enc, exist_ok=True)
    with open(os.path.join(enc, "key.bin"), "wb") as fh:
        fh.write(ENC_KEY)
    # 分片内容 = NIST 测试向量密文；解密后应得到 ENC_PLAINTEXT
    with open(os.path.join(enc, "seg0.ts"), "wb") as fh:
        fh.write(ENC_CIPHERTEXT)
    enc_playlist = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:4",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f'#EXT-X-KEY:METHOD=AES-128,URI="/hls/enc/key.bin",IV=0x{ENC_IV.hex()}',
        "#EXTINF:2.000000,",
        "seg0.ts",
        "#EXT-X-ENDLIST",
    ]
    with open(os.path.join(enc, "index.m3u8"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(enc_playlist) + "\n")


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------
PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>嗅探测试页 · 媒体资源</title>
<link rel="stylesheet" href="/assets/site.css">
<meta property="og:image" content="/assets/img/landscape.png">
</head>
<body>
<h1>测试页面</h1>
<img src="/assets/img/landscape.png" width="320" height="240" alt="landscape">
<img src="/assets/img/square.png" alt="square">
<img src="/assets/img/icon_16.png" alt="tiny icon">
<img data-src="/assets/img/lazy.png" alt="lazy" class="lazy">
<img srcset="/assets/img/square.png 1x, /assets/img/landscape.png 2x" alt="srcset">
<img src="/assets/img/not-exist.png" alt="404">
<div style="background-image:url('/assets/img/lazy.png')">bg</div>
<audio controls src="/assets/media/song.mp3"></audio>
<audio controls><source src="/assets/media/tone.wav" type="audio/wav"></audio>
<video controls poster="/assets/img/square.png">
  <source src="/assets/media/clip.mp4" type="video/mp4">
</video>
<video id="hlsplayer"></video>
<script src="/assets/dynamic.js"></script>
</body>
</html>
"""

CSS = """
body { background: url('/assets/img/square.png'); }
.hero { background-image: url("/assets/img/landscape.png"); }
"""

# 模拟「无路径扩展名 + 多尺寸变体」的站点（X/Twitter 这类）：
# 媒体地址靠 ?format= 指定格式，同一张图有多个尺寸版本
VARIANT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>无扩展名媒体测试</title>
<link rel="preload" as="image"
      imagesrcset="/assets/img/landscape.png?format=png&name=large 1x">
</head>
<body>
<img src="/assets/img/landscape.png?format=png&name=small" alt="小图">
<img src="/assets/img/landscape.png?format=png&name=medium" alt="中图">
<img src="/assets/img/landscape.png?format=png&name=large" alt="大图">
<img src="/assets/img/landscape.png" alt="无参数">
<img src="/assets/img/square.png?format=webp&name=medium" alt="方形">
<video>
  <source src="/assets/media/clip.mp4?tag=29" type="video/mp4">
</video>
</body>
</html>
"""

DYNAMIC_JS = """
window.addEventListener('load', function () {
  setTimeout(function () {
    var img = document.createElement('img');
    img.src = '/assets/img/injected.png';
    document.body.appendChild(img);
  }, 900);
});
"""


SLOW_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>慢资源测试</title></head>
<body>
<img src="/assets/img/landscape.png" alt="快的">
<img src="/slow/a.jpg" alt="慢1">
<img src="/slow/b.jpg" alt="慢2">
<img src="/slow/c.png" alt="慢3">
<video><source src="/slow/d.mp4" type="video/mp4"></video>
</body></html>
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ASSETS, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def translate_path(self, path):
        # 页面里统一用 /assets/... 前缀，实际文件在 _assets 根下
        clean = path.split("?")[0].split("#")[0]
        if clean.startswith("/assets/"):
            path = clean[len("/assets"):]
        return super().translate_path(path)

    def do_GET(self):     # noqa: N802
        LAST_HEADERS.clear()
        LAST_HEADERS.update({k.lower(): v for k, v in self.headers.items()})
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/page.html"):
            self._send_html(PAGE)
            return
        if path == "/variants.html":
            self._send_html(VARIANT_PAGE)
            return
        if path == "/assets/site.css":
            self._send_text(CSS, "text/css")
            return
        if path == "/assets/dynamic.js":
            self._send_text(DYNAMIC_JS, "application/javascript")
            return
        if path == "/api/list.json":
            self._send_text('{"items":[{"url":"/assets/media/song.mp3"}]}',
                            "application/json")
            return
        if path.startswith("/range/"):
            try:
                size = max(0, min(32 << 20, int(path.rsplit("/", 1)[-1])))
            except ValueError:
                size = 0
            if size:
                start, end = 0, size - 1
                range_header = self.headers.get("Range", "")
                status = 200
                if range_header.startswith("bytes="):
                    try:
                        span = range_header.split("=", 1)[1].split(",", 1)[0]
                        first, last = span.split("-", 1)
                        start = int(first or 0)
                        end = int(last) if last else size - 1
                        end = min(end, size - 1)
                        if start <= end:
                            status = 206
                    except Exception:
                        start, end, status = 0, size - 1, 200
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range",
                                     f"bytes {start}-{end}/{size}")
                self.end_headers()
                block = bytes((i * 31 + 7) % 256 for i in range(64 * 1024))
                written = 0
                while written < length:
                    take = min(len(block), length - written)
                    self.wfile.write(block[:take])
                    written += take
                return
        if path.startswith("/slow/"):
            # 模拟被墙/黑洞的站点：迟迟不响应
            import time

            time.sleep(25)
            try:
                self._send_text("too late", "text/plain")
            except Exception:
                pass
            return
        if path == "/slowpage.html":
            self._send_html(SLOW_PAGE)
            return
        if path == "/api/echo":
            # 回显请求头，用于验证 UA 伪装
            import json

            payload = {
                "ua": self.headers.get("User-Agent", ""),
                "sec_ch_ua": self.headers.get("sec-ch-ua", ""),
                "sec_ch_ua_mobile": self.headers.get("sec-ch-ua-mobile", ""),
                "sec_ch_ua_platform": self.headers.get("sec-ch-ua-platform", ""),
                "referer": self.headers.get("Referer", ""),
            }
            self._send_text(json.dumps(payload, ensure_ascii=False),
                            "application/json")
            return
        if path == "/delay":
            # 可控延迟，用于验证智能代理的测速选择
            import time
            from urllib.parse import parse_qs

            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            try:
                delay = max(0.0, min(5.0, float(query.get("ms", ["0"])[0]) / 1000.0))
            except (TypeError, ValueError):
                delay = 0.0
            time.sleep(delay)
            self._send_text("ok", "text/plain")
            return
        return super().do_GET()

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, body: str, ctype: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int = 8765, ready_cb=None) -> ThreadedServer:
    build_assets()
    server = ThreadedServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if ready_cb:
        ready_cb(server)
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = serve(port)
    print(f"测试站点已启动: http://127.0.0.1:{port}/page.html")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
