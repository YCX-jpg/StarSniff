"""GitHub Releases 检查更新。

只依赖标准库：公开仓库的 ``releases/latest`` API 不需要 token。
如果 API 被限流（403）或暂时不可用，会退回解析 GitHub 的
``/releases/latest`` 跳转地址，至少还能拿到版本号，保证检查更新
不会因为 API 限流而完全失效。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from . import __version__ as APP_VERSION

REPO = "YCX-jpg/StarSniff"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
RELEASES_LATEST = f"{RELEASES_PAGE}/latest"
USER_AGENT = f"StarSniff/{APP_VERSION} (+https://github.com/{REPO})"

_VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})")
# 预发布后缀：3.1.1-rc2 / 3.1.1-rc3 / 3.1.1-beta1 / 3.1.2-snapshot
_PRERELEASE_RE = re.compile(
    r"(?i)(?:^|[-_.\s])?(alpha|beta|rc|pre|preview|dev|snapshot|snap|sanshot)"
    r"[.\-_]?(\d*)")
# 同一主版本下预发布等级：数字越小越早；没有后缀 = 正式版，排最后
_PRE_RANK = {"snapshot": 0, "sanshot": 0, "snap": 0, "dev": 0,
             "alpha": 1, "pre": 2, "preview": 2, "beta": 2, "rc": 3}


class UpdateError(Exception):
    """检查更新失败，``code`` 供界面区分提示语气。"""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int = 0

    @property
    def is_windows(self) -> bool:
        lower = self.name.lower()
        return lower.endswith((".exe", ".msi", ".zip"))


@dataclass
class UpdateInfo:
    version: str
    tag: str
    name: str = ""
    notes: str = ""
    url: str = RELEASES_PAGE
    published_at: str = ""
    assets: list[ReleaseAsset] = field(default_factory=list)

    @property
    def download_url(self) -> str:
        """优先给 Windows 安装包/压缩包，否则打开 Release 页面。"""
        for asset in self.assets:
            if asset.name.lower().endswith(".exe"):
                return asset.url
        for asset in self.assets:
            if asset.name.lower().endswith(".zip"):
                return asset.url
        return self.url

    def is_newer_than(self, current: str = APP_VERSION) -> bool:
        return is_newer(self.version, current)


def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v2.1.0`` / ``StarSniff 2.1.0`` 归一成可比较的元组。"""
    match = _VERSION_RE.search(text or "")
    if not match:
        return ()
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def version_key(text: str) -> tuple[int, ...]:
    """完整排序键：主版本 + 预发布等级。

    只比主版本会让 ``3.1.1-rc2`` 和 ``3.1.1-rc3`` 相等（rc2 于是认为
    rc3「已是最新」）。这里把后缀也算进来：

    ``3.1.1-rc2 < 3.1.1-rc3 < 3.1.1``
    """
    base = parse_version(text)
    if not base:
        return ()
    match = _VERSION_RE.search(text or "")
    rest = (text or "")[match.end():] if match else ""
    pre = _PRERELEASE_RE.search(rest)
    if pre:
        rank = _PRE_RANK.get(pre.group(1).lower(), 3)
        number = int(pre.group(2) or 0)
        # 有预发布后缀的排在正式版之前
        return base + (-1, rank, number)
    return base + (0, 0, 0)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    new, old = version_key(candidate), version_key(current)
    if not new or not old:
        return False
    return new > old


def _proxy_candidates() -> list[str]:
    """可用的代理候选（系统代理 / 本地代理），没有就返回空列表。"""
    try:
        from .net import local_proxy_candidates, proxy_candidates

        items = list(proxy_candidates(RELEASES_LATEST))
        # 系统代理可能已经失效（VPN 换了端口），本地端口再兜一次
        for item in local_proxy_candidates():
            if item not in items:
                items.append(item)
        return items
    except Exception:  # noqa: BLE001 - 代理探测失败不能挡住更新检查
        return []


def _request(url: str, timeout: float, accept: str = "application/json",
             proxy: str = ""):
    request = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    # 显式指定代理：proxy="" 时连系统代理都不用（最后由外层兜底直连）
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.4)
    raise last  # type: ignore[misc]


def _read_json(url: str, timeout: float, proxy: str = "") -> dict:
    try:
        with _request(url, timeout, proxy=proxy) as response:
            raw = response.read(1_500_000)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("仓库还没有发布 Release", "no_release") from exc
        if exc.code == 403:
            raise UpdateError("GitHub API 暂时限流", "rate_limit") from exc
        raise UpdateError(f"GitHub 返回 HTTP {exc.code}", "http") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"网络连接失败：{exc}", "network") from exc
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise UpdateError("GitHub 返回了无法解析的数据", "bad_json") from exc
    if not isinstance(data, dict):
        raise UpdateError("GitHub 返回的数据格式不正确", "bad_json")
    return data


def _latest_tag_from_redirect(timeout: float, proxy: str = "") -> str:
    """API 限流时的兜底：解析 /releases/latest 最终跳转到的 tag。"""
    try:
        with _request(RELEASES_LATEST, timeout, accept="text/html",
                      proxy=proxy) as response:
            final = response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("仓库还没有发布 Release", "no_release") from exc
        raise UpdateError(f"GitHub 返回 HTTP {exc.code}", "http") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"网络连接失败：{exc}", "network") from exc
    match = re.search(r"/releases/tag/([^/?#]+)", final or "")
    if not match:
        raise UpdateError("仓库还没有发布 Release", "no_release")
    from urllib.parse import unquote

    return unquote(match.group(1))


def _build_info(data: dict) -> UpdateInfo:
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("最新 Release 缺少版本号", "bad_json")
    version = tag[1:] if tag.lower().startswith("v") else tag
    assets: list[ReleaseAsset] = []
    for raw in data.get("assets") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        url = str(raw.get("browser_download_url") or "")
        if name and url:
            assets.append(ReleaseAsset(
                name=name, url=url, size=int(raw.get("size") or 0)))
    return UpdateInfo(
        version=version,
        tag=tag,
        name=str(data.get("name") or f"StarSniff {tag}"),
        notes=str(data.get("body") or ""),
        url=str(data.get("html_url") or RELEASES_PAGE),
        published_at=str(data.get("published_at") or ""),
        assets=assets,
    )


def check_latest(timeout: float = 8.0,
                 proxies: Optional[list[str]] = None) -> UpdateInfo:
    """返回最新 Release；没有 Release 或网络失败时抛 ``UpdateError``。

    会按「系统代理 / 本地代理（Clash、v2rayN 之类）」逐个尝试，最后才直连，
    所以开着本地 VPN 但没设开机代理时也能检查更新。
    """
    candidates = list(proxies if proxies is not None else _proxy_candidates())
    if "" not in candidates:
        candidates.append("")                 # 最后兜底直连
    failure: Optional[UpdateError] = None
    for proxy in candidates:
        try:
            data = _read_json(API_LATEST, timeout, proxy=proxy)
        except UpdateError as exc:
            failure = exc
            # 404 / 数据坏了：换线路也没用，直接报错
            if exc.code in ("no_release", "bad_json"):
                raise
            continue
        return _build_info(data)

    # API 都失败（限流或网络不通）：退回解析跳转地址，至少拿到版本号
    for proxy in candidates:
        try:
            tag = _latest_tag_from_redirect(timeout, proxy=proxy)
        except UpdateError as exc:
            failure = exc
            continue
        version = tag[1:] if tag.lower().startswith("v") else tag
        return UpdateInfo(
            version=version, tag=tag, name=f"StarSniff {tag}",
            notes="（GitHub API 限流或不可用，读不到更新说明，请打开 Release 页面查看。）",
            url=f"https://github.com/{REPO}/releases/tag/{tag}")
    raise failure or UpdateError("检查更新失败", "network")


def describe_error(exc: Exception) -> str:
    if isinstance(exc, UpdateError):
        if exc.code == "no_release":
            return "仓库还没有发布 Release，发布后即可检查更新。"
        if exc.code == "rate_limit":
            return "GitHub API 暂时限流，请稍后再试。"
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
