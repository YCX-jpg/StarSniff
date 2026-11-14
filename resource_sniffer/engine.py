"""嗅探引擎。

一次「开始嗅探」的流程：

    输入 URL
      ├─ 是直链媒体？→ 直接探测并放到列表
      └─ 是网页？
           ├─ 静态嗅探：GET HTML → 解析 <img>/<video>/<source>/srcset/poster/
           │           内联脚本/CSS/og 标签 → 媒体候选
           ├─ 样式与脚本：抓 CSS 里的 url()、抓 JS 里的直链
           └─ 动态嗅探（可选）：无头 Chromium 真实渲染页面，
                        Network 域 + DOM 双通道抓取异步资源
    候选去重 → 并发探测元数据（大小/分辨率/时长/码率/缩略图）→ 回调逐条上屏
    流媒体清单（m3u8/mpd）→ 二次解析出各清晰度，再作为新条目加入
"""

from __future__ import annotations

import concurrent.futures as futures
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

from . import htmlparse, smartproxy, streams, useragents
from .browser import BrowserSession, find_browser
from .mediainfo import (AUDIO_EXT, IMAGE_EXT, STREAM_EXT, VIDEO_EXT, MediaProbe,
                        ext_from_url, ext_of, kind_from_ext, kind_from_mime,
                        looks_like_media_url, probe, variant_key)
from .models import ResourceItem, ResourceList
from .net import HttpSession, NetworkError

MEDIA_KINDS = ("image", "audio", "video", "stream")

# CDP 里这些网络请求类型直接视为媒体
_NETWORK_MEDIA_TYPES = {"Image", "Media", "Video", "Audio"}


@dataclass
class SniffOptions:
    """嗅探参数。"""

    deep: bool = True              # 解析 CSS / JS / JSON 里的直链
    dynamic: bool = True           # 用无头浏览器动态嗅探
    dynamic_wait: float = 8.0      # 页面加载后等待秒数（等异步资源出现）
    scroll: bool = True            # 自动滚动触发懒加载
    max_items: int = 1500          # 上限，防止超大图库卡死
    min_image_side: int = 0        # 过滤小于该边长的图片（0 = 不过滤）
    kinds: tuple = MEDIA_KINDS     # 只保留哪些类型
    expand_manifests: bool = True  # m3u8/mpd 展开成各清晰度
    workers: int = 10              # 并发探测线程数
    browser_path: str = ""
    headless: bool = True
    timeout: float = 15.0
    probe_deadline: float = 35.0  # 元数据探测总时限（秒），超时先出结果
    ua_key: str = "auto"           # User-Agent 伪装配置（见 useragents.PROFILES）


@dataclass
class SniffContext:
    """一次嗅探任务的可取消上下文。"""

    session: HttpSession
    options: SniffOptions
    cancel_event: threading.Event = field(default_factory=threading.Event)
    log: Callable[[str], None] = lambda _m: None
    proxy: str = ""                            # 设置里的代理地址
    proxy_mode: str = smartproxy.AUTO          # auto / proxy / direct
    route: Optional[smartproxy.Route] = None   # 本次选中的线路

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class Sniffer:
    """嗅探引擎。UI 只依赖它抛出的回调，不关心内部实现。"""

    def __init__(self, on_item: Optional[Callable[[ResourceItem], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_progress: Optional[Callable[[str], None]] = None,
                 on_done: Optional[Callable[[dict], None]] = None,
                 browser_factory=None,
                 on_remove: Optional[Callable[[ResourceItem], None]] = None):
        self.on_item = on_item or (lambda _i: None)
        # 去重/合并时通知界面移除已上屏的条目
        self.on_remove = on_remove or (lambda _i: None)
        self.on_log = on_log or (lambda _m: None)
        self.on_progress = on_progress or (lambda _m: None)
        self.on_done = on_done or (lambda _s: None)
        # 可注入的浏览器工厂（测试时可替换为模拟实现）
        self.browser_factory = browser_factory or BrowserSession
        self.resources = ResourceList()
        self._thread: Optional[threading.Thread] = None
        self._ctx: Optional[SniffContext] = None
        self._browser = None
        # 最近一次嗅探选中的线路（供下载复用；未嗅探时为 None）
        self.route: Optional[smartproxy.Route] = None

    # ------------------------------------------------------------- 生命周期
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, url: str, options: SniffOptions | None = None,
              proxy: str = "", cookie: str = "",
              proxy_mode: str = smartproxy.AUTO) -> None:
        assert not self.running, "已有嗅探任务在运行"
        opts = options or SniffOptions()
        if not opts.browser_path:
            opts.browser_path = find_browser()
        self.resources.clear()
        self.route = None
        profile = useragents.get(opts.ua_key)
        # 先禁用代理，等 _run 里测速选好线路后再应用，避免选路前误走默认线路
        session = HttpSession(timeout=opts.timeout, proxy="", cookie=cookie,
                              user_agent=profile.ua if profile.ua else "",
                              profile_headers=profile.headers() if profile.ua else None,
                              direct=True, env_proxy=False)
        self._ctx = SniffContext(session=session, options=opts, log=self.on_log,
                                 proxy=proxy, proxy_mode=proxy_mode)
        self._thread = threading.Thread(target=self._run, args=(url, self._ctx),
                                        daemon=True, name="sniffer")
        self._thread.start()

    def stop(self) -> None:
        if self._ctx:
            self._ctx.cancel_event.set()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ 线路
    def _select_route(self, url: str, ctx: SniffContext) -> smartproxy.Route:
        """嗅探开始前测速选择直连/代理；任何异常都退回可用线路，绝不阻断嗅探。"""
        self.on_progress("正在测速选择线路…")
        timeout = min(4.0, max(1.5, float(ctx.options.timeout)))
        try:
            route = smartproxy.choose(
                url, mode=ctx.proxy_mode, proxy=ctx.proxy, timeout=timeout,
                log=self.on_log, cancel=ctx.cancelled)
        except Exception as exc:                    # 兜底：测速本身不能拖垮嗅探
            self.on_log(f"[线路] 测速失败（{exc}），按设置直接开始")
            route = smartproxy.quick_route(ctx.proxy_mode, ctx.proxy, url)
        smartproxy.apply_route(ctx.session, route)
        self.route = route
        self.on_log(f"[线路] 本次使用：{route.label}（{route.reason}）")
        return route

    # ------------------------------------------------------------------ 主流程
    def _run(self, url: str, ctx: SniffContext) -> None:
        summary = {"page": url, "title": "", "count": 0, "elapsed": 0.0,
                   "canceled": False, "error": "", "dynamic": False,
                   "browser_missing": False, "login_wall": False}
        route = self._select_route(url, ctx)
        summary["route"] = route.to_dict()
        started = time.time()
        candidates: dict[str, dict] = {}   # url -> {"source": "network"}

        def add_candidate(raw_url: str, source: str, headers: Optional[dict] = None,
                          force: bool = False) -> None:
            u = (raw_url or "").strip()
            if not u or u.startswith(("data:", "blob:", "javascript:", "about:")):
                return
            if not u.startswith(("http://", "https://")):
                return
            if u in candidates:
                return
            if not force and not _looks_interesting(u):
                return
            candidates[u] = {"source": source, "headers": headers or {}}

        try:
            # ---- 0. 直链资源？
            direct = _is_direct_media_url(url)
            if direct:
                add_candidate(url, "direct", force=True)
                summary["title"] = url.rsplit("/", 1)[-1]
            # ---- 1. 抓页面
            else:
                html, final_url, title = self._fetch_page(url, ctx, summary)
                if summary.get("direct_body"):
                    # 输入的是媒体直链（没有扩展名之类的情况）
                    add_candidate(final_url, "direct", force=True)
                    direct = True
                    summary["title"] = summary["title"] or final_url.rsplit("/", 1)[-1]
                elif html is None:
                    summary["error"] = summary.get("error") or "页面获取失败"
                else:
                    summary["title"] = title
                    self._static_extract(html, final_url, ctx, add_candidate,
                                         summary)

            # ---- 2. 动态嗅探（无头浏览器）
            if not ctx.cancelled() and ctx.options.dynamic and not direct:
                self._dynamic_extract(url, ctx, add_candidate, summary, candidates)

            # ---- 3. 并发探测元数据
            items = self._probe_all(candidates, url, ctx)
            # ---- 3.5 合并同一张图的多个尺寸/格式变体
            items = self._collapse_image_variants(items)
            summary["count"] = len(items)

            # ---- 4. 展开流媒体清单
            if ctx.options.expand_manifests and not ctx.cancelled():
                self._expand_manifests(items, ctx)

            # ---- 5. 过滤（类型、最小尺寸）
            if ctx.options.kinds != MEDIA_KINDS:
                self.on_progress("按类型过滤…")
        except Exception as exc:                       # 兜底，绝不让线程炸掉 UI
            summary["error"] = f"{type(exc).__name__}: {exc}"
            self.on_log(f"[错误] {summary['error']}")
        finally:
            summary["canceled"] = ctx.cancelled()
            summary["elapsed"] = time.time() - started
            summary["total"] = len(self.resources)
            self.on_done(summary)

    # ------------------------------------------------------------------ 页面
    def _fetch_page(self, url: str, ctx: SniffContext,
                    summary: dict) -> tuple[Optional[str], str, str]:
        self.on_progress("正在获取页面…")
        try:
            resp = ctx.session.get_page(url)
        except NetworkError as exc:
            self.on_log(f"[页面] 获取失败: {exc}")
            summary["error"] = str(exc)
            return None, url, ""
        final_url = resp.url or url
        if not resp.ok:
            self.on_log(f"[页面] HTTP {resp.status} {resp.reason}")
        ctype = resp.content_type
        body = resp.body
        if not body:
            return None, final_url, ""
        # 如果链接本身是媒体文件
        if not any(t in ctype for t in ("html", "xml", "text")) or ctype == "":
            probe_res = probe(body[:65536], resp.body[-65536:] if len(body) > 65536 else b"",
                              len(body), ctype, ext_of(final_url))
            if probe_res.is_media:
                self.on_log("[页面] 这是媒体直链")
                summary["direct_body"] = body          # 供 probe_all 复用
                summary["direct_ctype"] = ctype
                return None, final_url, ""
        text = body.decode(resp.charset, errors="replace") if isinstance(body, bytes) else str(body)
        title = ""
        try:
            import re

            match = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            if match:
                import html as _h

                title = _h.unescape(match.group(1)).strip()
        except Exception:
            pass
        # 登录墙/同意页识别：这类页面几乎不会内联媒体，需要用户提供 Cookie
        lowered = text[:200000].lower()
        # 只匹配「明确表示内容被登录挡住」的措辞，避免普通导航里的“登录”误报
        markers = ("sign in to x", "log in to x", "join x now",
                   "sign in to continue", "log in to continue",
                   "you must be logged in", "需要登录后", "登录后查看",
                   "请先登录", "登录以继续", "登录以查看")
        summary["login_wall"] = any(m in lowered for m in markers)
        if summary["login_wall"]:
            self.on_log("[页面] 该页面可能要求登录，若抓不到资源请在设置里填入 Cookie")
        self.on_log(f"[页面] 已获取 {len(body) / 1024:.0f} KB，标题：{title or '(无)'}")
        return text, final_url, title

    def _static_extract(self, html: str, base_url: str, ctx: SniffContext,
                        add_candidate, summary: dict) -> None:
        self.on_progress("解析页面内容…")
        found = htmlparse.extract_from_html(html, base_url, want_all_links=True)
        groups = {"images": "html", "audio": "html", "video": "html",
                  "streams": "html", "other": "html"}
        total = 0
        for bucket in groups:
            for u in found.get(bucket, []):
                add_candidate(u, "html")
                total += 1
        self.on_log(f"[页面] 静态发现 {total} 个候选资源")

        if not ctx.options.deep:
            return
        # CSS
        styles = list(dict.fromkeys(found.get("css", [])))[:12]
        for css_url in styles:
            if ctx.cancelled():
                return
            try:
                resp = ctx.session.peep(css_url, referer=base_url, nbytes=2 << 20)
                if not resp.ok or not resp.body:
                    continue
                css_found = htmlparse.extract_from_css(
                    resp.body.decode(resp.charset, errors="replace"), css_url)
                for bucket in ("images", "streams", "other"):
                    for u in css_found.get(bucket, []):
                        add_candidate(u, "css")
            except NetworkError:
                continue
        # 内联样式/脚本已在 extract_from_html 中覆盖；再扫一遍脚本文件里的直链
        scripts = list(dict.fromkeys(found.get("scripts", [])))[:6]
        for js_url in scripts:
            if ctx.cancelled():
                return
            try:
                resp = ctx.session.peep(js_url, referer=base_url, nbytes=1 << 20)
                if not resp.ok or not resp.body:
                    continue
                text = resp.body.decode(resp.charset, errors="replace")
                for u in htmlparse.extract_json_media(text, js_url)[:50]:
                    add_candidate(u, "script")
            except NetworkError:
                continue
        # JSON 接口（页面里常见的 /api/list 之类）：尝试从 HTML 里找出来的接口地址
        for api in _find_api_endpoints(html, base_url)[:5]:
            if ctx.cancelled():
                return
            try:
                resp = ctx.session.peep(api, referer=base_url, nbytes=1 << 20,
                                        headers={"Accept": "application/json,*/*"})
                if resp.ok and resp.body and (
                        "json" in resp.content_type or resp.body.lstrip()[:1] in (b"{", b"[")):
                    for u in htmlparse.extract_json_media(
                            resp.body.decode(resp.charset, errors="replace"), api)[:100]:
                        add_candidate(u, "json")
            except NetworkError:
                continue
        del summary

    # ------------------------------------------------------------------ 动态
    def _dynamic_extract(self, url: str, ctx: SniffContext, add_candidate,
                         summary: dict, candidates: dict) -> None:
        browser_path = ctx.options.browser_path
        if not browser_path and self.browser_factory is BrowserSession:
            summary["browser_missing"] = True
            self.on_log("[动态] 未找到 Edge/Chrome，跳过动态嗅探（仅静态结果）")
            return
        self.on_progress("启动内置浏览器动态嗅探…")
        session = self.browser_factory(browser_path=browser_path,
                                       headless=ctx.options.headless,
                                       cookie=ctx.session._raw_cookie,
                                       proxy=ctx.session.proxy,
                                       direct=ctx.session.direct,
                                       # 让浏览器也用同一套 UA，避免静态/动态结果不一致
                                       user_agent=ctx.session.user_agent,
                                       log=self.on_log)
        self._browser = session
        try:
            session.start()
            summary["dynamic"] = True
            self.on_progress("加载页面并监听网络…")
            session.open_page(url, wait_load=min(15.0, ctx.options.timeout + 3))

            def _collect(hit: dict) -> None:
                htype = hit.get("type") or ""
                u = hit.get("url") or ""
                headers = hit.get("headers") or {}
                ctype = (headers.get("content-type") or "").lower()
                typed_media = (htype in _NETWORK_MEDIA_TYPES
                               or any(k in ctype for k in ("image/", "video/",
                                                           "audio/", "mpegurl",
                                                           "dash+xml")))
                if typed_media:
                    # 浏览器已经告诉我们这是媒体，URL 没有扩展名也照收
                    add_candidate(u, "network", headers=None, force=True)
                elif _looks_interesting(u) and _binary_like(headers):
                    add_candidate(u, "network", headers=None)

            collect_hit = _collect
            session.drain(collect_hit)
            deadline = time.time() + max(1.0, ctx.options.dynamic_wait)
            # 自适应等待：原来是无条件等满 dynamic_wait（默认 8 秒），
            # 但绝大多数页面前两秒就把媒体请求发完了。这里改成
            # “连续 idle_gap 秒没有新的媒体请求就提前收工”，
            # 上限仍然是 dynamic_wait，慢站点不受影响。
            idle_gap = 1.2
            min_wait = 2.0
            began = time.time()
            beat = {"last": began}

            def collect_hit(hit: dict) -> None:
                beat["last"] = time.time()
                _collect(hit)

            if ctx.options.scroll:
                steps = 4
                for i in range(steps):
                    if ctx.cancelled() or time.time() > deadline:
                        break
                    session.scroll_to_bottom(steps=1, delay=0.15)
                    session.wait(min(0.35, max(0.1, deadline - time.time())),
                                 collect_hit)
            while time.time() < deadline and not ctx.cancelled():
                session.wait(min(0.25, max(0.05, deadline - time.time())),
                             collect_hit)
                now = time.time()
                if (now - began >= min_wait
                        and now - beat["last"] >= idle_gap):
                    break
            session.drain(collect_hit)

            # DOM 兜底
            try:
                for u in session.collect_html_media():
                    if u.startswith(("http://", "https://")):
                        add_candidate(u, "dom")
            except Exception:
                pass
            for hit in session._hits:            # noqa: SLF001 - 内部数据，直接复用
                add_candidate(hit.get("url", ""), "network")
            self.on_log(f"[动态] 网络监听完成，候选资源 {len(candidates)} 个")
            try:
                summary["title"] = summary.get("title") or session.get_page_title()
            except Exception:
                pass
        except Exception as exc:
            self.on_log(f"[动态] 动态嗅探失败: {exc}")
        finally:
            try:
                session.close()
            except Exception:
                pass
            self._browser = None

    # ---------------------------------------------------------------- 探测
    def _probe_all(self, candidates: dict, page_url: str,
                   ctx: SniffContext) -> list[ResourceItem]:
        """并发探测候选资源。

        关键约束：**不能无限期等下去**。某些站点/CDN 会长时间不响应，
        若逐条等 socket 超时，界面就会停在「解析中」很久且一条结果都没有。
        因此这里有：
          * 总时限 ``probe_deadline``：超时后不再等，未完成的按 URL 推断类型照样上屏；
          * 工作线程为 daemon，且不 join，取消后立即返回；
          * 每 0.4 秒回报一次进度（含已用时间），让界面看得出在动。
        """
        if not candidates:
            self.on_progress("没有发现资源")
            return []

        total = len(candidates)
        limit = max(1, ctx.options.max_items)
        urls = list(candidates.items())[:limit]
        total = len(urls)
        workers = max(1, min(ctx.options.workers, total))
        deadline = time.monotonic() + max(3.0, float(ctx.options.probe_deadline))

        self.on_progress(f"开始解析 {total} 个候选资源…")
        task_q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        for entry in urls:
            task_q.put(entry)

        result_q: "queue.Queue[tuple]" = queue.Queue()
        stop_flag = threading.Event()

        def worker() -> None:
            while not stop_flag.is_set() and not ctx.cancelled():
                try:
                    u, meta = task_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    item = self.probe_url(
                        u, page_url, ctx.session,
                        source=meta.get("source", ""),
                        headers=meta.get("headers"),
                        timeout=min(float(ctx.options.timeout), 8.0))
                except Exception as exc:            # 单条失败不影响整体
                    item = ("error", u, str(exc))
                result_q.put((u, meta, item))

        threads = [threading.Thread(target=worker, daemon=True,
                                    name=f"probe-{i}") for i in range(workers)]
        for t in threads:
            t.start()

        items: list[ResourceItem] = []
        finished: set[str] = set()
        started = time.monotonic()
        last_report = 0.0

        def handle(item) -> None:
            if item is None:
                return                      # 该候选被过滤（HTML/404 等），跳过
            if isinstance(item, ResourceItem):
                if self._accept(item, ctx.options):
                    stored, _is_new = self.resources.add(item)
                    if stored.state != "error":
                        stored.state = "ready"
                    items.append(stored)
                    self.on_item(stored)
                return
            # 探测抛异常：仍然保留链接，让用户自己决定是否下载
            _kind, url, message = item
            fallback = self._fallback_item(url, page_url, message)
            if fallback is not None:
                stored, _is_new = self.resources.add(fallback)
                items.append(stored)
                self.on_item(stored)

        while not ctx.cancelled():
            now = time.monotonic()
            if now >= deadline:
                self.on_log(f"[探测] 已达时限 {deadline - started:.0f}s，"
                            f"跳过剩余未完成的资源")
                break
            try:
                u, _meta, item = result_q.get(timeout=0.25)
            except queue.Empty:
                if len(finished) >= total:
                    break
                now = time.monotonic()
                if now - last_report >= 0.4:
                    last_report = now
                    self.on_progress(
                        f"解析资源 {len(finished)}/{total}"
                        f"（已用 {now - started:.0f}s）")
                continue
            finished.add(u)
            handle(item)
            now = time.monotonic()
            if now - last_report >= 0.4:
                last_report = now
                self.on_progress(f"解析资源 {len(finished)}/{total}"
                                 f"（已用 {now - started:.0f}s）")

        stop_flag.set()
        # 收尾：把因为超时/取消还没出结果的候选也放上屏（标注未知信息）
        timeout_note = "元数据未获取（响应超时）"
        for u, meta in urls:
            if ctx.cancelled():
                break
            if u in finished:
                continue
            fallback = self._fallback_item(u, page_url, timeout_note)
            if fallback is not None:
                stored, _is_new = self.resources.add(fallback)
                items.append(stored)
                self.on_item(stored)
            finished.add(u)
        return items

    def _fallback_item(self, url: str, page_url: str,
                       message: str) -> Optional[ResourceItem]:
        """拿不到响应时的兜底条目：按 URL 猜类型，保证列表里有东西可操作。"""
        ext = ext_from_url(url) or ext_of(url)
        kind = kind_from_ext(ext)
        if kind == "other":
            return None
        item = ResourceItem(url=url, page_url=page_url, kind=kind, fmt=ext,
                            filename=htmlparse.guess_filename(url),
                            source="fallback")
        item.state = "error"
        item.message = message
        return item

    def probe_url(self, url: str, page_url: str, session: HttpSession,
                  source: str = "", headers: Optional[dict] = None,
                  want_full: bool = True,
                  timeout: Optional[float] = None) -> Optional[ResourceItem]:
        """探测单个 URL 并生成 ResourceItem（供后台探测与「补充信息」共用）。"""
        ext = ext_from_url(url) or ext_of(url)
        item = ResourceItem(url=url, page_url=page_url, source=source,
                            fmt=ext, kind=kind_from_ext(ext))
        try:
            resp = session.request(url, referer=page_url, headers=headers,
                                   max_bytes=96 * 1024, timeout=timeout)
        except NetworkError as exc:
            item.state = "error"
            item.message = str(exc)
            item.filename = htmlparse.guess_filename(url)
            if source in ("network", "dom", "css", "script", "json"):
                return None        # 动态通道的失效链接不值得展示
            return item
        item.status = resp.status
        if resp.status >= 400:
            # 出错响应不猜媒体类型：很多站点会把 404 渲染成 HTML
            item.filename = htmlparse.guess_filename(url)
            if resp.status in (404, 410) and source in ("network", "html", "dom",
                                                       "script", "css", "json"):
                return None
            item.kind = kind_from_ext(ext)
            item.state = "error"
            item.message = f"HTTP {resp.status}"
            item.fmt = ext
            return item if item.kind != "other" else None
        item.mime = resp.content_type
        item.filename = htmlparse.guess_filename(
            resp.url or url, resp.header("content-disposition"))
        size = resp.total_size()
        if size:
            item.size = size
        head = resp.body or b""
        # 文件不大且还没读完 → 再读一次拿完整数据（图片缩略图、音频时长）
        body_complete = not resp.truncated
        tail = b""
        if want_full and resp.ok and (not body_complete) and size and size <= 8 << 20:
            try:
                full = session.request(url, referer=page_url, headers=headers,
                                       max_bytes=int(size) + 1)
                if full.ok and full.body:
                    head = full.body
                    body_complete = True
            except NetworkError:
                pass
        if want_full and not body_complete and size > 8 << 20:
            # 大文件：再取末尾一段（MP4 的 moov 常常在文件尾）
            step = 256 * 1024
            start = max(0, size - step)
            try:
                tail_resp = session.request(
                    url, referer=page_url,
                    headers=dict(headers or {}, Range=f"bytes={start}-{size - 1}"),
                    max_bytes=step)
                if tail_resp.ok and tail_resp.body:
                    tail = tail_resp.body
            except NetworkError:
                pass

        info: MediaProbe = probe(head, tail, size or len(head), resp.content_type,
                                 ext, thumbnail=True)
        if info.kind == "other" and not info.format:
            return None
        item.kind = info.kind if info.kind != "other" else kind_from_ext(ext)
        item.fmt = info.format or ext
        item.width, item.height = info.width, info.height
        item.duration, item.bitrate = info.duration, info.bitrate
        item.estimate = info.estimate
        item.thumbnail = info.thumbnail
        item.state = "ready"
        if info.kind == "stream":
            item.extra = "流媒体清单" if not item.extra else item.extra
        return item

    @staticmethod
    def _accept(item: ResourceItem, opts: SniffOptions) -> bool:
        if item.state == "error" and item.kind == "other":
            return False
        if item.kind not in opts.kinds:
            # 直接下载链接允许 unknown 类型进入（kind=other 且有扩展名）
            return False
        if item.kind == "image" and opts.min_image_side:
            if item.width and min(item.width, item.height) < opts.min_image_side:
                return False
        if item.kind == "other" and not item.fmt:
            return False
        return True

    def _collapse_image_variants(self, items: list[ResourceItem]) -> list[ResourceItem]:
        """把同一张图的多个尺寸/格式变体合并成一条（保留最大最清晰的那个）。

        典型场景：X/Twitter 的头像同时有 _normal/_x96/_400x400 三种，
        配图同时有 ?format=jpg&name=small|medium|large 多个版本。
        """
        groups: dict[str, list[ResourceItem]] = {}
        for item in items:
            if item.kind != "image":
                continue
            groups.setdefault(variant_key(item.url, "image"), []).append(item)

        removed: list[ResourceItem] = []
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda i: ((i.width or 0) * (i.height or 0),
                                      i.size or 0), reverse=True)
            best = group[0]
            merged = len(group) - 1
            note = f"已合并 {merged} 个尺寸变体"
            best.extra = f"{best.extra}；{note}" if best.extra else note
            for dup in group[1:]:
                removed.append(dup)
        if removed:
            self.resources.remove([i.url for i in removed])
            for item in removed:
                self.on_remove(item)
            self.on_log(f"[合并] 同一图片的 {len(removed)} 个尺寸变体已合并")
        return [i for i in items if i not in removed]

    # -------------------------------------------------------------- 清单展开
    def _expand_manifests(self, items: list[ResourceItem], ctx: SniffContext) -> None:
        manifests = [i for i in items if i.kind == "stream"]
        for man in manifests:
            if ctx.cancelled():
                return
            self.on_progress(f"解析流媒体清单 {man.filename or man.url}…")
            try:
                resp = ctx.session.peep(man.url, referer=man.page_url, nbytes=2 << 20)
                if not resp.ok or not resp.body:
                    continue
                text = resp.body.decode(resp.charset, errors="replace")
            except NetworkError as exc:
                man.message = str(exc)
                continue
            pl = streams.parse_manifest(text, resp.url or man.url, resp.content_type)
            if pl.error:
                man.message = pl.error
                continue
            if pl.is_master and pl.variants:
                man.extra = f"{man.kind_label}清单 · {len(pl.variants)} 个清晰度"
                man.height = max((v.height for v in pl.variants), default=0)
                man.width = max((v.width for v in pl.variants), default=0)
                man.duration = man.duration or pl.duration
                for idx, variant in enumerate(pl.variants):
                    if ctx.cancelled():
                        return
                    child = ResourceItem(
                        url=variant.url, page_url=man.page_url,
                        kind=variant.kind if variant.kind != "audio" else "stream",
                        fmt=streams.guess_extension(variant),
                        filename=streams.variant_display_name(variant, idx)
                        + "." + streams.guess_extension(variant),
                        width=variant.width, height=variant.height,
                        bitrate=variant.bandwidth, duration=pl.duration,
                        source="manifest", child_of=man.url,
                        extra=", ".join(x for x in (variant.codecs, variant.lang) if x),
                    )
                    stored, is_new = self.resources.add(child)
                    if ctx.options.expand_manifests:
                        items.append(stored)
                    if is_new:
                        self.on_item(stored)
            elif pl.segments:
                man.duration = man.duration or pl.duration
                man.extra = (f"{man.extra or '流媒体'} · {len(pl.segments)} 个分片"
                             + (" · 加密" if pl.encrypted else ""))
                self.on_item(man)
        del items


# --------------------------------------------------------------------------
# 辅助判断
# --------------------------------------------------------------------------
_ALL_MEDIA = IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | STREAM_EXT


def _looks_interesting(url: str) -> bool:
    """URL 看起来像媒体。

    注意不能只看路径扩展名：X(Twitter)、部分图床把格式放在查询参数里，
    形如 ``/media/ABC123?format=jpg&name=large``，只看扩展名会把它们全部漏掉。
    """
    return looks_like_media_url(url)


def _binary_like(headers: dict) -> bool:
    ctype = (headers.get("content-type") or "").lower()
    return not any(t in ctype for t in ("text/html", "text/css", "javascript",
                                        "application/json", "text/plain"))


def _is_direct_media_url(url: str) -> bool:
    ext = ext_from_url(url)
    return ext in _ALL_MEDIA and ext not in ("html", "htm")


def _find_api_endpoints(html: str, base_url: str) -> list[str]:
    """在页面里找可能的 JSON 接口地址。"""
    import re

    out: list[str] = []
    patterns = [
        r"""['"](/[^'"]*?(?:api|list|json|ajax|feed|data)[^'"]{0,60}?\.(?:json|php|aspx?))['"]""",
        r"""['"](/[^'"]*/api/[^'"]{2,80})['"]""",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            url = htmlparse._clean_url(match.group(1), base_url)  # noqa: SLF001
            if url and url not in out:
                out.append(url)
    return out


def summarize(items: list[ResourceItem]) -> dict:
    counts: dict[str, int] = {}
    total = 0
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
        total += item.size or 0
    return {"counts": counts, "bytes": total}
