"""浏览器内核发现与 CDP（Chrome DevTools Protocol）驱动。

资源嗅探分两路：
1. 静态嗅探（httpx + HTML 解析）：快，能抓住页面里写死的图片/音视频；
2. 动态嗅探（本模块）：启动 Edge/Chrome 的无头实例，真实加载页面，
   从 Network 域里拿到**所有**请求过的媒体（含 JS 动态插入、异步加载、blob 之外的一切），
   这是「嗅探器」最核心的能力。

CDP 通过 WebSocket 通讯。为了零第三方依赖，这里内置了一个最小 WebSocket 客户端
（只实现 RFC6455 的文本帧收发，足够和 DevTools 通信）。
"""

from __future__ import annotations

import base64
import json
import os
import random
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import queue
from pathlib import Path
from typing import Callable, Optional
from urllib.error import URLError
from urllib.parse import urlsplit

USER_AGENT_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_CANDIDATE_BROWSERS = [
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Chromium\Application\chrome.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def _make_temp_dir(prefix: str) -> str:
    """建立临时目录。

    不用 tempfile.mkdtemp：某些受限环境（沙箱/组策略）下 mkdtemp 生成的
    ACL 会阻止后续写入，改用普通 makedirs 继承父目录权限。
    """
    base = tempfile.gettempdir()
    for _ in range(20):
        path = os.path.join(base, f"{prefix}{random.randrange(10 ** 8):08d}")
        if os.path.isdir(path):
            continue
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            continue
    return tempfile.mkdtemp(prefix=prefix)


def find_browser(explicit: str = "") -> str:
    """返回可用的 Chromium 内核浏览器路径，找不到返回空串。"""
    if explicit and os.path.isfile(explicit):
        return explicit
    env = os.environ.get("RS_BROWSER", "")
    if env and os.path.isfile(env):
        return env
    for pattern in _CANDIDATE_BROWSERS:
        path = os.path.expandvars(pattern)
        if os.path.isfile(path):
            return path
    # PATH 里找
    for name in ("chrome", "msedge", "chromium", "google-chrome", "chromium-browser"):
        for folder in (os.environ.get("PATH") or "").split(os.pathsep):
            if not folder:
                continue
            for ext in ("", ".exe"):
                candidate = os.path.join(folder, name + ext)
                if os.path.isfile(candidate):
                    return candidate
    return ""


# --------------------------------------------------------------------------
# 最小 WebSocket 客户端
# --------------------------------------------------------------------------
class WebSocket:
    """仅支持 ws:// 的文本帧客户端（DevTools 调试端口就是 ws://）。"""

    def __init__(self, url: str, host_header: str = "", origin: str = ""):
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "http"):
            raise ValueError("只支持 ws:// 连接")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("DevTools 只支持本机连接")
        self.sock = socket.create_connection((host, port), timeout=20)
        self.sock.settimeout(None)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header or f'{host}:{port}'}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if origin:
            lines.append(f"Origin: {origin}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        # 读握手响应
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手失败：连接已关闭")
            buf += chunk
        head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        if " 101" not in head.split("\r\n")[0]:
            raise ConnectionError(f"WebSocket 握手失败: {head.splitlines()[0]}")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]
        self._lock = threading.Lock()
        self.closed = False

    # ------------------------------------------------------------ 帧操作
    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            if self._buf:
                take = min(n - len(out), len(self._buf))
                out += self._buf[:take]
                self._buf = self._buf[take:]
                continue
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("WebSocket 连接已断开")
            out += chunk
        return bytes(out)

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def recv(self, timeout: Optional[float] = None) -> Optional[str]:
        """读取一条文本消息；超时返回 None。"""
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                b1, b2 = self._recv_exact(2)
                opcode = b1 & 0x0F
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack(">H", self._recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack(">Q", self._recv_exact(8))[0]
                masked = b2 & 0x80
                mask = self._recv_exact(4) if masked else b""
                payload = self._recv_exact(length) if length else b""
                if masked:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:
                    self.closed = True
                    return None
                if opcode == 0x9:      # ping -> pong
                    self._pong(payload)
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    return payload.decode("utf-8", errors="replace")
                # 其它控制帧忽略
        except socket.timeout:
            return None
        finally:
            if timeout is not None:
                try:
                    self.sock.settimeout(None)
                except OSError:
                    pass

    def _pong(self, payload: bytes) -> None:
        header = bytearray([0x8A])
        length = len(payload)
        header.append(0x80 | length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            try:
                self.sock.sendall(bytes(header) + masked)
            except OSError:
                pass

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# CDP 客户端
# --------------------------------------------------------------------------
class CDPClient:
    """极简 CDP 客户端：一个后台线程读消息，事件走回调，调用按 id 等待。"""

    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._event_cb: Optional[Callable[[dict], None]] = None
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def set_event_callback(self, cb: Callable[[dict], None]) -> None:
        self._event_cb = cb

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                msg = self.ws.recv(timeout=1.0)
            except (ConnectionError, OSError):
                break
            if msg is None:
                if self.ws.closed:
                    break
                continue
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if "id" in data:
                slot = self._pending.pop(data.get("id"), None)
                if slot is not None:
                    slot["result"] = data
                    slot["event"].set()
            elif self._event_cb:
                try:
                    self._event_cb(data)
                except Exception:
                    pass
        self._closed.set()
        for slot in list(self._pending.values()):
            slot["event"].set()

    def call(self, method: str, params: dict | None = None,
             timeout: float = 20.0) -> dict:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
        slot = {"event": threading.Event(), "result": None}
        self._pending[msg_id] = slot
        self.ws.send(json.dumps({"id": msg_id, "method": method,
                                 "params": params or {}}))
        if not slot["event"].wait(timeout):
            self._pending.pop(msg_id, None)
            return {"error": {"message": "调用超时", "method": method}}
        self._pending.pop(msg_id, None)
        return slot["result"] or {"error": {"message": "连接已断开"}}

    def close(self) -> None:
        self._closed.set()
        self.ws.close()


# --------------------------------------------------------------------------
# 浏览器实例
# --------------------------------------------------------------------------
class BrowserSession:
    """启动一个无头浏览器并跟踪其网络请求中的媒体资源。

    用法::

        with BrowserSession(ua="...", cookie="...") as bs:
            bs.open_page("https://example.com")
            bs.wait(seconds=8)          # 等页面跑起来
            for hit in bs.collect():
                ...
    """

    def __init__(self, browser_path: str = "", headless: bool = True,
                 user_agent: str = "", cookie: str = "", proxy: str = "",
                 direct: bool = False,
                 log: Optional[Callable[[str], None]] = None):
        self.browser_path = browser_path or find_browser()
        self.headless = headless
        self.user_agent = user_agent or USER_AGENT_FALLBACK
        self.cookie = cookie
        self.proxy = proxy
        # 强制直连：即使系统里配了代理，也让浏览器绕过它
        self.direct = bool(direct)
        self._log = log or (lambda _m: None)
        self.proc: Optional[subprocess.Popen] = None
        self.cdp: Optional[CDPClient] = None
        self.port = 0
        self._profile = ""
        self._hits: list[dict] = []
        self._hit_lock = threading.Lock()
        self._event_queue: queue.Queue = queue.Queue()
        self._page_ready = threading.Event()
        self.main_frame_id = ""
        self.document_url = ""
        self.page_title = ""
        self.cookies_from_browser: list[dict] = []
        self._closed = False

    # ------------------------------------------------------------ 生命周期
    @property
    def available(self) -> bool:
        return bool(self.browser_path and os.path.isfile(self.browser_path))

    def start(self) -> None:
        if not self.available:
            raise RuntimeError("没有找到可用的 Chromium 内核浏览器（Edge/Chrome）")
        self.port = random.randint(41000, 55000)
        self._profile = _make_temp_dir("rs_profile_")
        self._stderr_log = os.path.join(self._profile, "..",
                                        "rs_browser_stderr.log")
        self._stderr_log = os.path.normpath(self._stderr_log)
        args = [
            self.browser_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self._profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--mute-audio",
            "--autoplay-policy=no-user-gesture-required",
            "--window-size=1280,900",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
            args.insert(2, "--disable-gpu")
        if self.direct:
            args.insert(1, "--no-proxy-server")
        elif self.proxy:
            args.insert(1, f"--proxy-server={self.proxy}")
        if self.user_agent:
            args.insert(1, f"--user-agent={self.user_agent}")
        creation_flags = 0
        if os.name == "nt":
            creation_flags = 0x08000000  # CREATE_NO_WINDOW
        try:
            stderr_fh = open(self._stderr_log, "wb")
        except OSError:
            stderr_fh = subprocess.DEVNULL          # type: ignore[assignment]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=stderr_fh,
                                     creationflags=creation_flags)
        ws_url = self._wait_debugger(20.0)
        self.cdp = CDPClient(ws_url)
        self.cdp.set_event_callback(self._on_event)
        self._enable_domains()

    def _wait_debugger(self, timeout: float) -> str:
        import json
        import urllib.request

        # 调试端口是本机回环地址，绝不能走系统代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.time() + timeout
        backoff = 0.2
        last_err = ""
        while time.time() < deadline:
            try:
                with opener.open(f"http://127.0.0.1:{self.port}/json/list",
                                 timeout=2) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except (URLError, socket.timeout, OSError) as exc:
                last_err = type(exc).__name__       # 浏览器还没起来，正常
            except Exception as exc:                # 端口被别的程序占用了
                last_err = str(exc)
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.6, 1.0)
        if self.proc and self.proc.poll() is not None:
            detail = ""
            log_path = getattr(self, "_stderr_log", "")
            if log_path and os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                        detail = fh.read()[-400:].strip()
                except OSError:
                    detail = ""
            raise RuntimeError(
                "浏览器启动失败（退出码 "
                f"{self.proc.returncode}）"
                + (f"：{detail}" if detail else "")
            )
        raise RuntimeError(f"调试端口 {self.port} 未就绪（{last_err or '无响应'}）")

    def _enable_domains(self) -> None:
        assert self.cdp
        for method, params in (
            ("Network.enable", {"maxTotalBufferSize": 16 * 1024 * 1024,
                                "maxResourceBufferSize": 4 * 1024 * 1024}),
            ("Page.enable", {}),
            ("Runtime.enable", {}),
            ("Network.setCacheDisabled", {"cacheDisabled": True}),
        ):
            self.cdp.call(method, params, timeout=15)
        if self.user_agent:
            self.cdp.call("Network.setUserAgentOverride",
                          {"userAgent": self.user_agent}, timeout=10)
        if self.cookie:
            for part in self.cookie.replace("\n", ";").split(";"):
                if "=" not in part:
                    continue
                name, value = part.split("=", 1)
                self.cdp.call("Network.setCookie", {
                    "name": name.strip(), "value": value.strip(), "path": "/",
                }, timeout=5)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.cdp:
                self.cdp.call("Browser.close", timeout=3)
        except Exception:
            pass
        try:
            if self.cdp:
                self.cdp.close()
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self._cleanup_profile()

    def _cleanup_profile(self) -> None:
        import shutil

        if not self._profile:
            return
        for _ in range(3):
            try:
                shutil.rmtree(self._profile, ignore_errors=True)
                break
            except Exception:
                time.sleep(0.4)
        self._profile = ""

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 事件
    def _on_event(self, msg: dict) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if method == "Network.responseReceived":
            self._remember(params)
        elif method == "Network.requestWillBeSent":
            req = params.get("request") or {}
            if params.get("type") == "Document" and not self.document_url:
                self.document_url = req.get("url", "")
            if params.get("type") == "Media":
                self._queue("media-request", req.get("url", ""),
                            params.get("type", ""), 0, req.get("headers") or {})
        elif method == "Page.loadEventFired":
            self._page_ready.set()
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            if not frame.get("parentId"):
                self.main_frame_id = frame.get("id", "")
                self.document_url = frame.get("url", "") or self.document_url
        elif method == "Page.documentOpened":
            self._page_ready.set()
        elif method == "Network.loadingFailed":
            pass
        del method

    def _queue(self, kind: str, url: str, rtype: str, status: int,
               headers: dict) -> None:
        if not url or url.startswith(("data:", "blob:", "about:")):
            return
        hit = {"url": url, "type": rtype, "status": status, "headers": headers}
        with self._hit_lock:
            self._hits.append(hit)
        self._event_queue.put(hit)

    def _remember(self, params: dict) -> None:
        resp = params.get("response") or {}
        url = resp.get("url", "")
        if not url or url.startswith(("data:", "blob:")):
            return
        headers = resp.get("headers") or {}
        headers = {k.lower(): v for k, v in headers.items()}
        self._queue("response", url, params.get("type", ""),
                    int(resp.get("status") or 0), headers)
        del params

    # ---------------------------------------------------------------- 页面
    def open_page(self, url: str, wait_load: float = 12.0) -> None:
        assert self.cdp
        self._page_ready.clear()
        res = self.cdp.call("Page.navigate", {"url": url}, timeout=20)
        if res.get("error"):
            raise RuntimeError("打开页面失败: " + str(res["error"].get("message")))
        self._page_ready.wait(wait_load)

    def scroll_to_bottom(self, steps: int = 4, delay: float = 0.7) -> None:
        """滚动页面触发懒加载，这往往是「多抓图」的关键。"""
        assert self.cdp
        for i in range(steps):
            script = (
                "window.scrollTo(0, document.body.scrollHeight * %f);"
                "Array.from(document.querySelectorAll('img')).forEach(function(im){"
                "  if(im.dataset && im.dataset.src && !im.src){im.src=im.dataset.src;}"
                "  if(im.loading==='lazy'){im.loading='eager';}"
                "});"
                "var v=document.querySelectorAll('video');"
                "for(var i=0;i<v.length;i++){try{v[i].muted=true;v[i].play();}catch(e){}}"
            ) % ((i + 1) / steps)
            try:
                self.cdp.call("Runtime.evaluate",
                              {"expression": script, "awaitPromise": False},
                              timeout=8)
            except Exception:
                pass
            time.sleep(delay)

    def wait(self, seconds: float, poll_cb: Optional[Callable[[dict], None]] = None) -> None:
        """在 seconds 秒内持续消费事件队列。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            try:
                hit = self._event_queue.get(timeout=min(0.35, remaining + 0.01))
            except queue.Empty:
                continue
            if poll_cb:
                try:
                    poll_cb(hit)
                except Exception:
                    pass

    def drain(self, poll_cb: Optional[Callable[[dict], None]] = None) -> None:
        while True:
            try:
                hit = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if poll_cb:
                try:
                    poll_cb(hit)
                except Exception:
                    pass

    # ------------------------------------------------------------- 扩展能力
    def get_document_html(self) -> str:
        assert self.cdp
        res = self.cdp.call("Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        }, timeout=15)
        value = (((res.get("result") or {}).get("result") or {}).get("value") or "")
        return value

    def get_page_title(self) -> str:
        assert self.cdp
        res = self.cdp.call("Runtime.evaluate", {
            "expression": "document.title", "returnByValue": True}, timeout=10)
        return (((res.get("result") or {}).get("result") or {}).get("value") or "")

    def save_screenshot(self, path: str) -> bool:
        assert self.cdp
        res = self.cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=20)
        data = ((res.get("result") or {}).get("data") or "")
        if not data:
            return False
        try:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))
            return True
        except OSError:
            return False

    def collect_html_media(self) -> list[str]:
        """从 DOM 里再捞一遍 src/data-src/poster 等属性。"""
        assert self.cdp
        script = """
        (function(){
          var out = [];
          var push = function(v){ if(v && typeof v === 'string'){ out.push(v); } };
          document.querySelectorAll('img,video,audio,source,embed,object,iframe').forEach(function(el){
            push(el.currentSrc); push(el.src); push(el.getAttribute('data-src'));
            push(el.getAttribute('data-original')); push(el.getAttribute('data-lazy-src'));
            push(el.getAttribute('poster')); push(el.getAttribute('href'));
            var ss = el.querySelectorAll ? el.querySelectorAll('source') : [];
            for (var i=0;i<ss.length;i++){ push(ss[i].src); push(ss[i].getAttribute('src')); }
            var srcset = el.getAttribute && el.getAttribute('srcset');
            if (srcset) {
              srcset.split(',').forEach(function(part){
                var u = part.trim().split(/\\s+/)[0]; push(u);
              });
            }
          });
          document.querySelectorAll('[style*="background"]').forEach(function(el){
            var m = /url\\((['"]?)(.*?)\\1\\)/.exec(el.getAttribute('style') || '');
            if (m) { push(m[2]); }
          });
          var perf = performance.getEntriesByType('resource') || [];
          perf.forEach(function(e){ push(e.name); });
          return out;
        })()
        """
        res = self.cdp.call("Runtime.evaluate", {
            "expression": script, "returnByValue": True, "awaitPromise": False,
        }, timeout=20)
        values = (((res.get("result") or {}).get("result") or {}).get("value") or [])
        return [v for v in values if isinstance(v, str)]

    def media_urls(self) -> list[str]:
        with self._hit_lock:
            return [h["url"] for h in self._hits]
