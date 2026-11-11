"""可选 yt-dlp 集成：识别站点 / 拉取剧集列表 / 交给外部下载器进程。

不强制依赖 yt-dlp：找不到时 ``available()`` 返回 False，界面自动退回普通嗅探。
查找顺序：环境变量 ``YTDLP_PATH`` → 软件目录的 ``yt-dlp.exe`` → PATH →
当前 Python 解释器的 ``yt_dlp`` 模块。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import __version__ as APP_VERSION


class YtDlpError(Exception):
    """yt-dlp 执行失败。"""


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DOWNLOAD_URL = ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
                "yt-dlp.exe")
MIN_EXE_SIZE = 1_000_000


def install_dir() -> str:
    """yt-dlp.exe 的安装位置：源码运行时是版本目录，打包后是 exe 同级目录。"""
    return _app_dir()


def target_path() -> str:
    return os.path.join(install_dir(), "yt-dlp.exe")


def download_latest(dest_dir: str = "", progress=None, cancel=None,
                    timeout: float = 60.0, url: str = DOWNLOAD_URL,
                    proxy: str = "", direct: bool = False) -> str:
    """从 GitHub 下载最新 yt-dlp.exe，原子替换到软件目录。

    progress(done, total)；cancel() 返回 True 时中断。
    返回安装后的 exe 路径。
    """
    destination = os.path.join((dest_dir or install_dir()).strip(),
                               "yt-dlp.exe")
    folder = os.path.dirname(os.path.abspath(destination))
    os.makedirs(folder, exist_ok=True)
    part = destination + ".part"
    handlers = []
    if direct:
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy.strip():
        proxy = proxy.strip()
        if "://" not in proxy:
            proxy = "http://" + proxy
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={
        "User-Agent": f"StarSniff/{APP_VERSION} (yt-dlp installer)",
        "Accept": "application/octet-stream",
    })
    try:
        with opener.open(request, timeout=timeout) as response:
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = 0
            with open(part, "wb", buffering=1024 * 1024) as fh:
                while True:
                    if cancel and cancel():
                        raise YtDlpError("已取消下载")
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            if cancel and cancel():
                raise YtDlpError("已取消下载")
            if done < MIN_EXE_SIZE:
                raise YtDlpError(f"下载内容过小（{done} 字节），可能不是 yt-dlp.exe")
            with open(part, "rb") as fh:
                if fh.read(2) != b"MZ":
                    raise YtDlpError("下载内容不是 Windows exe（缺少 MZ 头）")
        os.replace(part, destination)
    except urllib.error.HTTPError as exc:
        raise YtDlpError(f"GitHub 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YtDlpError(f"下载失败：{exc}") from exc
    finally:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
    reset()
    return destination


@dataclass
class _State:
    command: list[str] = field(default_factory=list)
    checked: bool = False


_STATE = _State()


def reset() -> None:
    """测试/环境变化后重新探测。"""
    _STATE.command = []
    _STATE.checked = False


def _detect() -> list[str]:
    if _STATE.checked:
        return list(_STATE.command)
    _STATE.checked = True
    candidates: list[str] = []
    env = os.environ.get("YTDLP_PATH", "").strip()
    if env:
        candidates.append(env)
    candidates.append(os.path.join(_app_dir(), "yt-dlp.exe"))
    found = shutil.which("yt-dlp")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            _STATE.command = [candidate]
            return list(_STATE.command)
    try:
        import importlib.util

        if importlib.util.find_spec("yt_dlp") is not None:
            _STATE.command = [sys.executable, "-m", "yt_dlp"]
    except Exception:
        _STATE.command = []
    return list(_STATE.command)


def available() -> bool:
    return bool(_detect())


def command() -> list[str]:
    return list(_detect())


def describe() -> str:
    cmd = _detect()
    if not cmd:
        return "未检测到 yt-dlp（放一个 yt-dlp.exe 到软件目录即可启用）"
    if len(cmd) > 1:
        return "已检测到 yt-dlp（Python 模块）"
    return f"已检测到 yt-dlp：{cmd[0]}"


def _run(cmd: list[str], timeout: float,
         cancel: Optional[Callable[[], bool]] = None) -> tuple[int, str, str]:
    """运行子进程，支持取消；返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_NO_WINDOW)
    except OSError as exc:
        raise YtDlpError(f"无法启动 yt-dlp：{exc}") from exc
    deadline = time.time() + max(1.0, timeout)
    while True:
        code = proc.poll()
        if code is not None:
            break
        if cancel is not None and cancel():
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise YtDlpError("已取消")
        if time.time() > deadline:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise YtDlpError("yt-dlp 解析超时")
        time.sleep(0.15)
    out, err = proc.communicate()
    return proc.returncode or 0, out or "", err or ""


def extract(url: str, cookie: str = "", timeout: float = 45.0,
            cancel: Optional[Callable[[], bool]] = None) -> dict:
    """拉取单个视频/剧集列表的元数据（扁平列表，不逐个解析清晰度）。"""
    cmd = _detect()
    if not cmd:
        raise YtDlpError("未检测到 yt-dlp")
    full = cmd + ["--flat-playlist", "--dump-single-json", "--no-warnings",
                  "--skip-download", "--no-progress"]
    if cookie and "\n" not in cookie:
        full += ["--add-header", f"Cookie: {cookie.strip()}"]
    full.append(url)
    code, out, err = _run(full, timeout, cancel)
    if code != 0:
        detail = (err or out or "").strip().splitlines()
        raise YtDlpError(detail[-1] if detail else f"yt-dlp 退出码 {code}")
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise YtDlpError("yt-dlp 返回的不是有效 JSON") from exc
    entries = data.get("entries") or [data]
    return {"title": data.get("title") or "", "entries": [e for e in entries if e]}


def entry_url(entry: dict, page_url: str) -> str:
    raw = str(entry.get("url") or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    webpage = str(entry.get("webpage_url") or "").strip()
    if webpage.startswith("http"):
        return webpage
    extractor = f"{entry.get('ie_key') or ''} {entry.get('extractor') or ''}".lower()
    video_id = str(entry.get("id") or raw).strip()
    if video_id and "youtube" in extractor:
        return f"https://www.youtube.com/watch?v={video_id}"
    return page_url


def entry_to_item_data(entry: dict, page_url: str) -> dict:
    """把 yt-dlp 条目转换成 GUI 侧需要的字段。"""
    title = str(entry.get("title") or entry.get("id") or "").strip()
    url = entry_url(entry, page_url)
    duration = entry.get("duration") or 0
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = 0.0
    size = entry.get("filesize") or entry.get("filesize_approx") or 0
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    return {
        "url": url,
        "page_url": page_url,
        "title": title or url,
        "duration": duration,
        "size": size,
        "uploader": entry.get("uploader") or entry.get("channel") or "",
        "index": entry.get("playlist_index") or 0,
    }
