"""资源数据模型。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .mediainfo import KIND_LABEL as KindLabel, fmt_bitrate, fmt_duration, fmt_size


@dataclass
class ResourceItem:
    """一条嗅探到的资源。"""

    url: str = ""
    page_url: str = ""                 # 来源页面（作 Referer）
    kind: str = "other"                # image / audio / video / stream / other
    fmt: str = ""
    mime: str = ""
    filename: str = ""
    size: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0.0
    bitrate: int = 0
    estimate: bool = False
    thumbnail: Optional[bytes] = None
    source: str = ""                   # dom / network / html / css / manifest
    status: int = 0
    extra: str = ""                    # 附加说明，如 "HLS 主清单 · 4 个清晰度"
    child_of: str = ""                 # 若由清单展开而来，记录父清单 URL
    found_at: float = field(default_factory=time.time)
    checked: bool = False
    state: str = "ready"               # ready / querying / downloading / done / error
    progress: float = 0.0
    message: str = ""
    save_path: str = ""

    # ------------------------------------------------------------------ 展示
    @property
    def kind_label(self) -> str:
        return KindLabel.get(self.kind, self.kind)

    def preview_lines(self) -> tuple[str, str]:
        """返回 (预览主行, 预览副行)。"""
        if self.kind == "image":
            main = f"{self.width}×{self.height}" if self.width else "图片"
            sub = self.fmt.upper() or ""
            if not self.width:
                main = "加载中…" if self.state == "querying" else "尺寸未知"
            return main, sub
        if self.kind == "audio":
            main = fmt_duration(self.duration) or ("解析中…" if self.state == "querying" else "时长未知")
            return main, fmt_bitrate(self.bitrate)
        if self.kind in ("video", "stream"):
            main = f"{self.width}×{self.height}" if self.width else ("流媒体" if self.kind == "stream" else "视频")
            dur = fmt_duration(self.duration)
            sub = " · ".join(p for p in (dur, fmt_bitrate(self.bitrate)) if p)
            return main, sub
        if self.fmt:
            return self.fmt.upper(), ""
        return "文件", ""

    def info_lines(self) -> tuple[str, str]:
        """返回 (标题行, 详情行)。"""
        name = self.filename or self.url.rsplit("/", 1)[-1] or self.url
        size = fmt_size(self.size) if self.size else ""
        kind = self.kind_label
        head_bits = [kind]
        if self.width and self.height and self.kind == "image":
            head_bits.append(f"{self.width}×{self.height}")
        detail_bits = []
        if size:
            detail_bits.append(size)
        if self.bitrate:
            detail_bits.append(fmt_bitrate(self.bitrate))
        if self.duration and self.kind in ("audio", "video", "stream"):
            detail_bits.append(fmt_duration(self.duration))
        if self.extra:
            detail_bits.append(self.extra)
        if self.state == "downloading":
            detail_bits.append(f"下载中 {self.progress:.0f}%")
        elif self.state == "done":
            detail_bits.append("已下载")
        elif self.state == "error":
            detail_bits.append(self.message or "失败")
        title = f"[{'/'.join(head_bits)}] {name}"
        return title, " · ".join(detail_bits) + ("  " + self.url if not detail_bits else "")

    def search_key(self) -> str:
        return self.url.split("#", 1)[0]

    @property
    def referer(self) -> str:
        return self.page_url or ""


class ResourceList:
    """线程安全的资源集合（按 URL 去重）。"""

    def __init__(self) -> None:
        self._items: dict[str, ResourceItem] = {}
        self._lock = threading.RLock()
        self._order: list[str] = []

    def add(self, item: ResourceItem) -> tuple[ResourceItem, bool]:
        """加入列表；返回 (列表中的条目, 是否新增)。"""
        key = item.search_key()
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                # 补充更完整的信息
                for attr in ("size", "width", "height", "duration", "bitrate",
                             "thumbnail", "mime", "fmt"):
                    if not getattr(existing, attr) and getattr(item, attr):
                        setattr(existing, attr, getattr(item, attr))
                if item.source == "network" and existing.source != "network":
                    existing.source = "network"
                if item.extra and item.extra not in existing.extra:
                    existing.extra = "; ".join(p for p in (existing.extra, item.extra) if p)
                return existing, False
            self._items[key] = item
            self._order.append(key)
            return item, True

    def get(self, url: str) -> Optional[ResourceItem]:
        with self._lock:
            return self._items.get(url.split("#", 1)[0])

    def items(self) -> list[ResourceItem]:
        with self._lock:
            return [self._items[k] for k in list(self._order) if k in self._items]

    def remove(self, urls) -> int:
        """按 URL 从集合中移除若干条目，返回实际删除数量。"""
        keys = {u.split("#", 1)[0] for u in urls}
        with self._lock:
            gone = [k for k in self._items if k in keys]
            for k in gone:
                self._items.pop(k, None)
            if gone:
                self._order = [k for k in self._order if k in self._items]
            return len(gone)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()

    def checked(self) -> list[ResourceItem]:
        return [i for i in self.items() if i.checked]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def count_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items():
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts
