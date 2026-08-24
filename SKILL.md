---
name: remix-audio
description: Maintain remix audio mixer (G:/tools/audio/remix). pydub-based mixing and video audio composition.
agent_created: true
---

# remix（音频混音 & 视频合成）维护

## 概述
pydub 桌面混音器：多轨混音、分轨增益、软/硬削波、峰值/RMS 归一化、淡入淡出、交叉淡化、动态压缩、高低通滤波 + 视频音频提取/替换/混入。

## 环境要求（关键）
- ffmpeg/ffprobe 必须在 PATH（pydub 只是封装）
- Python 3.13/3.14 必须装 audioop-lts（标准库已移除 audioop）

## 已知陷阱
- audio_pipeline 经 sys.path 注入调用本项目的 core/（audio_mixer/audio_loader）
- musickit remix 与之不同（musickit 是采样混音，本项目是 pydub 音频处理）
