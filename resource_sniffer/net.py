"""极简 HTTP/1.1 客户端（仅用标准库）。

为什么不用 requests / urllib：
* 资源嗅探需要「只取前 N 字节」「按 Range 取片段」「只读响应头不下载正文」，
  urllib 会把整个响应读完，对几百 MB 的视频不可接受；
* 需要可中断的流式下载与断点续传；
* 保证整个程序除 Pillow（可选）外零第三方依赖。

支持：重定向、gzip/deflate 解压、分块传输、HTTP 代理（含 HTTPS CONNECT）、
超时、Range、Cookie 自动保存。
"""

from __future__ import annotations

import base64
import http.client
import os
import random
import socket
import ssl
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15",
]

MEDIA_ACCEPT = ("image/avif,image/webp,image/apng,image/svg+xml,image/*,"
                "video/*,audio/*,*/*;q=0.8")
HTML_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8")

REDIRECT_CODES = (301, 302, 303, 307, 308)


class NetworkError(Exception):
    """网络层错误（连接失败、超时、TLS 错误等）。"""


class HttpStatusError(NetworkError):
    """服务器返回 4xx / 5xx。"""

    def __init__(self, code: int, reason: str, url: str):
        super().__init__(f"HTTP {code} {reason}".strip())
        self.code = code
        self.reason = reason
        self.url = url


class DownloadCancelled(Exception):
    """下载被用户取消。"""


@dataclass
class Response:
    url: str
    status: int = 0
    reason: str = ""
    headers: dict = field(default_factory=dict)   # key 全部小写
    body: bytes = b""
    truncated: bool = False
    redirect_chain: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    def mime(self) -> str:
        return self.content_type

    @property
    def charset(self) -> str:
        raw = self.headers.get("content-type") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                cs = part.split("=", 1)[1].strip().strip("\"'")
                if cs:
                    return cs
        return "utf-8"

    @property
    def length(self) -> int:
        try:
            return int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def content_range(self) -> tuple[int, int, int]:
        raw = self.headers.get("content-range") or ""
        try:
            unit, rest = raw.split(" ", 1)
            if unit.lower() != "bytes":
                return 0, 0, 0
            span, total = rest.split("/", 1)
            start, end = span.split("-", 1)
            return int(start), int(end), (0 if total.strip() == "*" else int(total))
        except Exception:
            return 0, 0, 0

    def total_size(self) -> int:
        _s, _e, total = self.content_range
        return total or self.length

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def text(self) -> str:
        try:
            return self.body.decode(self.charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def normalize_url(raw: str) -> str:
    """把用户输入整理成合法 URL。

    * 去掉首尾空白与包裹的引号；
    * 缺协议时补 http://（本机地址也适用）；
    * 单独的域名补结尾 "/"；
    * 其它协议（magnet:、thunder: 等）原样返回。
    """
    url = (raw or "").strip().strip("\"'").strip()
    if not url:
        return ""
    low = url.lower()
    if low.startswith(("http://", "https://", "ftp://", "file://")):
        pass
    elif "://" in url:
        return url                      # 其它协议不处理
    else:
        url = "http://" + url
    parts = urlsplit(url)
    if not parts.netloc:
        return ""
    if not parts.path:
        url += "/"
    return url


def _resolve_proxy(url: str) -> str:
    env = os.environ
    scheme = urlsplit(url).scheme.lower()
    primary = (["HTTPS_PROXY", "https_proxy"] if scheme == "https"
               else ["HTTP_PROXY", "http_proxy"])
    rest = ["ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
            "HTTP_PROXY", "http_proxy"]
    for key in primary + rest:
        value = (env.get(key) or "").strip()
        if value:
            return value
    return ""


def system_proxy(url: str) -> str:
    """返回系统环境变量里为 url 配置的代理（没有则空串）。

    单独暴露成公开函数，给「智能代理」判断系统代理是否可用；设置页里
    留空代理时仍然沿用系统代理，不会破坏原有使用习惯。
    """
    return _resolve_proxy(url)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass
    return ctx


_SSL = _ssl_context()


def _decompress(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower().strip()
    if not data or not enc or enc == "identity":
        return data
    try:
        if enc == "gzip":
            return zlib.decompress(data, 16 + zlib.MAX_WBITS)
        if enc == "deflate":
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
        if enc == "br":
            try:
                import brotli  # type: ignore

                return brotli.decompress(data)
            except Exception:
                return data
    except Exception:
        return data
    return data


def build_headers(referer: str, extra: dict | None, accept: str,
                  user_agent: str = "", profile_headers: dict | None = None) -> dict:
    """构造请求头。

    user_agent 非空时使用指定 UA（伪装），profile_headers 附带
    sec-ch-ua 之类的浏览器客户端提示头；为空则用内置随机桌面 UA。
    """
    headers = {
        "User-Agent": user_agent or random.choice(USER_AGENTS),
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if user_agent and profile_headers:
        for key, value in profile_headers.items():
            if key.lower() != "user-agent" and value:
                headers[key] = value
    if referer:
        headers["Referer"] = referer
    for k, v in (extra or {}).items():
        if v is not None:
            headers[k] = v
    return headers


def _host_header(url: str) -> str:
    """URL 对应的 Host 头（去掉可能的 user:pass@）。"""
    return urlsplit(url).netloc.rsplit("@", 1)[-1]


def _ensure_host(headers: dict, url: str) -> None:
    """HTTP 代理使用 absolute-form 请求行时，http.client 会把 Host 设成代理地址，
    因此显式写入目标站点的 Host（直连时与自动生成的值一致，不影响原行为）。"""
    if not any(key.lower() == "host" for key in headers):
        host = _host_header(url)
        if host:
            headers["Host"] = host


def _norm_headers(raw) -> dict:
    """把 http.client 的 header 列表转成 dict；重复的 set-cookie 用 \\n 合并。"""
    out: dict[str, str] = {}
    for key, value in raw:
        key = key.lower()
        if key in out:
            sep = "\n" if key == "set-cookie" else ", "
            out[key] = out[key] + sep + value
        else:
            out[key] = value
    return out


class HttpSession:
    """带 Cookie / 代理 / 域名级限速的 HTTP 会话，可多线程共用。"""

    def __init__(self, timeout: float = 15.0, proxy: str = "", cookie: str = "",
                 user_agent: str = "", profile_headers: dict | None = None,
                 direct: bool = False, env_proxy: bool = True):
        self.timeout = timeout
        self.proxy = (proxy or "").strip()
        # direct=True：强制不走代理（连系统环境变量里的代理也不用）。
        # 智能代理选中「直连」时必须这样，否则环境代理会让「无代理」名不副实。
        self.direct = bool(direct)
        # env_proxy=False：没填显式代理时也不读 HTTP(S)_PROXY（测速用）。
        self.env_proxy = bool(env_proxy)
        self._cookies: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()
        self.domain_delay = 0.12                 # 同域名最小请求间隔
        self._domain_last: dict[str, float] = {}
        self._raw_cookie = (cookie or "").strip()
        self._delay_lock = threading.Lock()
        # UA 伪装：为空时每个请求随机挑一个桌面 UA
        self.user_agent = (user_agent or "").strip()
        self.profile_headers = dict(profile_headers or {})

    # ------------------------------------------------------------- cookie
    def _store_cookies(self, url: str, headers: dict) -> None:
        raw = headers.get("set-cookie") or ""
        if not raw:
            return
        host = urlsplit(url).hostname or ""
        with self._lock:
            jar = self._cookies.setdefault(host, {})
            for entry in raw.split("\n"):
                first = entry.split(";", 1)[0].strip()
                if "=" in first:
                    k, v = first.split("=", 1)
                    if k.strip():
                        jar[k.strip()] = v.strip()

    def _cookie_header(self, url: str) -> str:
        host = urlsplit(url).hostname or ""
        with self._lock:
            jar = self._cookies.get(host) or {}
            parts = [f"{k}={v}" for k, v in jar.items()]
        if self._raw_cookie:
            parts.append(self._raw_cookie)
        return "; ".join(parts)

    def clear_cookies(self) -> None:
        with self._lock:
            self._cookies.clear()

    def set_cookie(self, raw: str) -> None:
        self._raw_cookie = (raw or "").strip()

    # -------------------------------------------------------------- 限速
    def _throttle(self, host: str) -> None:
        if self.domain_delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._domain_last.get(host, 0.0)
            wait = max(0.0, last + self.domain_delay - now)
            self._domain_last[host] = max(now, last + self.domain_delay)
        if wait > 0:
            time.sleep(min(wait, 3.0))

    # -------------------------------------------------------------- 连接
    def _open(self, url: str, timeout: float | None = None):
        """按 URL 建立连接，返回 (conn, path)；timeout 为空时用会话默认值。"""
        parts = urlsplit(url)
        scheme = (parts.scheme or "http").lower()
        if scheme not in ("http", "https"):
            raise NetworkError(f"不支持的协议: {scheme or '(空)'}")
        host = parts.hostname or ""
        port = parts.port or (443 if scheme == "https" else 80)
        path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        timeout = timeout or self.timeout
        if self.direct:
            proxy = ""
        elif self.proxy:
            proxy = self.proxy
        elif self.env_proxy:
            proxy = _resolve_proxy(url)
        else:
            proxy = ""
        if not proxy:
            if scheme == "https":
                return http.client.HTTPSConnection(host, port, timeout=timeout,
                                                   context=_SSL), path
            return http.client.HTTPConnection(host, port, timeout=timeout), path
        pparts = urlsplit(proxy if "://" in proxy else "http://" + proxy)
        p_host, p_port = pparts.hostname or "", pparts.port or 8080
        if scheme == "https":
            sock = socket.create_connection((p_host, p_port), timeout=timeout)
            self._proxy_tunnel(sock, pparts, host, port)
            conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                               context=_SSL)
            conn.sock = _SSL.wrap_socket(sock, server_hostname=host)
            return conn, path
        conn = http.client.HTTPConnection(p_host, p_port, timeout=timeout)
        return conn, url          # 明文代理使用 absolute-form 请求行

    @staticmethod
    def _proxy_tunnel(sock, pparts, host: str, port: int) -> None:
        auth = ""
        if pparts.username:
            token = f"{unquote(pparts.username)}:{unquote(pparts.password or '')}"
            auth = "Proxy-Authorization: Basic " + base64.b64encode(
                token.encode()).decode() + "\r\n"
        req = (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"{auth}Proxy-Connection: keep-alive\r\n\r\n")
        sock.sendall(req.encode("latin-1"))
        fp = sock.makefile("rb")
        status = fp.readline(2048)
        if b" 200" not in status:
            sock.close()
            raise NetworkError("代理 CONNECT 失败: " + status.decode("latin-1").strip())
        while True:
            line = fp.readline(4096)
            if line in (b"\r\n", b"\n", b""):
                break

    # -------------------------------------------------------------- 请求
    def request(self, url: str, method: str = "GET", referer: str = "",
                headers: dict | None = None, max_bytes: int | None = None,
                timeout: float | None = None, follow_redirects: int = 6,
                want_body: bool = True, accept: str = MEDIA_ACCEPT) -> Response:
        """执行请求并返回 Response。

        max_bytes: 最多读取正文前 N 字节后主动断开（用于嗅探和预览）。
        want_body: False 时只取响应头，不下载正文。
        """
        effective_timeout = timeout or self.timeout
        chain: list[str] = []
        current = url
        for hop in range(follow_redirects + 1):
            resp = self._once(current, method, referer, headers, max_bytes,
                              want_body, accept, effective_timeout)
            if resp.status in REDIRECT_CODES and resp.header("location"):
                nxt = urljoin(current, resp.header("location"))
                chain.append(nxt)
                if hop >= follow_redirects:
                    resp.redirect_chain = chain
                    return resp
                current = nxt
                continue
            resp.redirect_chain = chain
            return resp
        raise NetworkError("重定向次数过多")

    def _once(self, url: str, method: str, referer: str, extra: dict | None,
              max_bytes: int | None, want_body: bool, accept: str,
              timeout: float | None = None) -> Response:
        hdrs = build_headers(referer, extra, accept, self.user_agent,
                             self.profile_headers)
        cookie = self._cookie_header(url)
        if cookie:
            hdrs.setdefault("Cookie", cookie)
        _ensure_host(hdrs, url)
        host = urlsplit(url).hostname or ""
        self._throttle(host)
        try:
            conn, path = self._open(url, timeout)
            try:
                conn.request(method, path, headers=hdrs)
                raw = conn.getresponse()
                resp = Response(url=url, status=raw.status, reason=raw.reason or "")
                resp.headers = _norm_headers(raw.getheaders())
                self._store_cookies(url, resp.headers)
                if not want_body or method == "HEAD" or raw.status in REDIRECT_CODES:
                    return resp
                chunks: list[bytes] = []
                got = 0
                while True:
                    need = 65536
                    if max_bytes is not None:
                        need = min(need, max_bytes - got)
                        if need <= 0:
                            resp.truncated = True
                            break
                    block = raw.read(need)
                    if not block:
                        break
                    chunks.append(block)
                    got += len(block)
                body = b"".join(chunks)
                resp.body = _decompress(body, resp.header("content-encoding"))
                if max_bytes is not None and got >= max_bytes:
                    declared = resp.length
                    if not declared or declared > got:
                        resp.truncated = True
                return resp
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except (HttpStatusError, NetworkError):
            raise
        except socket.timeout as exc:
            raise NetworkError(f"连接超时: {host}") from exc
        except ssl.SSLError as exc:
            raise NetworkError(f"TLS 错误: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    # ---------------------------------------------------------- 便捷方法
    def get(self, url: str, **kwargs) -> Response:
        return self.request(url, **kwargs)

    def get_page(self, url: str, referer: str = "",
                 max_bytes: int = 8 << 20) -> Response:
        """取 HTML 页面。"""
        return self.request(url, referer=referer, max_bytes=max_bytes,
                            accept=HTML_ACCEPT, follow_redirects=8)

    def head_info(self, url: str, referer: str = "") -> Response:
        """只拿响应头（不下载正文）。"""
        return self.request(url, referer=referer, want_body=False,
                            follow_redirects=8)

    def peep(self, url: str, referer: str = "",
             nbytes: int = 64 * 1024) -> Response:
        """只取前 nbytes 字节。"""
        return self.request(url, referer=referer, max_bytes=nbytes,
                            follow_redirects=8)

    # -------------------------------------------------------------- 下载
    def download_file(self, url: str, filepath: str, referer: str = "",
                      progress=None, cancel=None, chunk_size: int = 256 * 1024,
                      headers: dict | None = None,
                      resume: bool = True, segments: int = 1) -> tuple[int, str]:
        """流式下载到文件，支持断点续传、进度回调与取消。

        progress(done_total_bytes, total_bytes)；cancel() 返回 True 时中断。
        segments > 1 时，大文件会尝试多连接 Range 分段下载（服务器支持时）。
        返回 (文件字节数, 最终 URL)。
        """
        existing = 0
        if resume and os.path.exists(filepath):
            existing = os.path.getsize(filepath)
        req_headers = dict(headers or {})
        if existing:
            req_headers["Range"] = f"bytes={existing}-"
        info = self.request(url, referer=referer, headers=req_headers,
                            want_body=False, follow_redirects=8)
        final_url = info.url
        if info.status >= 400:
            raise HttpStatusError(info.status, info.reason or "", final_url)
        if existing and info.status == 416:
            return existing, final_url          # 已经下完
        if existing and info.status != 206:
            existing = 0                        # 服务器不支持续传，重来
        total = info.total_size()
        if info.status == 206:
            _s, _e, t = info.content_range
            total = t or total
        supports_ranges = "bytes" in (info.header("accept-ranges") or "").lower()
        if (segments > 1 and not existing and total >= 8 * 1024 * 1024
                and supports_ranges):
            try:
                return self._stream_segmented(
                    final_url, referer, headers, filepath, total, progress,
                    cancel, chunk_size, min(8, segments))
            except DownloadCancelled:
                raise
            except (NetworkError, OSError, http.client.HTTPException):
                # 某些 CDN 声明支持 Range 却返回 200；删除半成品后走单连接
                try:
                    os.remove(filepath)
                except OSError:
                    pass
        return self._stream(final_url, referer, req_headers, filepath, existing,
                            total, progress, cancel, chunk_size)

    def _stream_segmented(self, url: str, referer: str, headers: dict | None,
                          filepath: str, total: int, progress, cancel,
                          chunk_size: int, segments: int) -> tuple[int, str]:
        """大文件多连接 Range 下载：每段独立连接、独立文件句柄，按偏移写入。"""
        segment_count = max(1, min(segments, total // (2 * 1024 * 1024)))
        span = (total + segment_count - 1) // segment_count
        ranges = []
        for index in range(segment_count):
            start = index * span
            end = min(total - 1, start + span - 1)
            if start <= end:
                ranges.append((start, end))
        if not ranges:
            raise NetworkError("无法计算分段范围")
        with open(filepath, "wb") as fh:
            fh.truncate(total)
        done = 0
        done_lock = threading.Lock()
        stop = threading.Event()
        first_error: list[BaseException] = []

        def worker(start: int, end: int) -> None:
            nonlocal done
            if stop.is_set() or (cancel and cancel()):
                return
            hdrs = build_headers(referer, headers, "*/*", self.user_agent,
                                 self.profile_headers)
            hdrs["Accept-Encoding"] = "identity"
            hdrs["Range"] = f"bytes={start}-{end}"
            cookie = self._cookie_header(url)
            if cookie:
                hdrs.setdefault("Cookie", cookie)
            _ensure_host(hdrs, url)
            conn = None
            try:
                conn, path = self._open(url)
                conn.request("GET", path, headers=hdrs)
                raw = conn.getresponse()
                if raw.status != 206:
                    raise NetworkError(f"分段请求未返回 206（HTTP {raw.status}）")
                content_range = raw.getheader("Content-Range") or ""
                try:
                    got_start = int(content_range.split()[1].split("-")[0])
                except Exception:
                    got_start = start
                if got_start != start:
                    raise NetworkError("服务器返回的分段范围不一致")
                pos = start
                remaining = end - start + 1
                with open(filepath, "r+b") as fh:
                    while remaining > 0:
                        if stop.is_set() or (cancel and cancel()):
                            raise DownloadCancelled()
                        block = raw.read(min(chunk_size, remaining))
                        if not block:
                            raise NetworkError(f"分段提前结束（{pos}/{end}）")
                        fh.seek(pos)
                        fh.write(block)
                        pos += len(block)
                        remaining -= len(block)
                        with done_lock:
                            done += len(block)
                            if progress:
                                progress(done, total)
            except BaseException as exc:       # 保存首个错误，通知其它分段退出
                if not first_error:
                    first_error.append(exc)
                stop.set()
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        pool = ThreadPoolExecutor(max_workers=len(ranges),
                                  thread_name_prefix="dl-seg")
        futures = [pool.submit(worker, start, end) for start, end in ranges]
        try:
            for future in futures:
                future.result()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if first_error:
            try:
                os.remove(filepath)
            except OSError:
                pass
            raise first_error[0]
        if cancel and cancel():
            raise DownloadCancelled()
        if done != total:
            try:
                os.remove(filepath)
            except OSError:
                pass
            raise NetworkError(f"分段下载不完整（{done}/{total}）")
        if progress:
            progress(total, total)
        return total, url

    def _stream(self, url, referer, headers, filepath, start_offset, total,
                progress, cancel, chunk_size) -> tuple[int, str]:
        hdrs = build_headers(referer, headers, "*/*", self.user_agent,
                             self.profile_headers)
        hdrs["Accept-Encoding"] = "identity"
        cookie = self._cookie_header(url)
        if cookie:
            hdrs.setdefault("Cookie", cookie)
        _ensure_host(hdrs, url)
        conn, path = self._open(url)
        try:
            conn.request("GET", path, headers=hdrs)
            raw = conn.getresponse()
            if raw.status >= 400:
                raise HttpStatusError(raw.status, raw.reason or "", url)
            append = bool(start_offset) and raw.status == 206
            done = start_offset if append else 0
            if not total:
                try:
                    total = int(raw.getheader("Content-Length") or 0) + done
                except (TypeError, ValueError):
                    total = 0
            folder = os.path.dirname(os.path.abspath(filepath))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(filepath, "ab" if append else "wb",
                      buffering=1024 * 1024) as fh:
                while True:
                    if cancel and cancel():
                        raise DownloadCancelled()
                    block = raw.read(chunk_size)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total or done)
            if progress:
                progress(done, total or done)
            if total and done < total:
                # 服务端提前断开：交给上层重试（已写入部分会续传）
                raise NetworkError(f"连接提前中断（{done}/{total} 字节）")
            return done, url
        finally:
            try:
                conn.close()
            except Exception:
                pass
