# Remix — 音频混音 & 视频合成

## 仓库角色定位（投资组合分级 ①）

> 本仓在 siwpan 投资组合（D:/projects/repos，共 133 仓）中定级为 **① 核心推进**，领域 **音乐/音频**，定位 **本地音乐流水线**。
> **角色**：pydub 混音&视频音频合成，随流水线维护。
> **维护策略**：① 核心推进——持续投入、不允许冻结；随所属收敛点 / 流水线长期演进。
> 投资组合统一分级索引（多机可查）：[组合分级决策索引 · allin 事实源](https://gitee.com/siwpan/allin/blob/master/docs/事实源/组合分级决策索引.md)。

> 仓库：<https://gitee.com/siwpan/remix> ｜ 许可：MIT（见 [LICENSE](LICENSE)）

一个用 Python + pydub 实现的桌面音频混音器与视频音频合成工具。
支持多轨混音、分轨增益、软/硬削波、峰值/RMS 归一化、淡入淡出、
交叉淡化、动态压缩、高低通滤波，以及视频音频提取/替换/混入。

## ⚠️ 环境要求（重要）

### 1. ffmpeg 必须安装且在 PATH 中
pydub 只是 ffmpeg 的封装，**没有 ffmpeg 程序本工具无法运行**。

- 下载：https://ffmpeg.org/download.html
- 安装后确认命令行能执行 `ffmpeg -version` 和 `ffprobe -version`

### 2. Python 版本与 audioop
- **Python ≤ 3.12**：`pip install -r requirements.txt` 即可。
- **Python 3.13 / 3.14（及更高）**：标准库已移除 `audioop` 模块，
  而 pydub 运行依赖它。**必须安装 `audioop-lts` 补丁包**（已写入
  `requirements.txt`，但务必确认安装成功）。若缺少会报
  `ModuleNotFoundError: No module named 'audioop'`。

## 安装

```bash
# 建议使用虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install -r requirements.txt

# 验证依赖
python -c "import pydub, audioop; print('OK')"   # 3.13+ 应能看到 audioop 来自 audioop-lts
```

## 运行

```bash
python main.py
```

启动后有两个标签页：

- **音频混音**：添加多个音频文件 → 设置增益/削波/归一化/淡入淡出/交叉淡化/
  压缩/滤波 → 开始混音 → 输出到 `output/mixed_output.<fmt>`。
- **视频合成**：选择视频 → 添加（或提取）音频 → 选择替换原音轨或混入背景音
  → 合成导出。

## 功能列表

| 功能 | 说明 |
| --- | --- |
| 多轨混音 | 同时叠加多轨或交叉淡化串联 |
| 分轨增益 | 每轨独立 dB 增益 |
| 削波 | 硬削波（截断）/ 软削波（tanh 饱和） |
| 归一化 | 峰值归一化 / RMS 归一化 |
| 淡入淡出 | 整体首尾淡变 |
| 交叉淡化 | 轨间 equal-power 交叉淡化（串联模式） |
| 动态压缩 | 压缩动态范围 |
| 高通/低通 | 滤波截止频率可调 |
| 视频合成 | 提取/替换/混入视频音频 |

## 项目结构

```
remix/
├── main.py                 # 程序入口 + 依赖检查
├── core/
│   ├── audio_loader.py     # 加载 / 参数统一 / 导出
│   └── audio_mixer.py      # 混音引擎（纯业务逻辑，零 GUI 依赖）
├── gui/
│   └── main_window.py      # tkinter 主界面
├── utils/
│   └── progress_reporter.py# 线程安全进度报告
└── tests/                  # pytest 测试套件
```

## 测试

```bash
pip install pytest
pytest tests/ -v
```

> 注：涉及 GUI 的进度报告测试在缺少 tkinter / Tcl 环境时会自动跳过（skip），
> 不会失败。
