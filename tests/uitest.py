"""界面功能自检：不依赖人眼，通过控件树 / 命中测试 / 像素统计验证 GUI。

用法：python tests/uitest.py
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

# 关键：测试不得读写用户的真实配置
CFG_DIR = os.path.join(HERE, "_cfg")
os.environ["RS_CONFIG_DIR"] = CFG_DIR
import shutil  # noqa: E402

shutil.rmtree(CFG_DIR, ignore_errors=True)

from resource_sniffer import theme  # noqa: E402

PASS, FAIL = [], []
PORT = 8798


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}  {detail}")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pixel_stats(path: str) -> dict:
    """统计截图：主色占比、缩略图区域的彩色像素数量。"""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        raw = im.tobytes()
    total = w * h
    counts: dict[tuple, int] = {}
    colorful = 0
    for i in range(0, len(raw), 3):
        px = (raw[i], raw[i + 1], raw[i + 2])
        counts[px] = counts.get(px, 0) + 1
        mx, mn = max(px), min(px)
        if mx - mn > 40 and mx > 90:      # 明显有彩色（缩略图/徽标）
            colorful += 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    return {"size": (w, h), "top": [(c, n / total) for c, n in top],
            "colorful_ratio": colorful / total, "distinct": len(counts)}


def main() -> int:
    # Tk 用逻辑坐标、截图用物理像素；声明 DPI 感知后二者一致
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

    from resource_sniffer.gui import MainWindow

    app = MainWindow()
    app.geometry("1100x740+40+30")
    app.update()
    # 截图写到测试自己的临时目录，避免污染项目根目录
    shots = os.path.join(HERE, "_shots")
    os.makedirs(shots, exist_ok=True)

    def pump(seconds: float = 0.3):
        end = time.time() + seconds
        while time.time() < end:
            app.update_idletasks()
            app.update()
            time.sleep(0.02)

    try:
        print("\n== A. 启动与布局 ==")
        check("窗口标题正确", app.title() == "StarSniff 星嗅", app.title())
        check("主按钮文字为“开始”", app.btn_start._text == "开始")   # noqa: SLF001
        check("存在地址输入框", app.url_entry.winfo_exists() == 1)
        check("启动时输入框为空（不自动回填上次网址）",
              app.url_entry.value() == "", repr(app.url_entry.value()))
        check("存在资源列表", app.list_view.winfo_exists() == 1)
        check("存在下载按钮", app.btn_download.winfo_exists() == 1)
        check("主窗口为无边框窗口", bool(app.overrideredirect()))
        check("默认下载目录为配置目录下的 Download",
              os.path.normcase(app.download_dir)
              == os.path.normcase(os.path.join(CFG_DIR, "Download")),
              app.download_dir)
        check("输入框在主按钮左侧",
              app.url_entry.winfo_rootx() < app.btn_start.winfo_rootx())

        # 垂直顺序：地址栏 → 列表 → 底部按钮
        y_url = app.url_entry.winfo_rooty()
        y_list = app.list_view.winfo_rooty()
        y_dl = app.btn_download.winfo_rooty()
        check("自上而下：地址栏 → 列表 → 下载按钮", y_url < y_list < y_dl,
              f"{y_url} < {y_list} < {y_dl}")

        # 列表左侧预览、右侧信息
        canvas = app.list_view.canvas
        check("列表画布尺寸合理",
              canvas.winfo_width() > 400 and canvas.winfo_height() > 200,
              f"{canvas.winfo_width()}x{canvas.winfo_height()}")

        print("\n== A2. DPI 缩放适配 ==")
        scale = theme.SCALE
        check("检测到缩放比例", scale >= 1.0, f"{scale:.2f}x")
        check("行高按缩放放大",
              theme.ROW_HEIGHT == int(round(theme.BASE_ROW_HEIGHT * scale)),
              f"{theme.ROW_HEIGHT}px")
        check("缩略图尺寸按缩放放大",
              theme.THUMB_SIZE == int(round(theme.BASE_THUMB_SIZE * scale)),
              f"{theme.THUMB_SIZE}px")
        # 地址栏内容不能溢出（UA 下拉框必须完整可见）
        inner_w = app.url_entry.master.master.winfo_width()
        ua_right = (app.ua_dropdown.winfo_rootx()
                    - app.url_entry.master.master.winfo_rootx()
                    + app.ua_dropdown.winfo_width())
        check("UA 下拉框未溢出地址栏", ua_right <= inner_w,
              f"{ua_right} <= {inner_w}")
        check("开始按钮与 UA 下拉框不重叠",
              app.ua_dropdown.winfo_rootx() >=
              app.btn_start.winfo_rootx() + app.btn_start.winfo_width())
        # 分类栏能被完整容纳
        tabs_right = (app.tabs.winfo_rootx() - app.winfo_rootx()
                      + app.tabs.winfo_width())
        check("分类栏未超出窗口宽度", tabs_right <= app.winfo_width(),
              f"{tabs_right} <= {app.winfo_width()}")
        # 排序下拉框也必须完整容纳在筛选栏内
        sort_right = (app.sort_dropdown.winfo_rootx() - app.winfo_rootx()
                      + app.sort_dropdown.winfo_width())
        check("排序下拉框未超出窗口宽度", sort_right <= app.winfo_width(),
              f"{sort_right} <= {app.winfo_width()}")

        print("\n== A3. 窗口位置记忆 ==")
        original_geometry = app.geometry()
        saved_size = f"{theme.px(1000)}x{theme.px(700)}+40+30"
        app.cfg.window_geometry = saved_size
        app._restore_geometry()
        app.update_idletasks()
        app.update()
        restored = app.geometry()
        parsed = app._parse_geometry(restored)
        check("恢复上次窗口大小和位置",
              parsed is not None and parsed[0] == theme.px(1000)
              and abs(parsed[1] - theme.px(700)) <= 16
              and abs(parsed[2] - 40) <= 16 and abs(parsed[3] - 30) <= 16,
              f"{restored}（保存值 {saved_size}）")
        app._remember_geometry()
        check("关闭前记住窗口几何",
              app._parse_geometry(app.cfg.window_geometry) is not None,
              app.cfg.window_geometry)
        app.geometry(original_geometry)
        app.update()

        print("\n== A4. 自定义标题栏（无边框窗口） ==")
        check("主窗口使用自定义标题栏（无系统边框）",
              bool(app.overrideredirect()), "overrideredirect")
        check("标题栏含最小化/最大化/关闭按钮",
              all(hasattr(app, name) for name in
                  ("btn_min", "btn_max", "btn_close")))

        class _Ev:
            def __init__(self, xr, yr):
                self.x_root, self.y_root = xr, yr

        before = app.geometry()
        app._toggle_maximize()
        app.update_idletasks()
        app.update()
        check("最大化按钮生效", bool(app._maximized), app.geometry())
        app._toggle_maximize()
        app.update_idletasks()
        app.update()
        check("再次点击还原窗口",
              not app._maximized and app.geometry() == before,
              f"{app.geometry()} / {before}")

        x0, y0 = app.winfo_x(), app.winfo_y()
        app._title_press(_Ev(x0 + 300, y0 + 20))
        app._title_drag(_Ev(x0 + 340, y0 + 50))
        app.update_idletasks()
        app.update()
        moved = (app.winfo_x(), app.winfo_y()) == (x0 + 40, y0 + 30)
        check("拖动标题栏可移动窗口", moved,
              f"({x0},{y0}) -> ({app.winfo_x()},{app.winfo_y()})")

        w0, h0 = app.winfo_width(), app.winfo_height()
        app._resizer._on_press("se", _Ev(x0 + w0, y0 + h0))
        app._resizer._on_motion(_Ev(x0 + w0 + 80, y0 + h0 + 60))
        app.update_idletasks()
        app.update()
        check("拖动右下角可缩放窗口",
              app.winfo_width() > w0 and app.winfo_height() > h0,
              f"{w0}x{h0} -> {app.winfo_width()}x{app.winfo_height()}")
        app._resizer._on_release()
        app.geometry(before)
        app.update()

        print("\n== B. 空状态 ==")
        app.update_idletasks()
        app.update()
        from PIL import ImageGrab

        import tkinter as tk

        probe = tk.Frame(app, width=1, height=1, bg=theme.BG)
        probe.place(x=0, y=0, width=1, height=1)

        def grab(name):
            app.update_idletasks()
            app.update()
            app.lift()
            app.attributes("-topmost", True)
            app.update()
            x, y = probe.winfo_rootx(), probe.winfo_rooty()
            w, h = app.winfo_width(), app.winfo_height()
            path = os.path.join(shots, name)
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
            app.attributes("-topmost", False)
            return path

        empty = grab("01-空状态.png")
        stats = pixel_stats(empty)
        check("空状态有内容渲染（非纯色）", stats["distinct"] > 50,
              f"{stats['distinct']} 种颜色")
        check("空状态背景是深色主题",
              stats["top"][0][0] in (theme.BG, theme.PANEL)
              or sum(v for c, v in stats["top"] if sum(c) < 200) > 0.5,
              str(stats["top"][0]))

        print("\n== C. 真实嗅探 → 列表渲染 ==")
        app.url_entry.set_value(f"{base}/page.html")
        app.var_dynamic.set(False)
        app.start_sniff()
        for _ in range(300):
            if not app.sniffer.running:
                break
            pump(0.2)
        pump(0.5)
        count = len(app.list_view.all_items())
        check("嗅探结果进入列表", count >= 6, f"{count} 条")
        summary_text = str(app.status_label.cget("text"))
        check("嗅探完成提示含数量与大小",
              "嗅探完成" in summary_text and "已知大小" in summary_text,
              summary_text)
        kinds = {}
        for item in app.list_view.all_items():
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        check("列表含图片/音频/视频", all(kinds.get(k, 0) >= 1
                                        for k in ("image", "audio", "video")),
              str(kinds))
        check("状态栏显示所选线路", "线路：" in str(app.route_label.cget("text")),
              str(app.route_label.cget("text")))
        drawn = len(app.list_view.canvas.find_all())
        check("列表已绘制行内容", drawn > 20, f"{drawn} 个图元")
        thumbs = len(app.list_view._photos)                          # noqa: SLF001
        check("图片缩略图已载入", thumbs >= 2, f"{thumbs} 张")

        # 命中测试：点击第 2 行中间
        row_h = theme.ROW_HEIGHT
        y_mid = int(row_h * 1.5)
        idx = app.list_view._row_at(y_mid)                            # noqa: SLF001
        check("行命中测试正确", idx == 1, f"y={y_mid} -> 行 {idx}")

        class Ev:
            def __init__(self, x, y, state=0):
                self.x, self.y, self.state = x, y, state

        before = app.list_view.items[1].checked
        app.list_view._on_click(Ev(300, y_mid))                       # noqa: SLF001
        check("点击行可切换勾选",
              app.list_view.items[1].checked != before,
              f"{before} -> {app.list_view.items[1].checked}")
        check("底部统计随勾选更新", "已选 1 项" in app.count_label.cget("text"),
              app.count_label.cget("text"))
        app.list_view._on_click(Ev(300, y_mid))                       # noqa: SLF001

        result_shot = grab("02-嗅探结果.png")
        stats = pixel_stats(result_shot)
        check("结果页渲染出缩略图（存在彩色像素）",
              stats["colorful_ratio"] > 0.01,
              f"彩色像素 {stats['colorful_ratio'] * 100:.2f}%")

        print("\n== D. 勾选 / 筛选 / 全选 ==")
        app.list_view.select_all()
        pump(0.2)
        checked = app.list_view.checked_count()
        check("全选生效", checked == count, f"{checked}/{count}")
        check("统计显示已选数量", f"已选 {count} 项" in app.count_label.cget("text"),
              app.count_label.cget("text"))
        app.list_view.select_none()
        pump(0.1)
        app.list_view.canvas.focus_set()
        app._on_select_all_key()
        pump(0.1)
        check("Ctrl+A 快捷键全选", app.list_view.checked_count() == count,
              f"{app.list_view.checked_count()}/{count}")
        grab("03-勾选资源.png")

        app.tabs.select("image")
        pump(0.2)
        image_only = app.list_view.items
        check("按图片筛选只显示图片",
              bool(image_only) and all(i.kind == "image" for i in image_only),
              f"{len(image_only)} 条")
        app.tabs.select("all")
        pump(0.2)
        check("切回全部恢复完整列表", len(app.list_view.items) == count)

        print("\n== D2. 排序 ==")
        app.sort_dropdown.select("size")
        pump(0.25)
        sizes = [i.size or 0 for i in app.list_view.items]
        check("按大小降序排序", sizes == sorted(sizes, reverse=True),
              str(sizes[:6]))
        app.sort_dropdown.select("kind")
        pump(0.25)
        kinds = [i.kind for i in app.list_view.items]
        rank = {"image": 0, "audio": 1, "video": 2, "stream": 3}
        check("按类型排序",
              kinds == sorted(kinds, key=lambda k: rank.get(k, 9)),
              str(kinds))
        app.sort_dropdown.select("name")
        pump(0.25)
        names = [(i.filename or i.url).lower() for i in app.list_view.items]
        check("按文件名排序", names == sorted(names), str(names[:3]))
        app.sort_dropdown.select("default")
        pump(0.25)
        check("恢复默认顺序不丢条目", len(app.list_view.items) == count,
              f"{len(app.list_view.items)} 条")

        app.search_var.set("song")
        pump(0.2)
        check("搜索过滤生效",
              len(app.list_view.items) == 1
              and "song" in app.list_view.items[0].url,
              f"{len(app.list_view.items)} 条")
        app.search_var.set("")
        pump(0.2)

        print("\n== E. 下载入口 ==")
        app.list_view.select_all()
        captured = {}
        real_enqueue = app.downloader.enqueue

        def fake_enqueue(items, folder, namer=None):
            captured["items"] = list(items)
            captured["folder"] = folder
            return real_enqueue([], folder)      # 不真的下载

        app.downloader.enqueue = fake_enqueue
        app.download_dir = os.path.join(ROOT, "rs_uitest_dl")
        os.makedirs(app.download_dir, exist_ok=True)
        app.download()
        pump(0.2)
        check("点击下载读取到勾选项", len(captured.get("items", [])) == count,
              f"{len(captured.get('items', []))} 项")
        app.downloader.enqueue = real_enqueue

        # 已在下载队列里的链接不应重复入队
        captured.clear()
        app.downloader.enqueue = fake_enqueue
        for entry in app.list_view.checked_items():
            entry.state = "downloading"
        app.download()
        pump(0.2)
        check("已在队列的项不会重复入队",
              "items" not in captured
              and "重复" in str(app.status_label.cget("text")),
              str(app.status_label.cget("text")))
        for entry in app.list_view.all_items():
            entry.state = ""
        app.downloader.enqueue = real_enqueue

        print("\n== E2. 下载按钮 / 导出链接 ==")
        check("空闲时下载按钮为「下载」",
              getattr(app.btn_download, "_text", "") == "下载",
              getattr(app.btn_download, "_text", ""))

        class _BusyDownloader:
            busy = True

            def cancel_all(self):
                pass

        real_downloader = app.downloader
        app.downloader = _BusyDownloader()
        app._sync_download_button()
        check("下载中按钮切换为「停止下载」",
              getattr(app.btn_download, "_text", "") == "停止下载",
              getattr(app.btn_download, "_text", ""))
        app.downloader = real_downloader
        app._sync_download_button()
        check("队列结束后按钮恢复「下载」",
              getattr(app.btn_download, "_text", "") == "下载",
              getattr(app.btn_download, "_text", ""))

        import resource_sniffer.gui as gui_mod

        export_path = os.path.join(app.download_dir, "links_test.txt")
        real_save = gui_mod.filedialog.asksaveasfilename
        gui_mod.filedialog.asksaveasfilename = lambda **kw: export_path
        try:
            app._export_links(app.list_view.all_items()[:3])
        finally:
            gui_mod.filedialog.asksaveasfilename = real_save
        lines = []
        if os.path.exists(export_path):
            with open(export_path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        check("导出选中链接为 TXT",
              len(lines) == 3 and all(line.startswith("http") for line in lines),
              f"{len(lines)} 行")

        print("\n== F. 设置窗口 ==")
        app.open_settings()
        pump(0.6)
        toplevels = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
        check("设置窗口可以打开", len(toplevels) == 1, f"{len(toplevels)} 个")
        if toplevels:
            dlg = toplevels[0]
            grab("05-设置窗口.png")
            check("设置窗口尺寸合理",
                  dlg.winfo_width() > 400 and dlg.winfo_height() > 300,
                  f"{dlg.winfo_width()}x{dlg.winfo_height()}")
            check("设置窗口也是自定义标题栏",
                  bool(dlg.overrideredirect()), "overrideredirect")
            check("个性化页含全部主题预设",
                  len(getattr(dlg, "_cards", {})) == len(theme.PRESET_KEYS),
                  f"{len(getattr(dlg, '_cards', {}))} 个")
            dlg._choose_preset("retro")
            pump(0.25)
            check("选择复古主题并联动圆角",
                  dlg.var_preset.get() == "retro"
                  and int(dlg.var_radius_button.get()) == 0,
                  f"{dlg.var_preset.get()} / {dlg.var_radius_button.get()}")
            check("主题预览已绘制", len(dlg.preview.find_all()) > 5,
                  f"{len(dlg.preview.find_all())} 个图元")
            dlg._choose_accent("#34d399")
            pump(0.1)
            check("可以选择主题色", dlg.var_accent.get() == "#34d399",
                  dlg.var_accent.get())
            check("设置窗口有应用/取消按钮",
                  hasattr(dlg, "btn_apply") and hasattr(dlg, "btn_cancel")
                  and dlg.btn_apply._text == "应用"          # noqa: SLF001
                  and dlg.btn_cancel._text == "取消",        # noqa: SLF001
                  "应用 / 取消")
            dlg._choose_preset("default")
            pump(0.1)
            check("设置窗口有代理模式选择",
                  hasattr(dlg, "var_proxy_mode")
                  and dlg.var_proxy_mode.get() in ("auto", "proxy", "direct"),
                  getattr(getattr(dlg, "var_proxy_mode", None), "get", lambda: "?")(),
                  )
            check("设置窗口有代理测速按钮",
                  hasattr(dlg, "btn_proxy_test")
                  and str(dlg.btn_proxy_test.cget("text")) == "测速")
            check("设置窗口可调下载并发",
                  hasattr(dlg, "spin_concurrency")
                  and str(dlg.spin_concurrency.get()).isdigit(),
                  getattr(getattr(dlg, "spin_concurrency", None), "get",
                          lambda: "?")())
            check("设置窗口可调大文件加速连接数",
                  hasattr(dlg, "spin_segments")
                  and str(dlg.spin_segments.get()).isdigit(),
                  getattr(getattr(dlg, "spin_segments", None), "get",
                          lambda: "?")())
            dlg._show_page("sniff")
            pump(0.15)
            check("设置页有一键下载 yt-dlp 按钮",
                  hasattr(dlg, "btn_ytdlp")
                  and "下载" in str(dlg.btn_ytdlp.cget("text")),
                  getattr(getattr(dlg, "btn_ytdlp", None), "cget",
                          lambda _k: "?")("text"))
            dlg._show_page("appearance")
            # 手动测速走后台线程，结果回到界面，不应卡住设置窗口
            app.url_entry.set_value(f"{base}/page.html")
            dlg._test_route()
            for _ in range(60):
                pump(0.1)
                if "正在测速" not in str(dlg.test_result.cget("text")):
                    break
            text = str(dlg.test_result.cget("text"))
            check("设置窗口手动测速出结果",
                  "正在测速" not in text and ("ms" in text or "不可用" in text),
                  text[:60])
            dlg._show_page("about")
            pump(0.2)
            check("关于页含检查更新入口",
                  hasattr(dlg, "btn_check_update")
                  and hasattr(dlg, "update_status")
                  and hasattr(dlg, "var_check_updates"),
                  "check button / status")
            dlg.var_check_updates.set(False)
            dlg._auto_update_changed()
            pump(0.1)
            check("设置里能立即关闭自动检查更新",
                  app.cfg.check_updates is False
                  and "已关闭" in str(dlg.update_status.cget("text")),
                  dlg.update_status.cget("text"))
            dlg.var_check_updates.set(True)
            dlg._auto_update_changed()
            check("自动检查更新可以重新开启", app.cfg.check_updates is True)
            dlg.destroy()
        pump(0.2)

        print("\n== H. 分类栏计数（避免数字累加） ==")
        app.tabs.set_counts({"all": 12, "image": 8, "audio": 2, "video": 1,
                             "stream": 1})
        pump(0.1)
        first = app.tabs.label_of("all")
        for _ in range(5):
            app.tabs.set_counts({"all": 12, "image": 8, "audio": 2, "video": 1,
                                 "stream": 1})
            pump(0.02)
        again = app.tabs.label_of("all")
        check("多次刷新后页签文案不变", first == again == "全部 12",
              f"{first!r} -> {again!r}")
        app.tabs.set_counts({})
        pump(0.1)
        check("计数清零后只显示文字", app.tabs.label_of("all") == "全部",
              app.tabs.label_of("all"))
        app.tabs.set_counts({"all": 3})
        pump(0.1)
        check("计数恢复正确", app.tabs.label_of("all") == "全部 3",
              app.tabs.label_of("all"))

        print("\n== I. User-Agent 伪装 ==")
        from resource_sniffer import useragents
        from resource_sniffer.net import HttpSession

        expected = ["win_chrome", "win_firefox", "android_chrome", "ios_safari",
                    "mac_chrome", "linux_firefox", "linux_chrome"]
        keys = [p.key for p in useragents.PROFILES]
        check("内置 7 种 UA + 默认", all(k in keys for k in expected)
              and "auto" in keys, f"{len(keys)} 个")
        check("UA 下拉框在开始按钮右侧",
              app.ua_dropdown.winfo_rootx() > app.btn_start.winfo_rootx(),
              f"{app.ua_dropdown.winfo_rootx()} > {app.btn_start.winfo_rootx()}")
        check("下拉框默认显示", app.ua_dropdown.value() in keys,
              app.ua_dropdown.value())

        # 逐个验证：所选 UA 真的出现在请求头里
        import json as _json

        for key in expected:
            profile = useragents.get(key)
            session = HttpSession(user_agent=profile.ua,
                                  profile_headers=profile.headers())
            resp = session.peep(f"{base}/api/echo", nbytes=8192)
            try:
                echoed = _json.loads(resp.body.decode("utf-8"))
            except Exception:
                echoed = {}
            ok = echoed.get("ua") == profile.ua
            if ok and profile.brand:
                ok = bool(echoed.get("sec_ch_ua"))
                if profile.mobile:
                    ok = ok and echoed.get("sec_ch_ua_mobile") == "?1"
            check(f"请求头携带 {profile.label}", ok,
                  (echoed.get("ua") or "")[:46])

        app.ua_dropdown.select("android_chrome")
        pump(0.15)
        check("切换 UA 写入配置", app.cfg.user_agent == "android_chrome",
              app.cfg.user_agent)
        check("切换 UA 后嗅探参数生效",
              app._current_options().ua_key == "android_chrome",
              app._current_options().ua_key)
        app.ua_dropdown.select("auto")
        pump(0.1)

        print("\n== J. 异常与边界 ==")
        app.url_entry.set_value("")
        app.start_sniff()
        pump(0.2)
        check("空链接给出提示且不崩溃", "请先输入" in app.status_label.cget("text"),
              app.status_label.cget("text"))
        from resource_sniffer.net import normalize_url

        check("自动补全协议头",
              normalize_url("not-a-url.example") == "http://not-a-url.example/",
              normalize_url("not-a-url.example"))
        check("已带协议时不重复添加",
              normalize_url("https://a.com/x") == "https://a.com/x",
              normalize_url("https://a.com/x"))
        check("本机地址补全正确",
              normalize_url("127.0.0.1:8765/page") == "http://127.0.0.1:8765/page",
              normalize_url("127.0.0.1:8765/page"))
        app.list_view.clear()
        pump(0.2)
        check("清空列表不崩溃", len(app.list_view.items) == 0)
        app.download()
        pump(0.2)
        check("无勾选时下载给出提示", "勾选" in app.status_label.cget("text"),
              app.status_label.cget("text"))

        print("\n== K. 每套主题的独立布局 ==")
        for key in theme.PRESET_KEYS:
            app.cfg.theme_preset = key
            app._apply_preferences(app.cfg)
            pump(0.25)
            layout = theme.LAYOUT
            footer_bottom = (app.btn_download.winfo_rooty() - app.winfo_rooty()
                             + app.btn_download.winfo_height())
            check(f"{key} 布局已应用且底栏在窗口内",
                  theme.ACTIVE_PRESET == key
                  and app.btn_download.winfo_ismapped()
                  and footer_bottom <= app.winfo_height() + 2,
                  f"{layout.get('shell')} / footer={footer_bottom}")
            if key == "glass":
                check("液态玻璃：红黄绿灯 + 卡片列表",
                      hasattr(app, "traffic_lights")
                      and layout.get("list") == "cards"
                      and app.btn_start.winfo_width() >= theme.px(80),
                      "traffic lights / cards")
            elif key == "acrylic":
                check("亚克力：左侧导航栏 + 竖向筛选",
                      hasattr(app, "sidebar")
                      and app.tabs.__class__.__name__ == "SideTabs"
                      and app.sidebar.winfo_width() > theme.px(120),
                      app.tabs.__class__.__name__)
            elif key == "neon":
                check("霓虹：芯片式筛选",
                      getattr(app.tabs, "_filter_style", "") == "chips",
                      getattr(app.tabs, "_filter_style", "?"))
            elif key == "retro":
                check("复古：菜单栏 + 状态栏",
                      hasattr(app, "menubar")
                      and layout.get("footer") == "status"
                      and app.menubar.winfo_ismapped(),
                      "menubar / status")
        app.cfg.theme_preset = "default"
        app._apply_preferences(app.cfg)
        pump(0.2)

        print("\n== L. 更新检查不阻塞界面 ==")
        from resource_sniffer import updater

        real_check = updater.check_latest

        def _slow_check(timeout=8):
            time.sleep(1.2)
            raise updater.UpdateError("模拟慢网络", "network")

        updater.check_latest = _slow_check
        try:
            app._start_update_check(silent=True)
            started = time.time()
            app.open_settings()
            elapsed = time.time() - started
            pump(0.15)
            dialogs = [w for w in app.winfo_children()
                       if w.winfo_class() == "Toplevel"]
            check("检查更新期间打开设置不卡死",
                  elapsed < 0.8 and len(dialogs) >= 1,
                  f"{elapsed:.2f}s / {len(dialogs)} 个窗口")
            for dlg in dialogs:
                dlg.destroy()
        finally:
            updater.check_latest = real_check
            pump(0.3)

    finally:
        try:
            app.sniffer.stop()
            app.downloader.stop()
        except Exception:
            pass
        app.destroy()
        server.shutdown()
        import shutil

        shutil.rmtree(os.path.join(ROOT, "rs_uitest_dl"), ignore_errors=True)
        shutil.rmtree(CFG_DIR, ignore_errors=True)
        shutil.rmtree(os.path.join(HERE, "_shots"), ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  ✗ {name}")
    if not FAIL:
        print("全部通过 ✔")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
