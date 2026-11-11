"""智能代理：自动比较「直连」与「走代理」的延迟，选择更快的一条。

使用场景
--------
设置页填好代理后，每次开始嗅探前会同时向目标站点发一个极小的探测请求：

* 一条直接连目标站点（完全不用代理，连系统环境变量里的代理也不用）；
* 一条经过配置的代理（设置里留空时用系统环境变量里的代理）。

两条线路并发测速，只比较响应头到达时间（不下载正文），因此通常只要几十到
几百毫秒。哪条快就走哪条；某条线路不可用时自动落到另一条；两条都不可用时
先按直连继续，让原有的超时/报错提示照常工作。

也支持在设置里强制只用代理或强制直连：

* ``auto``   —— 自动测速选择（默认）；
* ``proxy``  —— 强制走代理；
* ``direct`` —— 强制直连，完全绕过代理。

测速结果只影响本次嗅探以及随后用本程序发起的下载；不修改系统代理设置。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .net import HttpSession, NetworkError, system_proxy

AUTO = "auto"
PROXY = "proxy"
DIRECT = "direct"
MODES = (AUTO, PROXY, DIRECT)

MODE_LABELS = {
    AUTO: "智能选择（自动测速，哪条快走哪条）",
    PROXY: "强制走代理",
    DIRECT: "不走代理（直连）",
}
MODE_SHORT = {AUTO: "智能", PROXY: "代理", DIRECT: "直连"}

# 两条线路都很快时允许的误差：代理必须比直连快 5ms 以上才值得绕路
_TOLERANCE_MS = 5.0


def normalize_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    return value if value in MODES else AUTO


def normalize_proxy(proxy: str) -> str:
    return (proxy or "").strip()


@dataclass
class Route:
    """一次线路选择的结果。"""

    mode: str = AUTO
    proxy: str = ""                 # 实际使用的代理地址（直连时为空）
    used_proxy: bool = False
    tested: bool = False            # 是否真的跑过测速
    direct_ms: Optional[float] = None
    proxy_ms: Optional[float] = None
    direct_error: str = ""
    proxy_error: str = ""
    reason: str = ""

    @property
    def label(self) -> str:
        if self.used_proxy:
            base = "代理" if self.proxy else "系统代理"
            if self.proxy_ms is not None:
                return f"{base} {self.proxy_ms:.0f}ms"
            return base
        if self.direct_ms is not None:
            return f"直连 {self.direct_ms:.0f}ms"
        return "直连"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "proxy": self.proxy,
            "used_proxy": self.used_proxy,
            "tested": self.tested,
            "direct_ms": None if self.direct_ms is None else round(self.direct_ms, 1),
            "proxy_ms": None if self.proxy_ms is None else round(self.proxy_ms, 1),
            "reason": self.reason,
            "label": self.label,
        }


def _measure(url: str, proxy: str, timeout: float) -> tuple[Optional[float], str]:
    """测量一条线路的响应延迟（毫秒）；失败返回 (None, 错误)。"""
    session = HttpSession(timeout=timeout, proxy=proxy, direct=not proxy,
                          env_proxy=False, user_agent="")
    started = time.perf_counter()
    try:
        resp = session.request(
            url,
            headers={"Range": "bytes=0-0", "Cache-Control": "no-cache"},
            want_body=False,
            timeout=timeout,
            follow_redirects=0,
        )
    except NetworkError as exc:
        return None, str(exc)
    except Exception as exc:                       # 兜底：任何异常都算这条线路失败
        return None, f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - started) * 1000.0
    # 4xx/3xx 说明线路通、站点有响应；5xx 多为代理自身故障，视为不可用
    if resp.status >= 500:
        return None, f"HTTP {resp.status}"
    return elapsed, ""


def probe_latencies(url: str, proxy: str = "", timeout: float = 4.0,
                    cancel: Optional[Callable[[], bool]] = None) -> dict:
    """并发测试直连/代理延迟，返回 ``{"direct": (ms, err), "proxy": (ms, err)}``。"""
    timeout = max(1.0, float(timeout))
    proxy = normalize_proxy(proxy)
    results: dict[str, tuple[Optional[float], str]] = {
        "direct": (None, "未测速"),
        "proxy": (None, "未配置代理" if not proxy else "未测速"),
    }
    lock = threading.Lock()

    def run(kind: str, p: str) -> None:
        ms, err = _measure(url, p, timeout)
        with lock:
            results[kind] = (ms, err)

    threads = [threading.Thread(target=run, args=("direct", ""), daemon=True,
                                name="route-direct")]
    if proxy:
        threads.append(threading.Thread(target=run, args=("proxy", proxy),
                                        daemon=True, name="route-proxy"))
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout + 1.0
    for thread in threads:
        while thread.is_alive() and time.monotonic() < deadline:
            if cancel and cancel():
                break
            thread.join(0.03)
    with lock:
        return dict(results)


def choose(url: str, mode: str = AUTO, proxy: str = "", timeout: float = 4.0,
           log: Optional[Callable[[str], None]] = None,
           cancel: Optional[Callable[[], bool]] = None) -> Route:
    """按设置选择本次请求使用的线路。

    会自动处理「没填代理」「代理挂了」「直连被墙」等情况，永远不会因为
    测速失败而让嗅探无法开始。
    """
    mode = normalize_mode(mode)
    configured = normalize_proxy(proxy)
    system = "" if configured else system_proxy(url).strip()
    effective = configured or system
    source = "配置代理" if configured else "系统代理"

    def emit(message: str) -> None:
        if log:
            log(message)

    if mode == DIRECT:
        return Route(mode=mode, used_proxy=False, tested=False,
                     reason="已强制直连（不走代理）")

    if mode == PROXY:
        if not effective:
            emit("[线路] 设置了强制走代理，但没有填写代理地址，已改为直连")
            return Route(mode=mode, used_proxy=False, tested=False,
                         reason="未配置代理，已改为直连")
        emit(f"[线路] 强制走代理：{effective}")
        return Route(mode=mode, proxy=effective, used_proxy=True, tested=False,
                     reason=f"已强制走{source}")

    # ---- 智能模式
    if not effective:
        return Route(mode=mode, used_proxy=False, tested=False,
                     reason="未配置代理，直连")

    emit(f"[线路] 正在测速：直连 vs {source} {effective} …")
    latencies = probe_latencies(url, effective, timeout=timeout, cancel=cancel)
    direct_ms, direct_err = latencies["direct"]
    proxy_ms, proxy_err = latencies["proxy"]

    if cancel and cancel():
        return Route(mode=mode, used_proxy=False, tested=False,
                     reason="已停止测速，暂用直连")

    direct_ok = direct_ms is not None
    proxy_ok = proxy_ms is not None
    if direct_ok and proxy_ok:
        assert direct_ms is not None and proxy_ms is not None
        if proxy_ms + _TOLERANCE_MS < direct_ms:
            reason = (f"{source}更快（{proxy_ms:.0f}ms，"
                      f"直连 {direct_ms:.0f}ms）")
            used_proxy = True
        else:
            reason = (f"直连更快（{direct_ms:.0f}ms，"
                      f"{source} {proxy_ms:.0f}ms）")
            used_proxy = False
    elif proxy_ok and not direct_ok:
        used_proxy = True
        reason = f"直连不可用，改走{source}（{proxy_ms:.0f}ms）"
    elif direct_ok and not proxy_ok:
        used_proxy = False
        reason = f"{source}不可用，已改用直连（{direct_ms:.0f}ms）"
    else:
        used_proxy = False
        reason = "直连与代理均不可用，先按直连继续"

    route = Route(mode=mode, proxy=effective if used_proxy else "",
                  used_proxy=used_proxy, tested=True,
                  direct_ms=direct_ms, proxy_ms=proxy_ms,
                  direct_error=direct_err, proxy_error=proxy_err,
                  reason=reason)
    emit(f"[线路] {reason} → 本次使用{route.label}")
    return route


def quick_route(mode: str = AUTO, proxy: str = "", url: str = "") -> Route:
    """不跑测速的快速线路（用于没有嗅探记录时直接下载）。"""
    mode = normalize_mode(mode)
    configured = normalize_proxy(proxy)
    system = "" if configured else system_proxy(url).strip()
    effective = configured or system
    if mode == DIRECT:
        return Route(mode=mode, used_proxy=False, tested=False,
                     reason="已强制直连（不走代理）")
    if mode == PROXY:
        if not effective:
            return Route(mode=mode, used_proxy=False, tested=False,
                         reason="未配置代理，已改为直连")
        return Route(mode=mode, proxy=effective, used_proxy=True, tested=False,
                     reason="强制走代理")
    if effective:
        return Route(mode=mode, proxy=effective, used_proxy=True, tested=False,
                     reason="使用已配置的代理（未测速）")
    return Route(mode=mode, used_proxy=False, tested=False,
                 reason="未配置代理，直连")


def apply_route(session: HttpSession, route: Route) -> HttpSession:
    """把选中的线路写进 HTTP 会话。"""
    session.proxy = route.proxy if route.used_proxy else ""
    session.direct = not route.used_proxy
    session.env_proxy = False
    return session
