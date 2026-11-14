"""直接双击运行（或 `python app.py`）。

本文件位于项目根目录，与 resource_sniffer 包同级，因此无需安装即可运行。
默认启动 WebView2 界面；如果环境里没有 pywebview / WebView2 运行时，
自动回退到原来的 Tk 界面，保证程序还能用。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _log_error(text: str) -> None:
    """打包成 exe 后没有控制台，把启动错误写到程序目录，方便排查。"""
    try:
        base = (os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False) else ROOT)
        path = os.path.join(base, "StarSniff.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except OSError:
        pass


def _run_web_ui() -> bool:
    """尝试启动网页界面；失败返回 False 交给 Tk 兜底。"""
    if os.environ.get("STARSNIFF_UI", "").lower() == "tk":
        return False
    try:
        from resource_sniffer import webbridge
    except Exception as exc:  # noqa: BLE001
        _log_error(f"[webui] import webbridge failed: {exc!r}")
        return False
    try:
        webbridge.run()
        return True
    except Exception:  # noqa: BLE001
        import traceback

        _log_error("[webui] webbridge.run failed: " + traceback.format_exc())
        return False


def _run_tk_ui() -> None:
    from resource_sniffer import theme
    from resource_sniffer.gui import MainWindow

    theme.enable_dpi_awareness()
    MainWindow().mainloop()


def main() -> None:
    if _run_web_ui():
        return
    _run_tk_ui()


if __name__ == "__main__":
    main()
