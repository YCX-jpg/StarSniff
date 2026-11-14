"""性能主题的原生窗口：全部用 ttk / 系统控件，不画任何自绘元素。

为什么单独写一个：原来的界面是 Canvas 自绘（阴影、圆角、模糊都要自己画），
在弱显卡上每帧重绘很贵。这里用 ttk.Treeview + ttk.Entry + ttk.Progressbar，
渲染交给系统主题引擎，开销接近零。

只做核心流程：输入网址 → 嗅探 → 列表勾选 → 下载。
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, config as cfgmod, theme
from .downloader import DownloadManager, DownloadTask
from .engine import SniffOptions, Sniffer
from .models import ResourceItem
from .net import normalize_url

COLUMNS = (
    ("kind", "类型", 90),
    ("name", "名称", 420),
    ("size", "大小", 100),
    ("meta", "详情", 260),
)


class NativeWindow(tk.Tk):
    """极简原生窗口（性能主题）。"""

    def __init__(self) -> None:
        super().__init__()
        self.base_dir = (os.environ.get("RS_CONFIG_DIR", "").strip()
                         or cfgmod.app_dir())
        os.makedirs(self.base_dir, exist_ok=True)
        self.cfg = cfgmod.load(self.base_dir)
        self.download_dir = (self.cfg.download_dir
                             or cfgmod.default_download_dir(self.base_dir))

        self.title(f"StarSniff 星嗅 {__version__}")
        self.geometry(self.cfg.window_geometry or "1000x640")
        self.minsize(820, 520)

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.items: dict[str, ResourceItem] = {}
        self._pending: list[ResourceItem] = []
        self._pump_job = None

        self.sniffer = Sniffer(
            on_item=lambda item: self.events.put(("item", item)),
            on_log=lambda msg: self.events.put(("log", msg)),
            on_progress=lambda msg: self.events.put(("progress", msg)),
            on_done=lambda summary: self.events.put(("done", summary)),
        )
        self.downloader = DownloadManager(
            on_start=lambda t: self.events.put(("dl_start", t)),
            on_progress=lambda t: self.events.put(("dl_progress", t)),
            on_finish=lambda t: self.events.put(("dl_finish", t)),
            max_workers=self.cfg.concurrency,
            segments=getattr(self.cfg, "download_segments", 4),
        )

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pump()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # 顶栏
        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="StarSniff", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ttk.Label(top, text=f"  {__version__} · 性能模式",
                  foreground="#666").pack(side="left")
        ttk.Button(top, text="设置", command=self._open_settings).pack(side="right")
        ttk.Button(top, text="帮助", command=self._show_help).pack(side="right", padx=(0, 6))

        # 地址栏
        bar = ttk.Frame(root, padding=(0, 10, 0, 6))
        bar.pack(fill="x")
        self.url_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.url_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.start_sniff())
        self.btn_go = ttk.Button(bar, text="开始", command=self.start_sniff)
        self.btn_go.pack(side="left", padx=(8, 0))

        # 过滤 + 操作
        mid = ttk.Frame(root, padding=(0, 0, 0, 6))
        mid.pack(fill="x")
        ttk.Label(mid, text="筛选").pack(side="left")
        self.kind_var = tk.StringVar(value="全部")
        kind = ttk.Combobox(mid, textvariable=self.kind_var, width=8, state="readonly",
                            values=["全部", "图片", "音频", "视频", "流媒体"])
        kind.pack(side="left", padx=(6, 12))
        kind.bind("<<ComboboxSelected>>", lambda _e: self._refresh_rows())

        self.query_var = tk.StringVar()
        q = ttk.Entry(mid, textvariable=self.query_var, width=26)
        q.pack(side="left")
        q.bind("<KeyRelease>", lambda _e: self._refresh_rows())
        ttk.Button(mid, text="全选", command=lambda: self._select_all(True)).pack(side="left", padx=(10, 0))
        ttk.Button(mid, text="全不选", command=lambda: self._select_all(False)).pack(side="left", padx=(6, 0))
        ttk.Button(mid, text="清空", command=self._clear).pack(side="left", padx=(6, 0))

        # 列表
        wrap = ttk.Frame(root)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in COLUMNS],
                                 show="headings", selectmode="extended")
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w", stretch=(key == "name"))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_status())

        # 底栏
        foot = ttk.Frame(root, padding=(0, 8, 0, 0))
        foot.pack(fill="x")
        self.status_var = tk.StringVar(value="等待开始…")
        ttk.Label(foot, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(foot, length=180, mode="determinate",
                                        maximum=100)
        self.progress.pack(side="left", padx=12)
        self.btn_dl = ttk.Button(foot, text="下载选中", command=self.download)
        self.btn_dl.pack(side="right")
        ttk.Button(foot, text="打开目录",
                   command=lambda: self._open_path(self.download_dir)).pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------ 嗅探
    def start_sniff(self) -> None:
        if self.sniffer.running:
            self.sniffer.stop()
            self.btn_go.configure(text="开始")
            self.status_var.set("已停止")
            return
        url = normalize_url(self.url_var.get().strip())
        if not url:
            messagebox.showinfo("提示", "请先输入要嗅探的网址")
            return
        self.url_var.set(url)
        self._clear()
        self.btn_go.configure(text="停止")
        self.status_var.set("开始嗅探…")
        self.progress["value"] = 0
        opts = SniffOptions(
            deep=self.cfg.deep, dynamic=self.cfg.dynamic,
            dynamic_wait=self.cfg.dynamic_wait, scroll=self.cfg.scroll,
            max_items=self.cfg.max_items, min_image_side=self.cfg.min_image_side,
            kinds=tuple(self.cfg.kinds or ["image", "audio", "video", "stream"]),
            expand_manifests=True, workers=self.cfg.workers,
            browser_path=self.cfg.browser_path, headless=self.cfg.headless,
            timeout=self.cfg.timeout, probe_deadline=self.cfg.probe_deadline,
            ua_key=self.cfg.user_agent,
        )
        try:
            self.sniffer.start(url, opts, proxy=self.cfg.proxy,
                               cookie=self.cfg.cookie,
                               proxy_mode=self.cfg.proxy_mode)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"启动失败：{exc}")
            self.btn_go.configure(text="开始")

    def _clear(self) -> None:
        self.items.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    # ------------------------------------------------------------ 列表
    def _kind_label(self, item: ResourceItem) -> str:
        return {"image": "图片", "audio": "音频", "video": "视频",
                "stream": "流媒体"}.get(item.kind, "其它")

    def _row_values(self, item: ResourceItem) -> tuple:
        from .mediainfo import fmt_size

        name = item.filename or item.url.rsplit("/", 1)[-1] or item.url
        size = fmt_size(item.size) if item.size else ""
        main, sub = item.preview_lines()
        meta = " · ".join(p for p in (main, sub, item.extra) if p)
        return (self._kind_label(item), name, size, meta)

    def _add_item(self, item: ResourceItem) -> None:
        """先攒着，定时批量插入，避免一次几百条把 Treeview 卡住。"""
        self.items[item.url] = item
        self._pending.append(item)
        if len(self._pending) >= 40:
            self._flush_pending()

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        for item in batch:
            try:
                self.tree.insert("", "end", iid=item.url,
                                 values=self._row_values(item))
            except tk.TclError:
                pass
        self._update_status()

    def _refresh_rows(self) -> None:
        kind = self.kind_var.get()
        query = self.query_var.get().strip().lower()
        wanted = {"图片": "image", "音频": "audio", "视频": "video",
                  "流媒体": "stream"}.get(kind)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for url, item in self.items.items():
            if wanted and item.kind != wanted:
                continue
            name = (item.filename or item.url).lower()
            if query and query not in name and query not in url.lower():
                continue
            self.tree.insert("", "end", iid=url, values=self._row_values(item))
        self._update_status()

    def _select_all(self, on: bool) -> None:
        self.tree.selection_set(self.tree.get_children()) if on else self.tree.selection_remove(self.tree.get_children())

    def _update_status(self) -> None:
        picked = len(self.tree.selection())
        total = len(self.tree.get_children())
        self.status_var.set(f"共 {total} 条，已选 {picked} 条")

    # ------------------------------------------------------------ 下载
    def download(self) -> None:
        urls = list(self.tree.selection())
        if not urls:
            messagebox.showinfo("提示", "请先在列表里选中要下载的资源")
            return
        picks = [self.items[u] for u in urls if u in self.items]
        self.downloader.enqueue(picks, self.download_dir)
        self.downloader.start()
        self.status_var.set(f"开始下载 {len(picks)} 个文件")

    # ------------------------------------------------------------ 事件泵
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "item":
                    self._add_item(payload)
                elif kind in ("log", "progress"):
                    self.status_var.set(str(payload))
                elif kind == "done":
                    self._flush_pending()
                    self.btn_go.configure(text="开始")
                    self.progress["value"] = 100
                    self._update_status()
                elif kind == "dl_progress":
                    self.progress["value"] = payload.progress
                    self.status_var.set(
                        f"下载中 {payload.progress:.0f}% · {payload.title}")
                elif kind == "dl_finish":
                    self.progress["value"] = 100
                    self.status_var.set(
                        "下载完成" if payload.state == "done" else f"失败：{payload.error}")
        except queue.Empty:
            pass
        self._pump_job = self.after(60, self._pump)

    # ------------------------------------------------------------ 其它
    def _open_path(self, path: str) -> None:
        try:
            os.startfile(path)  # noqa: S606
        except OSError:
            pass

    def _open_settings(self) -> None:
        from .prefs import SettingsDialog

        SettingsDialog(self, self.cfg, self._apply_settings, self._save_config)

    def _show_help(self) -> None:
        messagebox.showinfo(
            "帮助",
            "1. 粘贴网页地址，点「开始」嗅探\n"
            "2. 列表里选中要下载的资源（可多选）\n"
            "3. 点「下载选中」保存到设定目录\n\n"
            "性能模式：界面全部使用系统控件，不绘制阴影/模糊，弱显卡也不卡。")

    def _apply_settings(self, _cfg) -> None:
        self._save_config()
        self.downloader.segments = max(1, min(8, int(self.cfg.download_segments or 1)))

    def _save_config(self) -> None:
        cfgmod.save(self.cfg, self.base_dir)

    def _on_close(self) -> None:
        try:
            if self._pump_job is not None:
                self.after_cancel(self._pump_job)
        except tk.TclError:
            pass
        try:
            self.sniffer.stop()
            self.downloader.stop()
        except Exception:  # noqa: BLE001
            pass
        cfgmod.save(self.cfg, self.base_dir)
        self.destroy()


def run() -> None:
    theme.enable_dpi_awareness()
    app = NativeWindow()
    app.mainloop()
