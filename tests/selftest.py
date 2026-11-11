"""端到端自检：本地起测试站点 → 跑真实嗅探引擎 → 校验结果 / 下载 / 清单 / 主题 / yt-dlp。

用法：python tests/selftest.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from resource_sniffer import mediainfo, streams        # noqa: E402
from resource_sniffer.downloader import DownloadManager, DownloadTask  # noqa: E402
from resource_sniffer.engine import SniffOptions, Sniffer  # noqa: E402
from resource_sniffer.mediainfo import fmt_bitrate, fmt_duration, fmt_size  # noqa: E402
from resource_sniffer.net import HttpSession, NetworkError  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

# main() 加载的测试站点模块（单例），供用例读取服务器端观察到的请求头
SITE = None


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  [{'OK ' if condition else 'FAIL'}] {name}  {detail}")


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_sniff(url: str, browser_factory=None, browser_path: str = "",
              dynamic: bool = False, proxy: str = "", proxy_mode: str = "auto",
              **opts):
    done = threading.Event()
    summary: dict = {}
    items: list = []
    removed: list = []

    def on_item(item):
        items.append(item)

    sniffer = Sniffer(on_item=on_item,
                      on_done=lambda s: (summary.update(s), done.set()),
                      on_remove=removed.append,
                      browser_factory=browser_factory)
    options = SniffOptions(dynamic=dynamic, expand_manifests=True,
                           timeout=10, dynamic_wait=1.5, scroll=False,
                           browser_path=browser_path)
    for key, value in opts.items():
        setattr(options, key, value)
    sniffer.start(url, options, proxy=proxy, proxy_mode=proxy_mode)
    done.wait(120)
    sniffer.join(5)
    # 折叠掉尺寸变体后，这些条目已从列表移除，不应计入结果
    kept = [i for i in items if i not in removed]
    return kept, summary


def test_static() -> list:
    print("\n== 1. 静态嗅探 ==")
    items, summary = run_sniff(f"{BASE}/page.html")
    urls = {i.url for i in items}
    print(f"  共 {len(items)} 条，用时 {summary.get('elapsed', 0):.2f}s")
    for item in items:
        main, sub = item.preview_lines()
        print(f"    [{item.kind:6}] {main:12} {sub:12} {item.filename[:26]:28}"
              f" {fmt_size(item.size)}")
    check("抓到 PNG 图片", any(u.endswith("landscape.png") for u in urls))
    check("CSS 背景图被深度解析", any(u.endswith("lazy.png") for u in urls))
    check("抓到 MP3 音频", any(u.endswith("song.mp3") for u in urls))
    check("抓到 WAV 音频", any(u.endswith("tone.wav") for u in urls))
    check("抓到 MP4 视频", any(u.endswith("clip.mp4") for u in urls))
    check("404 资源被过滤", not any(u.endswith("not-exist.png") for u in urls))

    png = next((i for i in items if i.url.endswith("landscape.png")), None)
    check("图片尺寸正确", bool(png and (png.width, png.height) == (320, 240)),
          f"{png.width}×{png.height}" if png else "")
    check("图片缩略图已生成", bool(png and png.thumbnail),
          f"{len(png.thumbnail)} 字节" if png and png.thumbnail else "")
    check("缩略图可被 Tk 解码",
          _tk_can_decode(png.thumbnail if png else None))

    mp3 = next((i for i in items if i.url.endswith("song.mp3")), None)
    check("音频时长与码率正确",
          bool(mp3 and 3.5 < mp3.duration < 4.5 and 120000 < mp3.bitrate < 136000),
          f"{mp3.duration:.2f}s {fmt_bitrate(mp3.bitrate)}" if mp3 else "")
    mp4 = next((i for i in items if i.url.endswith("clip.mp4")), None)
    check("视频分辨率与时长正确",
          bool(mp4 and (mp4.width, mp4.height) == (1280, 720)
               and 11 < mp4.duration < 13),
          f"{mp4.width}×{mp4.height} {fmt_duration(mp4.duration)}" if mp4 else "")
    wav = next((i for i in items if i.url.endswith("tone.wav")), None)
    check("WAV 时长正确", bool(wav and 2.8 < wav.duration < 3.2),
          f"{wav.duration:.2f}s" if wav else "")
    check("文件名推断正确", bool(png and png.filename == "landscape.png"),
          png.filename if png else "")
    return items


def _tk_can_decode(data) -> bool:
    if not data:
        return False
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        image = tk.PhotoImage(data=data, format="png")
        ok = image.width() > 0
        root.destroy()
        return ok
    except Exception:
        return False


def test_direct():
    print("\n== 2. 直链嗅探 ==")
    items, _ = run_sniff(f"{BASE}/assets/media/song.mp3")
    check("直链识别为音频", len(items) == 1 and items[0].kind == "audio",
          f"{len(items)} 条")
    items, _ = run_sniff(f"{BASE}/assets/img/unknown_binary")
    check("无扩展名直链按内容识别",
          any(i.kind == "image" for i in items),
          str([(i.kind, i.fmt) for i in items]))


def test_manifest() -> list:
    print("\n== 3. HLS 清单展开 ==")
    items, _ = run_sniff(f"{BASE}/hls/master.m3u8")
    variants = [i for i in items if i.source == "manifest"]
    check("主清单展开出 3 个清晰度", len(variants) == 3, f"{len(variants)} 个")
    check("含 1080p 清晰度", any(i.height == 1080 for i in variants))
    check("清晰度带码率信息", all(i.bitrate for i in variants))
    master = next((i for i in items if i.url.endswith("master.m3u8")), None)
    check("主清单标高清晰度数", bool(master and "清晰度" in master.extra),
          master.extra if master else "")
    return items


def test_dynamic():
    print("\n== 4. 动态嗅探（模拟 DevTools，验证 CDP/WebSocket 链路） ==")
    mock = load("mock_cdp", "mock_cdp.py")
    server = mock.MockCDPServer(resources=[
        {"url": f"{BASE}/assets/img/landscape.png?from=browser", "type": "Image"},
        {"url": f"{BASE}/assets/media/song.mp3?from=browser", "type": "Media"},
        {"url": f"{BASE}/assets/img/lazy.png?from=browser", "type": "Image"},
    ]).start()
    try:
        from resource_sniffer.browser import BrowserSession, CDPClient

        class MockBrowser(BrowserSession):
            mock_server = None

            def start(self):
                self.cdp = CDPClient(self.mock_server.ws_url())
                self.cdp.set_event_callback(self._on_event)
                self._enable_domains()

            def close(self):
                try:
                    self.cdp.close()
                except Exception:
                    pass

        MockBrowser.mock_server = server
        items, summary = run_sniff(f"{BASE}/page.html", browser_factory=MockBrowser,
                                   browser_path="mock.exe", dynamic=True)
        urls = {i.url for i in items}
        check("浏览器会话建立成功", bool(summary.get("dynamic")))
        check("命令序列包含 Network.enable",
              "Network.enable" in server.commands, str(len(server.commands)) + " 条命令")
        check("抓到仅由浏览器发现的资源",
              any("from=browser" in u for u in urls),
              str([u.rsplit('/', 1)[-1] for u in urls if "from=browser" in u]))
        check("动态资源标记来源 network",
              any(i.source == "network" and "from=browser" in i.url
                  for i in items))
        check("动态与静态结果去重合并",
              len({i.url for i in items}) == len(items))
    finally:
        server.stop()

    print("\n   真实浏览器可用性：", end="")
    from resource_sniffer.browser import find_browser

    found = find_browser()
    print(found or "未找到 Edge/Chrome")
    check("检测到 Chromium 内核浏览器", bool(found), found)


def test_download(items: list) -> None:
    print("\n== 5. 下载（单文件 + HLS 合并） ==")
    folder = os.path.join(ROOT, f"rs_dl_{os.getpid()}")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    try:
        mp3 = next((i for i in items if i.url.endswith("song.mp3")), None)
        png = next((i for i in items if i.url.endswith("landscape.png")), None)
        master = next((i for i in items if i.url.endswith("master.m3u8")), None)
        targets = [t for t in (mp3, png, master) if t]
        manager = DownloadManager()
        progress_seen = {"n": 0}

        def on_progress(_task):
            progress_seen["n"] += 1

        manager.on_progress = on_progress
        tasks = manager.enqueue(targets, folder)
        manager.start()
        manager.join(120)
        for task in tasks:
            print(f"    {os.path.basename(task.filepath)}: {task.state} "
                  f"{fmt_size(task.done_bytes)}  {task.error}")
        by_name = {os.path.basename(t.filepath): t for t in tasks}
        mp3_task = by_name.get("song.mp3")
        check("MP3 下载成功且大小一致",
              bool(mp3_task and mp3_task.state == "done"
                   and mp3_task.done_bytes == mp3.size))
        png_task = by_name.get("landscape.png")
        check("图片下载成功", bool(png_task and png_task.state == "done"))
        hls_task = next((t for t in tasks if t.filepath.endswith(".ts")), None)
        check("HLS 分片合并为 .ts",
              bool(hls_task and hls_task.state == "done"
                   and hls_task.done_bytes > 10000),
              f"{fmt_size(hls_task.done_bytes)} -> "
              f"{os.path.basename(hls_task.filepath)}" if hls_task else "")
        check("下载过程有进度回调", progress_seen["n"] > 0,
              f"{progress_seen['n']} 次")

        # 大文件多连接 Range 下载：9MB，4 段，校验总大小
        range_path = os.path.join(folder, "range.bin")
        range_progress = {"last": 0}
        range_size, _final = HttpSession().download_file(
            f"{BASE}/range/9000000", range_path, segments=4,
            chunk_size=256 * 1024,
            progress=lambda d, t: range_progress.update(last=d))
        check("大文件多连接分段下载完整",
              os.path.exists(range_path) and range_size == 9000000
              and os.path.getsize(range_path) == 9000000,
              f"{range_size} / {os.path.getsize(range_path) if os.path.exists(range_path) else 0}")
        check("分段下载进度回调覆盖完整文件",
              range_progress["last"] == 9000000,
              range_progress["last"])
        if hls_task and os.path.exists(hls_task.filepath):
            with open(hls_task.filepath, "rb") as fh:
                data = fh.read(400)
            check("合并文件是有效 TS（47 同步字节）", data[:1] == b"\x47")

        # AES-128 加密 HLS：解密后应等于 NIST 明文
        testsite_mod = load("testsite", "site.py")
        enc_task = DownloadTask(url=f"{BASE}/hls/enc/index.m3u8",
                                filepath=os.path.join(folder, "enc.ts"),
                                kind="stream")
        manager2 = DownloadManager()
        manager2.tasks = [enc_task]
        manager2.start()
        manager2.join(60)
        decoded = b""
        if os.path.exists(enc_task.filepath):
            with open(enc_task.filepath, "rb") as fh:
                decoded = fh.read()
        ok = (enc_task.state == "done"
              and decoded == testsite_mod.ENC_PLAINTEXT)
        check("AES-128 加密 HLS 解密正确", ok,
              f"{enc_task.state} {enc_task.error or ''} "
              f"{len(decoded)} 字节")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class _StubSession:
    """模拟下载会话：可指定失败次数/耗时，用于检查并发与重试。

    不访问网络；前 ``fail_times`` 次写入半个文件后抛 NetworkError，
    之后写满 ``size`` 字节并回调进度。
    """

    def __init__(self, fail_times: int = 0, delay: float = 0.0,
                 size: int = 8192):
        self.fail_times = fail_times
        self.delay = delay
        self.size = size
        self.calls: list[str] = []
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()

    def download_file(self, url, filepath, referer="", progress=None,
                      cancel=None, headers=None, resume=True, **kwargs):
        with self.lock:
            self.calls.append(url)
            attempt = self.calls.count(url)
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            if self.delay:
                time.sleep(self.delay)
            with open(filepath, "wb") as fh:
                if attempt <= self.fail_times:
                    fh.write(b"x" * (self.size // 2))
                    raise NetworkError("模拟网络中断")
                fh.write(b"x" * self.size)
            if progress:
                progress(self.size, self.size)
            return self.size, url
        finally:
            with self.lock:
                self.running -= 1


def test_download_queue() -> None:
    print("\n== 5b. 并行下载队列 / 自动重试 ==")
    folder = os.path.join(ROOT, f"rs_queue_{os.getpid()}")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    try:
        session = _StubSession(delay=0.6)
        manager = DownloadManager(session=session, max_workers=3)
        tasks = [DownloadTask(url=f"http://stub/{i}.bin",
                              filepath=os.path.join(folder, f"{i}.bin"))
                 for i in range(3)]
        manager.tasks = tasks
        manager.start()
        time.sleep(0.25)
        running = sum(1 for t in tasks if t.state == "running")
        check("多个文件同时下载", running >= 2, f"{running} 个并发")
        manager.join(30)
        stats = manager.stats()
        check("并发队列全部完成",
              all(t.state == "done" for t in tasks)
              and stats["done"] == 3 and stats["failed"] == 0,
              f"{' '.join(t.state for t in tasks)} / {stats}")
        check("并发数不超过设置上限", session.peak <= 3, f"峰值 {session.peak}")

        retry_session = _StubSession(fail_times=1, size=4096)
        retry_manager = DownloadManager(session=retry_session, max_workers=1)
        retry_task = DownloadTask(url="http://stub/retry.bin",
                                  filepath=os.path.join(folder, "retry.bin"))
        retry_manager.tasks = [retry_task]
        retry_manager.start()
        retry_manager.join(30)
        size = (os.path.getsize(retry_task.filepath)
                if os.path.exists(retry_task.filepath) else 0)
        check("下载失败自动重试并完成",
              retry_task.state == "done" and size == 4096,
              f"{retry_task.state} {size}B {retry_task.error}")
        check("重试次数符合预期（失败 1 次 + 成功 1 次）",
              len(retry_session.calls) == 2, f"{len(retry_session.calls)} 次")

        # 取消：排队中的立即移除，进行中的在本次下载返回后退出
        cancel_session = _StubSession(delay=0.8, size=2048)
        cancel_manager = DownloadManager(session=cancel_session, max_workers=1)
        running_task = DownloadTask(url="http://stub/c1.bin",
                                    filepath=os.path.join(folder, "c1.bin"))
        queued_task = DownloadTask(url="http://stub/c2.bin",
                                   filepath=os.path.join(folder, "c2.bin"))
        cancel_manager.tasks = [running_task, queued_task]
        cancel_manager.start()
        time.sleep(0.2)
        cancel_manager.cancel_task(queued_task)
        check("取消排队中的下载", queued_task.state == "canceled",
              queued_task.state)
        cancel_manager.cancel_task(running_task)
        cancel_manager.join(15)
        check("取消进行中的下载", running_task.state == "canceled",
              f"{running_task.state} {running_task.error}")
        cancel_manager.enqueue([], folder)
        check("新一批下载前清理已结束任务", cancel_manager.tasks == [],
              f"{len(cancel_manager.tasks)} 条")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_ytdlp_fake() -> None:
    """用伪 yt-dlp 可执行文件验证检测 / 剧集解析 / 子进程下载。"""
    print("\n== 5c. yt-dlp 集成（伪可执行文件） ==")
    folder = os.path.join(ROOT, f"rs_ytdlp_{os.getpid()}")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    script = os.path.join(folder, "fake_ytdlp.py")
    batch = os.path.join(folder, "yt-dlp.bat")
    try:
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "if '--dump-single-json' in args:\n"
                "    print(json.dumps({'title': '示例剧集', 'entries': [\n"
                "        {'id': 'ep1', 'title': '第 1 集', 'url': 'https://v.example/1',"
                " 'duration': 120, 'playlist_index': 1},\n"
                "        {'id': 'ep2', 'title': '第 2 集', 'url': 'https://v.example/2',"
                " 'duration': 130, 'playlist_index': 2}]}, ensure_ascii=False))\n"
                "else:\n"
                "    out = args[args.index('-o') + 1] if '-o' in args else 'out.bin'\n"
                "    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)\n"
                "    with open(out, 'wb') as fh:\n"
                "        fh.write(b'x' * 2048)\n"
                "    print('[download]  50.0% of 2.00KiB')\n"
                "    print('[download] 100% of 2.00KiB')\n"
                "    print(out)\n")
        with open(batch, "w", encoding="utf-8") as fh:
            fh.write('@echo off\n"%s" "%s" %%*\n' % (sys.executable, script))

        from resource_sniffer import ytdlp

        os.environ["YTDLP_PATH"] = batch
        ytdlp.reset()
        check("检测到 yt-dlp 命令", ytdlp.available(), ytdlp.describe())
        data = ytdlp.extract("https://fake.example/list")
        check("拉取到剧集列表", len(data.get("entries", [])) == 2,
              f"{len(data.get('entries', []))} 集")
        first = ytdlp.entry_to_item_data(data["entries"][0], "https://fake.example/list")
        check("剧集条目字段解析",
              first["title"] == "第 1 集"
              and first["url"] == "https://v.example/1"
              and first["duration"] == 120.0, str(first))

        target = os.path.join(folder, "episode-01.mp4")
        task = DownloadTask(url="https://fake.example/1", filepath=target,
                            engine="ytdlp", title="第 1 集")
        manager = DownloadManager(max_workers=1)
        manager.tasks = [task]
        manager.start()
        manager.join(30)
        size = os.path.getsize(target) if os.path.exists(target) else 0
        check("yt-dlp 子进程下载完成",
              task.state == "done" and size == 2048,
              f"{task.state} {size}B {task.error}")
    finally:
        os.environ.pop("YTDLP_PATH", None)
        try:
            from resource_sniffer import ytdlp as _ytdlp

            _ytdlp.reset()
        except Exception:
            pass
        shutil.rmtree(folder, ignore_errors=True)


def test_units() -> None:
    print("\n== 6. 解析器 / 网络 / 主题单元检查 ==")
    pl = streams.parse_m3u8(
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1920x1080\n'
        'v/1080.m3u8\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="中文",'
        'URI="a/zh.m3u8"\n', "http://x/y.m3u8")
    check("HLS 主清单：变体 + 独立音轨", len(pl.variants) == 2)
    check("相对地址解析正确", pl.variants[0].url == "http://x/v/1080.m3u8")

    media = streams.parse_m3u8(
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\na.ts\n#EXTINF:2.5,\nb.ts\n"
        "#EXT-X-ENDLIST\n", "http://x/live/i.m3u8")
    check("HLS 媒体清单：分片与总时长",
          len(media.segments) == 2 and abs(media.duration - 6.5) < 0.01,
          f"{media.duration}s")

    mpd = ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
           'mediaPresentationDuration="PT1M30S"><Period><AdaptationSet '
           'mimeType="video/mp4"><Representation id="v1" bandwidth="2000000" '
           'width="1280" height="720"><BaseURL>v1.mp4</BaseURL></Representation>'
           '</AdaptationSet></Period></MPD>')
    dash = streams.parse_mpd(mpd, "http://x/dash/manifest.mpd")
    check("DASH：Representation 与时长",
          len(dash.variants) == 1 and dash.variants[0].height == 720
          and abs(dash.duration - 90) < 0.01)

    from resource_sniffer.htmlparse import extract_from_html, guess_filename
    found = extract_from_html(
        '<img src="/a.jpg"><img data-src="/b.png"><img srcset="/c.webp 2x, /d.png 1x">'
        '<video><source src="/e.mp4"></video><script>var u="/f.mp3";</script>',
        "http://h.com/dir/page")
    all_urls = sum((v for k, v in found.items() if isinstance(v, list)), [])
    check("HTML 提取 src/data-src/srcset/source/脚本直链",
          all(any(u.endswith(x) for u in all_urls)
              for x in ("a.jpg", "b.png", "c.webp", "e.mp4", "f.mp3")))
    check("Content-Disposition 文件名解析",
          guess_filename("http://x/y", 'attachment; filename="报告.pdf"') == "报告.pdf")
    check("URL 解码文件名",
          guess_filename("http://x/%E5%9B%BE%E7%89%87.png") == "图片.png")

    session = HttpSession()
    resp = session.peep(f"{BASE}/assets/media/song.mp3", nbytes=1000)
    check("Range 截断读取", len(resp.body) == 1000 and resp.truncated,
          f"{len(resp.body)} 字节, total={resp.total_size()}")

    # AES（HLS 加密）自测向量
    from resource_sniffer.aes import aes128_cbc_decrypt

    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes(range(16))
    ct = bytes.fromhex("7649abac8119b246cee98e9b12e9197d"
                       "5086cb9b507219ee95db113a917678b2")
    plain = aes128_cbc_decrypt(ct, key, iv)
    check("AES-128-CBC 解密（NIST 向量）",
          plain == bytes.fromhex("6bc1bee22e409f96e93d7e117393172a"
                                 "ae2d8a571e03ac9c9eb76fac45af8e51"))

    # 配置文件：脏数据清洗 + 原子保存
    from resource_sniffer import config as cfgmod

    dirty = cfgmod.Config.from_dict({
        "workers": "abc", "concurrency": 99, "download_segments": 99,
        "timeout": -3,
        "kinds": ["image", "bogus"], "dynamic": "false", "prox": 1,
        "max_items": 10 ** 9,
    })
    check("脏配置被清洗（类型 / 范围 / 合法值）",
          dirty.workers == 10 and dirty.concurrency == 8
          and dirty.download_segments == 8
          and dirty.timeout == 1.0 and dirty.max_items == 10000
          and dirty.kinds == ["image"] and dirty.dynamic is False,
          f"workers={dirty.workers} concurrency={dirty.concurrency} "
          f"timeout={dirty.timeout} kinds={dirty.kinds}")
    cfg_dir = os.path.join(ROOT, f"rs_cfg_{os.getpid()}")
    shutil.rmtree(cfg_dir, ignore_errors=True)
    os.makedirs(cfg_dir, exist_ok=True)
    try:
        check("默认下载目录在软件目录的 Download 下",
              cfgmod.default_download_dir(cfg_dir)
              == os.path.join(cfg_dir, "Download"),
              cfgmod.default_download_dir(cfg_dir))
        sample = cfgmod.Config()
        sample.proxy_mode = "proxy"
        sample.concurrency = 5
        sample.window_geometry = "1200x800+10+20"
        sample.check_updates = False
        sample.skipped_version = "9.9.9"
        sample.download_segments = 6
        saved = cfgmod.save(sample, cfg_dir)
        loaded = cfgmod.load(cfg_dir)
        leftover = os.path.exists(os.path.join(cfg_dir, "config.json.tmp"))
        check("配置原子保存并可回读",
              saved and not leftover and loaded.concurrency == 5
              and loaded.proxy_mode == "proxy"
              and loaded.window_geometry == "1200x800+10+20"
              and loaded.check_updates is False
              and loaded.skipped_version == "9.9.9"
              and loaded.download_segments == 6,
              f"saved={saved} tmp={leftover}")
        check("未设置下载目录时自动使用软件目录 Download",
              loaded.download_dir == os.path.join(cfg_dir, "Download"),
              loaded.download_dir)
    finally:
        shutil.rmtree(cfg_dir, ignore_errors=True)

    # 视觉主题 / 圆角 / 主题色
    from resource_sniffer import theme as theme_mod

    check("内置 5 套视觉预设",
          set(theme_mod.PRESET_KEYS) == {"default", "acrylic", "glass",
                                         "neon", "retro"},
          ", ".join(theme_mod.PRESET_KEYS))
    theme_mod.apply_style("retro", "#34d399", 0, 0, True)
    check("复古主题切换为浅色直角",
          theme_mod.CLASSIC and theme_mod.BG == "#d4d0c8"
          and theme_mod.RADIUS_BUTTON == 0,
          f"{theme_mod.BG} / r={theme_mod.RADIUS_BUTTON}")
    theme_mod.apply_style("glass", "#f472b6", 18, 22, False)
    check("主题色 / 按钮圆角 / 窗口圆角生效",
          theme_mod.ACCENT == "#f472b6" and theme_mod.RADIUS_BUTTON == 18
          and theme_mod.RADIUS_WINDOW == 22 and not theme_mod.ANIMATIONS,
          f"{theme_mod.ACCENT} / {theme_mod.RADIUS_BUTTON} / "
          f"{theme_mod.RADIUS_WINDOW}")
    check("液态玻璃使用独立布局与卡片列表",
          theme_mod.LAYOUT["shell"] == "floating"
          and theme_mod.LAYOUT["list"] == "cards"
          and theme_mod.LAYOUT["traffic_lights"] is True
          and theme_mod.MOTION == "spring",
          f"{theme_mod.LAYOUT['shell']} / {theme_mod.LAYOUT['list']}")
    theme_mod.apply_style("acrylic", "", None, None, True)
    check("亚克力使用左侧导航布局",
          theme_mod.LAYOUT["shell"] == "sidebar"
          and theme_mod.LAYOUT["filter_in_sidebar"] is True,
          theme_mod.LAYOUT["shell"])
    theme_mod.apply_style("retro", "", None, None, True)
    check("复古使用菜单栏 / 状态栏布局",
          theme_mod.LAYOUT["header"] == "menu"
          and theme_mod.LAYOUT["footer"] == "status",
          f"{theme_mod.LAYOUT['header']} / {theme_mod.LAYOUT['footer']}")
    check("五套主题的布局规格不完全相同",
          len({tuple(sorted((k, str(v)) for k, v in
                            theme_mod.LAYOUTS[key].items()))
               for key in theme_mod.PRESET_KEYS}) == 5,
          "layouts unique")
    theme_mod.apply_style("default", "", 10, 0, True)

    # 应用图标（exe / 窗口图标共用）
    ico_path = os.path.join(ROOT, "assets", "starsniff.ico")
    png_path = os.path.join(ROOT, "assets", "starsniff.png")
    ico_head = b""
    if os.path.exists(ico_path):
        with open(ico_path, "rb") as fh:
            ico_head = fh.read(4)
    check("应用图标文件齐全且 ICO 头有效",
          os.path.exists(png_path) and ico_head == b"\x00\x00\x01\x00",
          f"{os.path.getsize(ico_path) if os.path.exists(ico_path) else 0} bytes")

    # 检查更新（不访问网络，mock GitHub 响应）
    from resource_sniffer import updater

    check("版本号解析支持 v 前缀与多段",
          updater.parse_version("v2.1.0") == (2, 1, 0, 0)
          and updater.parse_version("StarSniff 3.10") == (3, 10, 0, 0),
          str(updater.parse_version("v2.1.0")))
    check("版本比较能识别新旧",
          updater.is_newer("2.1.0", "2.0.9")
          and not updater.is_newer("2.0.0", "2.0.0")
          and not updater.is_newer("1.9.9", "2.0.0"),
          "2.1.0 > 2.0.9")
    asset_exe = updater.ReleaseAsset("StarSniff-2.1.0.exe",
                                     "https://example.com/a.exe")
    asset_zip = updater.ReleaseAsset("StarSniff-2.1.0.zip",
                                     "https://example.com/a.zip")
    info = updater.UpdateInfo(version="2.1.0", tag="v2.1.0",
                              assets=[asset_zip, asset_exe])
    check("Release 优先选择 exe 附件",
          info.download_url == asset_exe.url and info.is_newer_than("2.0.0"),
          info.download_url)

    real_read_json = updater._read_json
    real_fallback = updater._latest_tag_from_redirect
    try:
        updater._read_json = lambda url, timeout: {
            "tag_name": "v2.1.0", "name": "StarSniff 2.1.0",
            "body": "测试更新说明", "html_url": "https://example.com/rel",
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [{"name": "StarSniff-2.1.0-win64.zip",
                        "browser_download_url": "https://example.com/a.zip",
                        "size": 123}],
        }
        fetched = updater.check_latest()
        check("能从 GitHub Release JSON 解析更新",
              fetched.version == "2.1.0" and fetched.notes == "测试更新说明"
              and fetched.assets[0].name.endswith(".zip"),
              f"{fetched.version} / {len(fetched.assets)} assets")

        def _rate_limited(url, timeout):
            raise updater.UpdateError("限流", "rate_limit")

        updater._read_json = _rate_limited
        updater._latest_tag_from_redirect = lambda timeout: "v2.2.0"
        fallback = updater.check_latest()
        check("API 限流时退回 releases/latest 跳转",
              fallback.version == "2.2.0" and "限流" in fallback.notes,
              fallback.version)
    finally:
        updater._read_json = real_read_json
        updater._latest_tag_from_redirect = real_fallback

    # yt-dlp 条目解析（不需要真的安装 yt-dlp）
    from resource_sniffer import ytdlp

    ytdlp.reset()
    check("yt-dlp 未安装时安全降级", isinstance(ytdlp.available(), bool),
          ytdlp.describe())
    check("yt-dlp 剧集条目转成可下载 URL",
          ytdlp.entry_url({"url": "dQw4w9WgXcQ", "ie_key": "Youtube"},
                          "https://youtube.com/playlist?list=x")
          == "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    data = ytdlp.entry_to_item_data(
        {"id": "ep1", "title": "第 1 集", "url": "https://v.example/ep1",
         "duration": 123, "filesize": 456}, "https://v.example/list")
    check("yt-dlp 条目字段解析",
          data["title"] == "第 1 集" and data["url"] == "https://v.example/ep1"
          and data["duration"] == 123.0 and data["size"] == 456,
          str(data))


def test_media_parsers() -> None:
    print("\n== 7. 媒体解析边界情况 ==")
    session = HttpSession()
    cases = {
        "整页 HTML 不应被识别为媒体": (f"{BASE}/page.html", "other"),
        "m3u8 识别为流媒体": (f"{BASE}/hls/master.m3u8", "stream"),
    }
    for label, (url, expect) in cases.items():
        resp = session.peep(url, nbytes=64 * 1024)
        info = mediainfo.probe(resp.body, b"", resp.total_size(),
                               resp.content_type, mediainfo.ext_of(url))
        check(label, info.kind == expect, f"{info.kind}/{info.format}")
    data = b"\xff\xfb\x90\x64" + b"\x00" * 400
    info = mediainfo.probe(data, b"", len(data), "audio/mpeg", "mp3")
    check("裸 MP3 帧头也能读出码率", info.bitrate == 128000,
          fmt_bitrate(info.bitrate))


def test_user_agent() -> None:
    print("\n== 8. User-Agent 伪装（引擎层） ==")
    from resource_sniffer import useragents

    site = SITE or load("testsite", "site.py")
    keys = [p.key for p in useragents.PROFILES]
    check("内置 UA 数量（7 + 默认）", len(keys) == 8, str(len(keys)))
    for key in ("win_chrome", "win_firefox", "android_chrome", "ios_safari",
                "mac_chrome", "linux_firefox", "linux_chrome"):
        profile = useragents.get(key)
        run_sniff(f"{BASE}/page.html", ua_key=key, deep=False,
                  dynamic=False)
        sent = site.LAST_HEADERS.get("user-agent", "")
        ok = bool(sent) and sent == profile.ua
        detail = (sent or "")[:40]
        if ok and profile.brand:
            ok = bool(site.LAST_HEADERS.get("sec-ch-ua"))
        check(f"引擎使用 {profile.label}", ok, detail)

    run_sniff(f"{BASE}/page.html", ua_key="auto", deep=False, dynamic=False)
    auto_ua = site.LAST_HEADERS.get("user-agent", "")
    check("默认模式仍带合法桌面 UA",
          "Mozilla/5.0" in auto_ua and "Mobile" not in auto_ua, auto_ua[:40])


def test_extensionless_and_variants() -> None:
    """X/Twitter 这类站点：媒体没有路径扩展名，且同一张图有多个尺寸变体。"""
    print("\n== 9. 无扩展名媒体 + 尺寸变体合并 ==")
    items, _ = run_sniff(f"{BASE}/variants.html", dynamic=False)
    urls = [i.url for i in items]
    images = [i for i in items if i.kind == "image"]

    check("带 ?format= 的无扩展名图片被识别",
          any("?format=png" in u for u in urls),
          f"{len(images)} 张图片")
    check("带 ?format=webp 的图片被识别",
          any("?format=webp" in u for u in urls))
    # landscape 的 4 个变体 + preload 里的 1 个，应合并成 1 条
    landscape = [i for i in images
                 if "landscape.png" in i.url or
                 ("landscape" in i.url and "format" in i.url)]
    check("同一图片的多个尺寸变体合并为 1 条", len(landscape) == 1,
          f"{len(landscape)} 条：{[i.url.rsplit('/', 1)[-1] for i in landscape]}")
    if landscape:
        kept = landscape[0]
        check("保留的是最大尺寸（320×240）",
              (kept.width, kept.height) == (320, 240),
              f"{kept.width}×{kept.height}")
        check("合并在备注里说明", "已合并" in kept.extra, kept.extra)
    check("不同图片不会被误合并",
          any("square" in u for u in urls), f"{len(images)} 张图片")
    check("带 query 的视频仍被识别",
          any(i.kind == "video" and "tag=29" in i.url for i in items))

    # URL 判定函数本身
    from resource_sniffer.mediainfo import ext_from_url, variant_key

    cases = [
        ("https://pbs.twimg.com/media/ABC?format=jpg&name=large", "jpg", True),
        ("https://cdn.x.com/a?fm=webp&w=800", "webp", True),
        ("https://x.com/page.html", "html", False),
        ("https://x.com/api/list?type=json", "json", False),
        ("https://v.cdn.com/a.m3u8?tag=1", "m3u8", True),
    ]
    for url, want_ext, want_media in cases:
        from resource_sniffer.mediainfo import looks_like_media_url

        ok = ext_from_url(url) == want_ext and looks_like_media_url(url) == want_media
        check(f"URL 识别 {want_ext}", ok, url[:52])
    check("不同 id 的图片不会被误判为同一张",
          variant_key("https://x.com/i.php?id=1", "image")
          != variant_key("https://x.com/i.php?id=2", "image"))


def test_slow_resources() -> None:
    """站点不响应时，不能一直卡在「解析中」：到时限就先出结果。"""
    print("\n== 10. 慢资源 / 探测时限 ==")
    started = time.monotonic()
    items, summary = run_sniff(f"{BASE}/slowpage.html", dynamic=False,
                               timeout=3, probe_deadline=3)
    elapsed = time.monotonic() - started
    check("整体在时限内结束（不被慢请求拖死）", elapsed < 15,
          f"{elapsed:.1f}s")
    urls = [i.url for i in items]
    check("慢资源仍被列出（标注未获取元数据）",
          any("/slow/" in u for u in urls), f"{len(items)} 条")
    check("快资源正常解析出尺寸",
          any(i.width == 320 and i.height == 240 for i in items),
          str([(i.filename, f"{i.width}x{i.height}") for i in items][:3]))
    slow_items = [i for i in items if "/slow/" in i.url]
    check("慢资源标注了超时提示",
          all(i.state == "error" and "超时" in (i.message or "")
              for i in slow_items),
          slow_items[0].message if slow_items else "")

    # 停止要立刻返回，不能等套接字超时
    import threading as _th

    done = _th.Event()
    sniffer = Sniffer(on_done=lambda s: done.set())
    sniffer.start(f"{BASE}/slowpage.html",
                  SniffOptions(dynamic=False, timeout=20, probe_deadline=30))
    time.sleep(1.2)
    t_stop = time.monotonic()
    sniffer.stop()
    finished = done.wait(8)
    cost = time.monotonic() - t_stop
    check("点击停止后迅速结束", finished and cost < 5, f"{cost:.1f}s")


def start_http_proxy(delay: float = 0.0, shortcut_delay: bool = False):
    """起一个最小 HTTP 正向代理（支持 absolute-form），可加固定延迟。

    仅用于测试智能代理选路：把请求转发到本机测试站点。
    shortcut_delay=True 时对 /delay 直接返回（模拟「代理有缓存所以更快」）。
    """
    import http.client
    import http.server
    import socketserver
    from urllib.parse import urlsplit

    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _plain(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                       # noqa: N802
            parts = urlsplit(self.path)
            if shortcut_delay and parts.path == "/delay":
                self._plain(b"ok")              # 代理侧直接命中，远快于直连
                return
            time.sleep(delay)
            if parts.scheme != "http" or not parts.hostname:
                self.send_error(400, "only http absolute-form")
                return
            path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("proxy-connection", "connection",
                                            "host")}
            # 不覆盖客户端给的 Host，便于验证代理请求携带的是目标站点 Host
            supplied_host = next((v for k, v in self.headers.items()
                                  if k.lower() == "host"), "")
            headers["Host"] = supplied_host or parts.netloc
            conn = None
            try:
                conn = http.client.HTTPConnection(parts.hostname,
                                                  parts.port or 80, timeout=10)
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                self.send_response(resp.status)
                for key, value in resp.getheaders():
                    if key.lower() not in ("connection", "transfer-encoding",
                                           "content-length", "keep-alive"):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass                            # 客户端只读响应头就断开，正常
            except Exception:
                pass
            finally:
                if conn is not None:
                    conn.close()

    class ProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ProxyServer(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="test-proxy").start()
    return server


def test_smart_proxy() -> None:
    print("\n== 11. 智能代理（测速选路 / 强制模式 / 环境代理） ==")
    from resource_sniffer import smartproxy
    from resource_sniffer.config import Config
    from resource_sniffer.net import NetworkError

    cfg = Config.from_dict({"proxy": "127.0.0.1:7890", "proxy_mode": "direct"})
    check("配置保存代理模式", cfg.proxy_mode == "direct")
    check("非法代理模式回落为智能选择",
          Config.from_dict({"proxy_mode": "bogus"}).proxy_mode == "auto")

    route = smartproxy.choose(f"{BASE}/page.html", mode="direct",
                              proxy="127.0.0.1:1", timeout=1)
    check("强制直连：不测速、不使用代理",
          not route.used_proxy and not route.tested and not route.proxy)

    route = smartproxy.choose(f"{BASE}/page.html", mode="proxy",
                              proxy="127.0.0.1:1", timeout=1)
    check("强制代理：直接选中代理",
          route.used_proxy and route.proxy == "127.0.0.1:1")

    route = smartproxy.choose(f"{BASE}/page.html", mode="auto", proxy="",
                              timeout=1)
    check("未配置代理时直接直连",
          not route.used_proxy and not route.tested and "未配置" in route.reason,
          route.reason)

    # 代理端口没人监听：自动落到直连，并保留代理错误原因
    route = smartproxy.choose(f"{BASE}/page.html", mode="auto",
                              proxy="127.0.0.1:1", timeout=1.5)
    check("代理不可用时自动直连",
          route.tested and not route.used_proxy and bool(route.proxy_error),
          route.reason)

    slow_proxy = start_http_proxy(0.30)
    fast_proxy = start_http_proxy(0.0, shortcut_delay=True)
    try:
        slow_addr = f"127.0.0.1:{slow_proxy.server_port}"
        fast_addr = f"127.0.0.1:{fast_proxy.server_port}"

        route = smartproxy.choose(f"{BASE}/page.html", mode="auto",
                                  proxy=slow_addr, timeout=3)
        check("直连更快时选直连",
              route.tested and not route.used_proxy
              and route.direct_ms is not None and route.proxy_ms is not None
              and route.direct_ms < route.proxy_ms,
              route.reason)

        route = smartproxy.choose(f"{BASE}/delay?ms=350", mode="auto",
                                  proxy=fast_addr, timeout=3)
        check("代理更快时选代理",
              route.tested and route.used_proxy
              and route.proxy_ms is not None and route.direct_ms is not None
              and route.proxy_ms + 5 < route.direct_ms,
              route.reason)

        # 强制直连必须无视系统环境变量里的代理
        keys = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy")
        saved = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ[key] = "http://127.0.0.1:1"
        try:
            forced = HttpSession(timeout=3, direct=True)
            resp = forced.peep(f"{BASE}/page.html", nbytes=100)
            check("强制直连绕过系统环境代理", resp.ok,
                  f"HTTP {resp.status}")

            default_session = HttpSession(timeout=2)
            failed = False
            try:
                default_session.peep(f"{BASE}/page.html", nbytes=100)
            except NetworkError:
                failed = True
            check("普通会话仍会读取系统环境代理", failed)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        # 引擎接入：强制代理时，页面和资源请求都经过代理
        items, summary = run_sniff(f"{BASE}/page.html", proxy=fast_addr,
                                   proxy_mode="proxy", dynamic=False, deep=False)
        info = summary.get("route") or {}
        check("引擎按强制代理抓取",
              bool(info.get("used_proxy"))
              and any(i.url.endswith("landscape.png") for i in items),
              f"{info.get('label')} / {len(items)} 条")
        site = SITE or load("testsite", "site.py")
        check("经代理的请求携带目标站点 Host",
              site.LAST_HEADERS.get("host") == f"127.0.0.1:{PORT}",
              site.LAST_HEADERS.get("host", ""))

        # 引擎接入：直连很慢、代理很快时，自动改用代理
        _, summary = run_sniff(f"{BASE}/delay?ms=350", proxy=fast_addr,
                               proxy_mode="auto", dynamic=False)
        info = summary.get("route") or {}
        check("嗅探时自动选择更快的代理",
              bool(info.get("used_proxy")) and bool(info.get("tested")),
              info.get("reason", ""))
    finally:
        slow_proxy.shutdown()
        fast_proxy.shutdown()


def main() -> int:
    global SITE
    testsite = load("testsite", "site.py")
    SITE = testsite
    testsite.build_assets()
    server = testsite.serve(PORT)
    try:
        items = test_static()
        manifest_items = test_manifest()
        test_direct()
        test_dynamic()
        test_download(items + manifest_items)
        test_download_queue()
        test_ytdlp_fake()
        test_units()
        test_media_parsers()
        test_user_agent()
        test_extensionless_and_variants()
        test_smart_proxy()
        test_slow_resources()
    finally:
        server.shutdown()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  ✗ {name}")
    if not FAIL:
        print("全部通过 ✔")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
