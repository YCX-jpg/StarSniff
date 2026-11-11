"""GUI 截图 + 版式校验：顺序驱动界面状态，逐步截图并做像素统计。

用法：python tests/guishot.py [输出目录]
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

# 截图测试同样使用独立配置目录
os.environ.setdefault("RS_CONFIG_DIR", os.path.join(HERE, "_cfg"))

from resource_sniffer import theme  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_shots")
PORT = 8799


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def region_stats(image, box) -> dict:
    """统计某个区域的亮度与色彩丰富度。"""
    crop = image.crop(box).convert("RGB")
    data = crop.tobytes()
    n = len(data) // 3
    if not n:
        return {"mean": 0, "colorful": 0.0, "distinct": 0}
    total = [0, 0, 0]
    colorful = 0
    colors = set()
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        total[0] += r
        total[1] += g
        total[2] += b
        colors.add((r, g, b))
        if max(r, g, b) - min(r, g, b) > 40 and max(r, g, b) > 90:
            colorful += 1
    return {"mean": sum(total) / (3 * n), "colorful": colorful / n,
            "distinct": len(colors)}


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    # Tk 报告的是逻辑坐标，GDI 截图用物理像素；声明 DPI 感知后两者才能对齐
    if sys.platform == "win32":
        try:
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    testsite = load("testsite", "site.py")
    testsite.build_assets()
    server = testsite.serve(PORT)
    base = f"http://127.0.0.1:{PORT}"

    from PIL import Image, ImageGrab

    from resource_sniffer.gui import MainWindow

    app = MainWindow()
    app.update()   # 窗口尺寸由程序按 DPI 决定

    # 兜底：任何情况下都不让截图脚本无限挂住
    def watchdog():
        print("\n[看门狗] 截图超时，强制退出")
        os._exit(2)

    app.after(120_000, watchdog)

    shots: list[tuple[str, object]] = []
    problems: list[str] = []

    def pump(seconds: float = 0.25) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.update_idletasks()
            app.update()
            time.sleep(0.02)

    # Tk 的 winfo_rootx/rooty 对顶层窗口会包含标题栏，用一个 1x1 探针取真实客户区原点
    import tkinter as tk

    probe = tk.Frame(app, width=1, height=1, bg=theme.BG)
    probe.place(x=0, y=0, width=1, height=1)

    def client_origin() -> tuple[int, int, int, int]:
        app.update_idletasks()
        return (probe.winfo_rootx(), probe.winfo_rooty(),
                app.winfo_width(), app.winfo_height())

    def grab(name: str, extra_windows=(), pad_top: int = 0) -> None:
        """截图。

        extra_windows: 需要一并纳入画面的附加窗口（如设置对话框），
                       会先把它提到最前，避免被主窗口盖住。
        pad_top: 向上扩展的像素（用于捕获弹出菜单）。
        """
        try:
            app.attributes("-topmost", True)
            for win in extra_windows:
                win.lift()
                win.update_idletasks()
                win.attributes("-topmost", True)
                win.focus_force()
            if not extra_windows:
                app.lift()
                app.focus_force()
        except Exception:
            pass
        pump(0.4)

        x, y, w, h = client_origin()
        x2, y2 = x + w, y + h
        for win in extra_windows:
            try:
                wx, wy = win.winfo_rootx(), win.winfo_rooty()
                wx2 = wx + win.winfo_width()
                wy2 = wy + win.winfo_height()
                x, y = min(x, wx), min(y, wy)
                x2, y2 = max(x2, wx2), max(y2, wy2)
            except Exception:
                pass
        y -= pad_top
        image = ImageGrab.grab(bbox=(x, y, x2, y2))
        path = os.path.join(OUT, name)
        image.save(path)
        shots.append((path, image))
        try:
            app.attributes("-topmost", False)
            for win in extra_windows:
                win.attributes("-topmost", False)
        except Exception:
            pass
        print(f"截图: {name}  {image.size}")

    # ---------------- 步骤定义
    steps = []

    def step(fn):
        steps.append(fn)
        return fn

    @step
    def empty_state():
        grab("01-空状态.png")

    @step
    def run_sniff():
        app.url_entry.set_value(f"{base}/page.html")
        app.var_dynamic.set(False)          # 沙箱内无法启动真实浏览器
        app.start_sniff()
        deadline = time.time() + 40
        while app.sniffer.running and time.time() < deadline:
            pump(0.15)
        pump(0.4)
        grab("02-嗅探结果.png")

    @step
    def check_all():
        app.list_view.select_all()
        pump(0.2)
        grab("03-勾选资源.png")

    @step
    def filter_images():
        app.tabs.select("image")
        pump(0.25)
        grab("04-筛选图片.png")
        app.tabs.select("all")
        pump(0.2)

    @step
    def settings_dialog():
        app.open_settings()
        pump(0.6)
        dialogs = [w for w in app.winfo_children()
                   if w.winfo_class() == "Toplevel"]
        grab("05-设置窗口.png", extra_windows=dialogs)
        for dlg in dialogs:
            dlg.destroy()
        pump(0.2)

    @step
    def long_list():
        """灌入大量条目，验证列表滚动与虚拟化性能。"""
        from resource_sniffer.models import ResourceItem

        start = time.time()
        for i in range(400):
            item = ResourceItem(
                url=f"{base}/assets/img/landscape.png?v={i}",
                page_url=f"{base}/page.html", kind="image", fmt="png",
                filename=f"示例图片_{i:03d}.png", size=30_000 + i,
                width=320, height=240, source="html")
            app.list_view.add_item(item)
        pump(0.5)
        cost = time.time() - start
        drawn = len(app.list_view.canvas.find_all())
        print(f"  灌入 400 条耗时 {cost:.2f}s，当前绘制 {drawn} 个图元")
        if cost > 6:
            problems.append(f"列表批量插入偏慢：{cost:.1f}s")
        grab("06-大量资源.png")

    @step
    def finish():
        app.sniffer.stop()
        app.downloader.stop()
        app.destroy()

    for fn in steps:
        try:
            fn()
        except Exception as exc:                 # 单步失败不影响后续截图
            print(f"  [步骤异常] {fn.__name__}: {exc}")
            problems.append(f"{fn.__name__}: {exc}")

    server.shutdown()

    # ---------------- 版式像素校验
    print("\n== 版式像素校验 ==")
    empty = next((im for p, im in shots if "01-" in p), None)
    result = next((im for p, im in shots if "02-" in p), None)
    if empty is not None and result is not None:
        w, h = empty.size
        # 顶栏区域应有主色 logo
        header = region_stats(empty, (0, 0, w, 54))
        print(f"  顶栏: 平均亮度 {header['mean']:.0f}, 彩色 {header['colorful'] * 100:.1f}%")
        if header["mean"] > 120:
            problems.append("顶栏过亮，不符合深色主题")
        if header["colorful"] < 0.001:
            problems.append("顶栏没有渲染出主色标识")

        # 列表区域应有内容（彩色缩略图）
        list_box = (20, 300, w - 20, h - 120)
        listing = region_stats(result, list_box)
        print(f"  列表区: 平均亮度 {listing['mean']:.0f}, "
              f"彩色 {listing['colorful'] * 100:.1f}%, 颜色数 {listing['distinct']}")
        if listing["colorful"] < 0.01:
            problems.append("列表区看不到缩略图色彩")
        if listing["distinct"] < 200:
            problems.append("列表区内容过于单调，可能未渲染")

        # 底部操作区应有按钮（存在主色或浅色块）
        footer = region_stats(result, (0, h - 110, w, h))
        print(f"  底部区: 平均亮度 {footer['mean']:.0f}, 彩色 {footer['colorful'] * 100:.1f}%")
        if footer["colorful"] < 0.002:
            problems.append("底部操作区没有渲染出按钮")
        if not (240 < h < 1600 and 640 < w < 3200):
            problems.append(f"窗口尺寸异常: {w}x{h}")

    print(f"\n共 {len(shots)} 张截图 -> {OUT}")
    if problems:
        print("发现 {0} 个问题:".format(len(problems)))
        for p in problems:
            print("  -", p)
        return 1
    print("版式校验通过 ✔")
    import shutil

    shutil.rmtree(os.path.join(HERE, "_cfg"), ignore_errors=True)
    if len(sys.argv) <= 1:      # 默认输出到临时目录，跑完即清理
        shutil.rmtree(OUT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
