"""
程序入口。
"""

import os
import sys

# =========================================================================
# pydub 使用项目内置 ffmpeg / ffprobe（bin/ 目录，零外部依赖）
# 必须在 import pydub 之前把 bin/ 加入 PATH，否则 import 时 pydub 找不到
# ffmpeg 会发出 RuntimeWarning。
# =========================================================================
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_BIN_DIR = os.path.join(_PROJECT_ROOT, "bin")
os.environ["PATH"] = _BIN_DIR + os.pathsep + os.environ.get("PATH", "")

import pydub  # noqa: E402

_BUNDLED_FFMPEG = os.path.join(_BIN_DIR, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
_BUNDLED_FFPROBE = os.path.join(_BIN_DIR, "ffprobe.exe" if sys.platform == "win32" else "ffprobe")

pydub.AudioSegment.converter = _BUNDLED_FFMPEG
pydub.AudioSegment.ffprobe = _BUNDLED_FFPROBE

# =========================================================================
# 依赖检查（在创建 tk 窗口前完成）
# =========================================================================
_MISSING = []

try:
    from pydub import AudioSegment  # noqa: F811
except ImportError:
    _MISSING.append("pydub")

# Python 3.13+ 移除了标准库 audioop，pydub 运行时依赖它，
# 需要通过 audioop-lts 补丁包提供。
try:
    import audioop
except ImportError:
    try:
        import audioop_lts  # audioop-lts 暴露的兼容模块
    except ImportError:
        _MISSING.append("audioop-lts")

if _MISSING:
    import tkinter.messagebox as mb

    msg = (
        "缺少依赖: " + ", ".join(_MISSING) + "\n\n"
        "请运行：pip install -r requirements.txt\n\n"
        "注意：\n"
        "  - 使用 Python 3.13+ 时必须安装 audioop-lts（pydub 依赖 audioop）。"
    )
    print("错误：" + msg, file=sys.stderr)
    # 尝试弹窗（如果 tk 可用）
    try:
        mb.showerror("缺少依赖", msg)
    except Exception:
        pass
    sys.exit(1)


def main():
    import tkinter as tk
    from gui.main_window import MixerApp

    root = tk.Tk()
    MixerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
