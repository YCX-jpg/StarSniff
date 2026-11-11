"""下载管理：并行任务队列 + HLS 分片合并下载。

设计：
* 最多 ``max_workers`` 个文件同时下载，队列线程安全，支持取消与断点续传；
* 单个文件下载失败会自动重试，已写入的部分走 Range 续传，不会从头重下；
* 服务端提前断开（收到的字节少于 Content-Length）时按失败处理并续传；
* 所有回调（进度/完成/失败）在任务线程里触发，由 UI 层负责转投到主线程；
* HLS（.m3u8）任务会把分片/初始化段拼接成一个完整文件（.ts 或 .mp4），
  分片并发抓取、严格按顺序写盘，加密切片用纯 Python AES-128-CBC 解密。
"""

from __future__ import annotations

import collections
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import streams
from . import ytdlp
from .aes import aes128_cbc_decrypt
from .mediainfo import ext_of
from .net import DownloadCancelled, HttpSession, NetworkError


@dataclass
class DownloadTask:
    url: str
    filepath: str
    referer: str = ""
    headers: dict = field(default_factory=dict)
    kind: str = ""                 # image / audio / video / stream / other
    title: str = ""
    engine: str = "http"           # http / ytdlp
    cookie: str = ""
    done_bytes: int = 0
    total_bytes: int = 0
    state: str = "pending"         # pending / running / done / error / canceled
    error: str = ""
    speed: float = 0.0
    started: float = 0.0
    finished: float = 0.0
    cancel_requested: bool = False  # 单个任务取消标记（不影响队列其它任务）

    @property
    def progress(self) -> float:
        if self.total_bytes > 0:
            return min(100.0, self.done_bytes * 100.0 / self.total_bytes)
        return 0.0


class DownloadManager:
    """并行下载队列（默认 4 个文件并发，大文件自动多连接加速）。"""

    MAX_WORKERS = 8

    def __init__(self, session: Optional[HttpSession] = None,
                 on_start: Optional[Callable[[DownloadTask], None]] = None,
                 on_progress: Optional[Callable[[DownloadTask], None]] = None,
                 on_finish: Optional[Callable[[DownloadTask], None]] = None,
                 max_workers: int = 4, retries: int = 3,
                 segments: int = 4):
        self.session = session or HttpSession()
        self.on_start = on_start or (lambda _t: None)
        self.on_progress = on_progress or (lambda _t: None)
        self.on_finish = on_finish or (lambda _t: None)
        self.max_workers = self._clamp_workers(max_workers)
        self.segments = max(1, min(8, int(segments or 1)))
        self.retries = max(1, int(retries or 1))
        self.tasks: list[DownloadTask] = []
        self._threads: list[tuple[int, threading.Thread]] = []
        self._generation = 0
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)

    @classmethod
    def _clamp_workers(cls, value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 4
        return max(1, min(cls.MAX_WORKERS, number))

    # ---------------------------------------------------------------- 状态
    @property
    def busy(self) -> bool:
        """是否还有排队或正在下载的任务。"""
        with self._lock:
            return any(t.state in ("pending", "running") for t in self.tasks)

    running = busy

    def stats(self) -> dict:
        """队列汇总：用于底栏显示总进度。"""
        with self._lock:
            tasks = list(self.tasks)
        done = sum(1 for t in tasks if t.state == "done")
        failed = sum(1 for t in tasks if t.state in ("error", "canceled"))
        active = sum(1 for t in tasks if t.state == "running")
        pending = sum(1 for t in tasks if t.state == "pending")
        partial = sum(min(1.0, t.progress / 100.0) for t in tasks
                      if t.state == "running")
        finished = done + failed
        total = len(tasks)
        percent = ((finished + partial) * 100.0 / total) if total else 0.0
        speed = sum(t.speed for t in tasks if t.state == "running")
        return {"total": total, "done": done, "failed": failed,
                "finished": finished, "active": active, "pending": pending,
                "percent": percent, "speed": speed,
                "bytes": sum(t.done_bytes for t in tasks)}

    def active_urls(self) -> set[str]:
        """排队中/下载中的链接，避免重复入队。"""
        with self._lock:
            return {t.url for t in self.tasks
                    if t.state in ("pending", "running")}

    # ---------------------------------------------------------------- 队列
    def enqueue(self, items, folder: str,
                namer: Optional[Callable[[object], str]] = None) -> list[DownloadTask]:
        """把资源条目加入队列；返回新建的任务。"""
        with self._lock:
            idle = not any(t.state in ("pending", "running") for t in self.tasks)
            # 失败/取消留下的半截文件：重试时沿用，配合 Range 续传
            reuse = {t.url: t.filepath for t in self.tasks
                     if t.state in ("error", "canceled") and t.filepath
                     and os.path.exists(t.filepath)}
        created = []
        for item in items:
            name = namer(item) if namer else _default_name(item)
            path = reuse.get(item.url) or unique_path(
                os.path.join(folder, name))
            task = DownloadTask(
                url=item.url, filepath=path, referer=getattr(item, "page_url", ""),
                kind=getattr(item, "kind", ""),
                title=getattr(item, "filename", "") or name,
                total_bytes=getattr(item, "size", 0) or 0,
            )
            created.append(task)
        with self._cond:
            # 上一批已经全部结束：清掉旧任务，队列统计只反映当前这一批
            if idle and not any(t.state in ("pending", "running")
                                for t in self.tasks):
                self.tasks.clear()
            self.tasks.extend(created)
            self._cond.notify_all()
        return created

    def start(self) -> None:
        """启动一批工作线程；队列清空后线程自动退出，不常驻。"""
        with self._cond:
            if self._cancel.is_set():
                # 上一轮已停止：清掉取消标记并换代，旧线程收尾后自行退出
                self._cancel.clear()
            self._generation += 1
            generation = self._generation
            self._threads = [pair for pair in self._threads if pair[1].is_alive()]
            self._spawn_locked(generation)
            self._cond.notify_all()

    def _spawn_locked(self, generation: int) -> None:
        for index in range(self.max_workers):
            thread = threading.Thread(target=self._worker, args=(generation,),
                                      daemon=True,
                                      name=f"download-{index + 1}")
            self._threads.append((generation, thread))
            thread.start()

    def stop(self) -> None:
        with self._cond:
            self._cancel.set()
            self._cond.notify_all()

    def cancel_task(self, task: DownloadTask) -> None:
        """取消单个任务：排队中的立即移除，进行中的在下一个检查点退出。"""
        task.cancel_requested = True
        with self._cond:
            was_pending = task.state == "pending"
            if was_pending:
                task.state = "canceled"
                task.error = "已取消"
                task.finished = time.time()
            self._cond.notify_all()
        if was_pending:
            # 排队任务没有工作线程负责收尾，这里主动通知界面
            self.on_finish(task)

    def cancel_all(self) -> None:
        """停止整条队列（UI 的「停止下载」用）。"""
        with self._cond:
            self._cancel.set()
            canceled = []
            for task in self.tasks:
                if task.state == "pending":
                    task.state = "canceled"
                    task.error = "已取消"
                    task.finished = time.time()
                    canceled.append(task)
            self._cond.notify_all()
        for task in canceled:
            self.on_finish(task)

    def _task_cancelled(self, task: DownloadTask) -> bool:
        return self._cancel.is_set() or task.cancel_requested

    def join(self, timeout: Optional[float] = None) -> None:
        """等待队列清空（新任务可能随时入队，故不 join 工作线程）。"""
        deadline = None if timeout is None else time.time() + timeout
        with self._cond:
            while self.busy:
                if self._cancel.is_set():
                    return
                if deadline is None:
                    self._cond.wait()
                    continue
                remain = deadline - time.time()
                if remain <= 0:
                    return
                self._cond.wait(remain)

    # ---------------------------------------------------------------- 内部
    def _worker(self, generation: int) -> None:
        while True:
            with self._cond:
                if generation != self._generation or self._cancel.is_set():
                    return
                task = next((t for t in self.tasks if t.state == "pending"),
                            None)
                if task is None:
                    return
                task.state = "running"
                task.started = time.time()
            self.on_start(task)
            try:
                self._run_one(task)
                task.state = "done"
                task.error = ""
            except DownloadCancelled:
                task.state = "canceled"
                task.error = "已取消"
            except NetworkError as exc:
                task.state = "error"
                task.error = str(exc)
            except OSError as exc:
                task.state = "error"
                task.error = f"写入失败: {exc}"
            except Exception as exc:                       # 兜底
                task.state = "error"
                task.error = f"{type(exc).__name__}: {exc}"
            finally:
                task.finished = time.time()
                with self._cond:
                    self._cond.notify_all()
            self.on_finish(task)

    def _run_one(self, task: DownloadTask) -> None:
        if task.engine == "ytdlp":
            self._download_ytdlp(task)
        elif task.kind == "stream" and ext_of(task.url) == "m3u8":
            self._download_hls(task)
        elif task.kind == "stream" and ext_of(task.url) == "mpd":
            raise NetworkError("DASH 清单暂不支持本地合并，可改用浏览器或 yt-dlp 下载")
        else:
            self._download_file(task)

    def _progress_reporter(self, task: DownloadTask) -> Callable[[int, int], None]:
        """进度回调 + 瞬时速度（按 0.25 秒窗口计算，而不是整段平均）。"""
        state = {"time": 0.0, "bytes": 0}

        def report(done: int, total: int) -> None:
            task.done_bytes = done
            if total:
                task.total_bytes = total
            now = time.time()
            if not state["time"]:
                state["time"], state["bytes"] = now, done
                return
            span = now - state["time"]
            if span >= 0.25:
                task.speed = max(0.0, (done - state["bytes"]) / span)
                state["time"], state["bytes"] = now, done
                self.on_progress(task)

        return report

    def _download_file(self, task: DownloadTask) -> None:
        reporter = self._progress_reporter(task)
        for attempt in range(self.retries):
            if self._cancel.is_set():
                raise DownloadCancelled()
            try:
                self.session.download_file(
                    task.url, task.filepath, referer=task.referer,
                    progress=reporter,
                    cancel=lambda: self._task_cancelled(task),
                    headers=task.headers,
                    segments=self.segments,
                    chunk_size=256 * 1024)
                break
            except DownloadCancelled:
                raise
            except (NetworkError, OSError) as exc:
                if self._task_cancelled(task):
                    raise DownloadCancelled()
                if attempt + 1 >= self.retries:
                    raise
                code = getattr(exc, "code", 0) or 0
                if 400 <= code < 500 and code not in (408, 429):
                    raise          # 403/404 这类重试也没用，直接报错
                # 已写入部分会在下一次尝试里用 Range 续传
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
        if self._task_cancelled(task):
            raise DownloadCancelled()
        try:
            task.total_bytes = os.path.getsize(task.filepath)
        except OSError:
            pass
        task.done_bytes = task.total_bytes

    # ------------------------------------------------------------- yt-dlp
    def _download_ytdlp(self, task: DownloadTask) -> None:
        """交给 yt-dlp 子进程下载，解析百分比进度并支持取消。"""
        cmd = ytdlp.command()
        if not cmd:
            raise NetworkError("没有找到 yt-dlp，无法下载该条目")
        folder = os.path.dirname(os.path.abspath(task.filepath))
        if folder:
            os.makedirs(folder, exist_ok=True)
        full = cmd + ["--newline", "--no-colors", "--no-playlist", "--no-part",
                      "--concurrent-fragments", "4",
                      "-f", "b", "--merge-output-format", "mp4",
                      "-o", task.filepath, "--print", "after_move:filepath"]
        cookie = (task.cookie or "").strip()
        if cookie and "\n" not in cookie:
            full += ["--add-header", f"Cookie: {cookie}"]
        full.append(task.url)
        try:
            proc = subprocess.Popen(
                full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=(0x08000000 if os.name == "nt" else 0))
        except OSError as exc:
            raise NetworkError(f"无法启动 yt-dlp：{exc}") from exc
        percent_re = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
        final_path = ""
        last_report = 0.0
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self._task_cancelled(task):
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise DownloadCancelled()
                match = percent_re.search(line)
                if match:
                    percent = max(0.0, min(100.0, float(match.group(1))))
                    task.done_bytes = percent
                    task.total_bytes = 100
                    now = time.time()
                    if now - last_report >= 0.25:
                        last_report = now
                        self.on_progress(task)
                stripped = line.strip()
                if stripped and not stripped.startswith("[") \
                        and os.path.exists(stripped):
                    final_path = stripped
            code = proc.wait()
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
        if self._task_cancelled(task):
            raise DownloadCancelled()
        if code != 0:
            raise NetworkError(f"yt-dlp 下载失败（退出码 {code}）")
        if final_path and os.path.exists(final_path):
            task.filepath = final_path
        elif not os.path.exists(task.filepath):
            # 有些版本会把扩展名换成 .webm/.mkv，按文件名前缀兜底查找
            stem = os.path.splitext(os.path.basename(task.filepath))[0]
            for name in os.listdir(folder or "."):
                if name.startswith(stem):
                    task.filepath = os.path.join(folder or ".", name)
                    break
        if not os.path.exists(task.filepath):
            raise NetworkError("yt-dlp 已结束，但没有找到输出文件")
        task.total_bytes = task.done_bytes = os.path.getsize(task.filepath)

    # ---------------------------------------------------------------- HLS
    def _download_hls(self, task: DownloadTask) -> None:
        resp = self.session.peep(task.url, referer=task.referer, nbytes=4 << 20)
        if not resp.ok or not resp.body:
            raise NetworkError(f"清单获取失败: HTTP {resp.status}")
        text = resp.body.decode(resp.charset, errors="replace")
        playlist = streams.parse_manifest(text, resp.url or task.url,
                                           resp.content_type)
        if playlist.error:
            raise NetworkError(playlist.error)
        if playlist.is_master and playlist.variants:
            best = streams.best_variant(
                [v for v in playlist.variants if v.kind == "video"]
                or playlist.variants)
            if best is None:
                raise NetworkError("主清单中没有可用清晰度")
            sub = self.session.peep(best.url, referer=task.referer, nbytes=4 << 20)
            text = sub.body.decode(sub.charset, errors="replace")
            playlist = streams.parse_manifest(text, best.url, sub.content_type)
        if not playlist.segments:
            raise NetworkError("媒体清单中没有分片")

        target = task.filepath
        if playlist.segment_type == "m4s":
            target = re.sub(r"\.m3u8$", ".mp4", target, flags=re.I)
            if not target.lower().endswith(".mp4"):
                target += ".mp4"
        elif not target.lower().endswith((".ts", ".mp4")):
            target += ".ts"
        task.filepath = target

        folder = os.path.dirname(os.path.abspath(target)) or "."
        os.makedirs(folder, exist_ok=True)
        key_cache: dict[str, bytes] = {}
        key_lock = threading.Lock()
        total = len(playlist.segments)
        # 分片是纯网络 IO，线程数可以比文件并发更高；上限 8 避免把小站打挂
        workers = max(1, min(8, max(4, self.max_workers)))
        written = 0
        completed = 0
        started = time.time()

        pool = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="hls")
        pending: collections.deque = collections.deque()
        succeeded = False
        last_report = 0.0

        def cancel() -> bool:
            return self._task_cancelled(task)

        try:
            with open(target, "wb") as out:
                if playlist.init_segment:
                    chunk = self._fetch_segment(playlist.init_segment,
                                                task.referer, cancel=cancel)
                    out.write(chunk)
                    written += len(chunk)
                jobs = iter(enumerate(playlist.segments))

                def submit() -> None:
                    job = next(jobs, None)
                    if job is None:
                        return
                    index, url = job
                    pending.append((index, pool.submit(
                        self._fetch_segment_stream, url, task.referer, index,
                        playlist, key_cache, key_lock, cancel)))

                # 预取一个小窗口，既压满带宽又不会把整个清单读进内存
                for _ in range(workers * 2):
                    submit()
                while pending:
                    _index, future = pending.popleft()
                    if cancel():
                        raise DownloadCancelled()
                    data = future.result()
                    out.write(data)
                    written += len(data)
                    completed += 1
                    submit()
                    task.done_bytes = completed
                    task.total_bytes = total
                    elapsed = time.time() - started
                    if elapsed > 0.3:
                        task.speed = written / elapsed
                    # 分片可能上千个，进度事件按 0.2 秒节流，避免刷爆界面队列
                    now = time.time()
                    if completed == total or now - last_report >= 0.2:
                        last_report = now
                        self.on_progress(task)
            succeeded = True
        finally:
            for _index, future in pending:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            if not succeeded:
                # 中断/失败时不留半截文件，下次重新开始
                try:
                    os.remove(target)
                except OSError:
                    pass

        task.total_bytes = task.done_bytes = os.path.getsize(target)

    def _fetch_segment_stream(self, url: str, referer: str, index: int,
                              playlist, key_cache: dict,
                              key_lock: threading.Lock,
                              cancel: Optional[Callable[[], bool]] = None) -> bytes:
        """抓取单个分片并按需解密（在工作线程里运行）。"""
        if cancel and cancel():
            raise DownloadCancelled()
        data = self._fetch_segment(url, referer, cancel=cancel)
        key_info = _key_for(playlist.keys, index)
        if key_info and str(key_info.get("method", "")).upper() == "AES-128":
            uri = key_info.get("uri", "")
            if uri:
                key = self._segment_key(uri, referer, key_cache, key_lock)
                # IV 优先用清单声明的；否则按 RFC 8216 用媒体序号
                iv = key_info.get("iv") or b""
                if not iv:
                    seq = playlist.media_sequence + index
                    iv = seq.to_bytes(16, "big")
                data = aes128_cbc_decrypt(data, key, iv)
        return data

    def _segment_key(self, uri: str, referer: str, key_cache: dict,
                     key_lock: threading.Lock) -> bytes:
        with key_lock:
            key = key_cache.get(uri)
        if key is not None:
            return key
        result = self.session.peep(uri, referer=referer, nbytes=64)
        if not result.body:
            raise NetworkError(f"解密密钥获取失败: HTTP {result.status}")
        key = result.body[:16]
        with key_lock:
            key_cache[uri] = key
        return key

    def _fetch_segment(self, url: str, referer: str, retries: int = 3,
                       cancel: Optional[Callable[[], bool]] = None) -> bytes:
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            if (cancel and cancel()) or self._cancel.is_set():
                raise DownloadCancelled()
            try:
                resp = self.session.request(url, referer=referer,
                                            max_bytes=64 << 20,
                                            follow_redirects=5)
                if resp.status >= 400:
                    raise NetworkError(f"HTTP {resp.status} {url}")
                return resp.body
            except DownloadCancelled:
                raise
            except NetworkError as exc:
                last_err = exc
            if (cancel and cancel()) or self._cancel.is_set():
                raise DownloadCancelled()
            if attempt + 1 < retries:
                time.sleep(0.4 * (attempt + 1))
        raise NetworkError(f"分片下载失败: {last_err}")


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _key_for(keys: dict, index: int):
    """找到覆盖第 index 个分片的密钥（取 <= index 的最近一条）。"""
    best = None
    for start in sorted(keys):
        if start <= index:
            best = keys[start]
    return best


def _default_name(item) -> str:
    from .htmlparse import ensure_extension, guess_filename

    name = getattr(item, "filename", "") or guess_filename(getattr(item, "url", ""))
    if not name:
        name = "download"
    return ensure_extension(name, getattr(item, "kind", ""),
                            getattr(item, "fmt", ""))


def unique_path(path: str) -> str:
    """若文件已存在，自动加 (1)(2) 后缀。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    index = 1
    while True:
        candidate = f"{root} ({index}){ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1
