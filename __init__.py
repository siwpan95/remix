"""remixaudio —— 音频混音 & 视频音频合成（命名空间根包）。"""

import sys

# 兼容仓库「脚本模式」（根目录在 sys.path 上时 `from core.x` / `from gui.x` 可用）。
# 打包为命名空间包后顶层不再有 core/gui，这里把别名指回本包子模块，
# 使既有绝对导入在包模式下仍能解析。若用户环境已有同名顶层包则不动（setdefault）。
# 注意顺序：core/gui 在导入时依赖 utils，故 utils 必须先导入并注册别名。
for _alias, _sub in (("utils", "remixaudio.utils"), ("core", "remixaudio.core"), ("gui", "remixaudio.gui")):
    try:
        __import__(_sub)
        sys.modules.setdefault(_alias, sys.modules[_sub])
    except ImportError:
        pass
del _alias, _sub
