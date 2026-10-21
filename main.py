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
# 仅当项目内置 bin/ 存在时才加入 PATH（源码模式带 ffmpeg.exe；
# PyPI 安装的包内不含 bin/，跳过，转而走系统 PATH 自发现）
if os.path.isdir(_BIN_DIR):
    os.environ["PATH"] = _BIN_DIR + os.pathsep + os.environ.get("PATH", "")

# 内置 ffmpeg / ffprobe 路径（仅源码模式存在；PyPI 包内不含二进制）
_BUNDLED_FFMPEG = os.path.join(_BIN_DIR, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
_BUNDLED_FFPROBE = os.path.join(_BIN_DIR, "ffprobe.exe" if sys.platform == "win32" else "ffprobe")

import pydub  # noqa: E402

# ffmpeg / ffprobe 运行时自发现：优先系统 PATH 或项目内置 bin/（仅源码模式），
# 找不到时保留 pydub 默认查找，真正缺依赖时由 core.video_processor 抛出明确错误。
try:
    from core.video_processor import VideoProcessor
    pydub.AudioSegment.converter = VideoProcessor._find_ffmpeg()
    pydub.AudioSegment.ffprobe = VideoProcessor._find_ffprobe()
except Exception:
    pass

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
