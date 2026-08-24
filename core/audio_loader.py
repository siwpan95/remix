"""
音频加载与参数统 —  纯业务逻辑，零 GUI 依赖。

负责：
  - 从磁盘加载音频文件（支持 mp3/wav/ogg/flac/m4a 等）
  - 检测音频参数与元信息
  - 统一多轨音频的参数以便混合
  - 静音修剪、格式推断
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Callable

from pydub import AudioSegment


# ======================================================================
# 数据结构
# ======================================================================


@dataclass(frozen=True)
class AudioParams:
    """音频参数的不可变容器。"""
    frame_rate: int
    channels: int
    sample_width: int


@dataclass
class AudioInfo:
    """音频文件的完整元信息。"""
    path: str
    duration_ms: int
    duration_sec: float
    params: AudioParams
    rms_dbfs: float
    peak_dbfs: float
    max_dbfs: float    # maximum possible = 0.0 for normalized


# ======================================================================
# 加载器
# ======================================================================


class AudioLoader:
    """加载并归一化音频文件参数。"""

    SUPPORTED_FORMATS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "wma", "aiff"}

    def __init__(self):
        self._segments: list[AudioSegment] = []
        self._paths: list[str] = []
        self._infos: list[AudioInfo] = []

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load(self, file_path: str) -> AudioSegment:
        """加载单个音频文件。"""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"音频文件不存在: {file_path}")
        return AudioSegment.from_file(file_path)

    def load_batch(
        self,
        paths: list[str],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[AudioSegment]:
        """批量加载音频文件，保留内部状态。"""
        self._paths = list(paths)
        self._segments = []
        self._infos = []

        for path in paths:
            seg = self.load(path)
            self._segments.append(seg)
            info = self.get_info(path, seg)
            self._infos.append(info)
            if on_progress:
                on_progress(f"已加载: {os.path.basename(path)}")

        return self._segments

    @property
    def segments(self) -> list[AudioSegment]:
        return self._segments

    @property
    def infos(self) -> list[AudioInfo]:
        return self._infos

    # ------------------------------------------------------------------
    # 参数检测与统一
    # ------------------------------------------------------------------

    @staticmethod
    def get_params(segment: AudioSegment) -> AudioParams:
        """获取音频段的参数快照。"""
        return AudioParams(
            frame_rate=segment.frame_rate,
            channels=segment.channels,
            sample_width=segment.sample_width,
        )

    @staticmethod
    def get_info(path: str, segment: AudioSegment | None = None) -> AudioInfo:
        """获取音频文件的完整元信息。"""
        if segment is None:
            segment = AudioSegment.from_file(path)

        dur_ms = len(segment)
        return AudioInfo(
            path=path,
            duration_ms=dur_ms,
            duration_sec=dur_ms / 1000.0,
            params=AudioLoader.get_params(segment),
            rms_dbfs=segment.dBFS,
            peak_dbfs=segment.max_dBFS if hasattr(segment, "max_dBFS") else segment.dBFS,
            max_dbfs=segment.max if hasattr(segment, "max") else 0.0,
        )

    @staticmethod
    def detect_common_params(segments: list[AudioSegment]) -> AudioParams:
        """自动选择公共参数。

        策略：以第一个音轨为准。若需要最高质量，可改用
        max(s.frame_rate) / max(s.sample_width) / max(s.channels)。
        """
        if not segments:
            raise ValueError("音频列表为空")
        ref = segments[0]
        return AudioParams(
            frame_rate=ref.frame_rate,
            channels=ref.channels,
            sample_width=ref.sample_width,
        )

    @staticmethod
    def detect_best_params(segments: list[AudioSegment]) -> AudioParams:
        """选择「最高质量」公共参数。

        取所有音轨中的最高采样率、最高位深、最多通道数。
        适合希望保留最高保真度的场景。
        """
        if not segments:
            raise ValueError("音频列表为空")
        return AudioParams(
            frame_rate=max(s.frame_rate for s in segments),
            channels=max(s.channels for s in segments),
            sample_width=max(s.sample_width for s in segments),
        )

    @staticmethod
    def unify(
        segments: list[AudioSegment],
        target: AudioParams | None = None,
    ) -> list[AudioSegment]:
        """将音频列表统一到相同参数。

        Args:
            segments: 待统一的音频段列表。
            target: 目标参数。为 None 时自动以第一个音轨为准。

        Returns:
            参数统一的 AudioSegment 列表（新对象，不修改原列表）。
        """
        if not segments:
            return []

        if target is None:
            target = AudioLoader.detect_common_params(segments)

        converted = []
        for seg in segments:
            seg = seg.set_frame_rate(target.frame_rate)
            seg = seg.set_channels(target.channels)
            seg = seg.set_sample_width(target.sample_width)
            converted.append(seg)

        return converted

    @staticmethod
    def extract_samples(segments: list[AudioSegment]) -> list[list[int]]:
        """从 AudioSegment 列表中提取 int 样本数组。"""
        return [list(seg.get_array_of_samples()) for seg in segments]

    # ------------------------------------------------------------------
    # 静音修剪
    # ------------------------------------------------------------------

    @staticmethod
    def trim_silence(
        segment: AudioSegment,
        silence_thresh: float = -50.0,
        min_silence_ms: int = 500,
        keep_margin_ms: int = 100,
    ) -> AudioSegment:
        """修剪音频首尾的静音部分。

        Args:
            segment: 输入音频段。
            silence_thresh: 静音阈值 (dBFS)，低于此值视为静音。
            min_silence_ms: 最短连续静音时长。
            keep_margin_ms: 保留的边距（避免切得太干净）。

        Returns:
            修剪后的 AudioSegment。
        """
        from pydub.silence import detect_leading_silence

        lead = detect_leading_silence(segment, silence_threshold=silence_thresh)
        rev = segment.reverse()
        trail = detect_leading_silence(rev, silence_threshold=silence_thresh)

        start = max(0, lead - keep_margin_ms)
        end = len(segment) - max(0, trail - keep_margin_ms)
        return segment[start:end]

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    @staticmethod
    def export(
        segment: AudioSegment,
        output_path: str,
        fmt: str | None = None,
        bitrate: str | None = None,
    ) -> str:
        """导出音频到文件。

        Args:
            segment: 要导出的 AudioSegment。
            output_path: 目标路径。
            fmt: 格式（"wav"/"mp3"/"ogg"/"flac"）。为 None 时从后缀推断。
            bitrate: 码率（仅对 mp3/ogg 等有效），如 "192k"。

        Returns:
            实际输出路径。
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if fmt is None:
            _, ext = os.path.splitext(output_path)
            fmt = ext.lstrip(".").lower()
            if not fmt:
                fmt = "wav"

        export_kw = {"format": fmt}
        if bitrate and fmt in ("mp3", "ogg", "aac"):
            export_kw["bitrate"] = bitrate

        segment.export(output_path, **export_kw)
        return output_path

    # ------------------------------------------------------------------
    # 静音生成
    # ------------------------------------------------------------------

    @staticmethod
    def make_silence(
        duration_ms: int,
        frame_rate: int = 44100,
        channels: int = 2,
        sample_width: int = 2,
    ) -> AudioSegment:
        """生成指定时长的静音段。"""
        return AudioSegment.silent(
            duration=duration_ms,
            frame_rate=frame_rate,
        ).set_channels(channels).set_sample_width(sample_width)

    # ------------------------------------------------------------------
    # 格式工具
    # ------------------------------------------------------------------

    @staticmethod
    def infer_format(path: str) -> str:
        """从文件路径推断音频格式。"""
        _, ext = os.path.splitext(path)
        fmt = ext.lstrip(".").lower()
        if fmt in AudioLoader.SUPPORTED_FORMATS:
            return fmt
        return "wav"  # fallback
