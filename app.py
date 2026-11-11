"""直接双击运行（或 `python app.py`）。

本文件位于项目根目录，与 resource_sniffer 包同级，因此无需安装即可运行。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 让 Pillow 等第三方库在「打包成 exe」的场景下也能被找到
try:
    from resource_sniffer import theme
    from resource_sniffer.gui import MainWindow
except ImportError as exc:      # pragma: no cover - 启动期兜底
    import tkinter.messagebox as mb

    mb.showerror("启动失败", f"无法加载程序模块：{exc}\n\n请确认已安装 Python 3.9+ 运行环境。")
    raise SystemExit(1)


def main() -> None:
    # 先声明 DPI 感知，再创建窗口：150% 缩放下文字才清晰、控件尺寸才正确
    theme.enable_dpi_awareness()
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
