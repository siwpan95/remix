"""
视频音频合成处理器 --- 纯业务逻辑，零 GUI 依赖。

基于 ffmpeg/ffprobe 实现：
  - 视频元信息获取
  - 从视频中提取音频轨道
  - 替换视频音频轨道
  - 混音模式：原音频 + 新音频混合后合成到视频
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

from utils import generate_output_name
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ======================================================================
# 数据结构
# ======================================================================


class MuxMode(Enum):
    """音频合成模式"""
    REPLACE = auto()   # 替换原音频
    MIX = auto()       # 原音频 + 新音频混合


@dataclass
class VideoInfo:
    """视频文件完整元信息"""
    path: str
    duration_sec: float
    width: int
    height: int
    video_codec: str
    video_bitrate_kbps: int
    fps: float
    audio_codec: str
    audio_sample_rate: int
    audio_channels: int
    has_audio: bool
    file_size_mb: float

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def duration_str(self) -> str:
        m, s = divmod(int(self.duration_sec), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


# ======================================================================
# 处理器
# ======================================================================


class VideoProcessor:
    """视频音频合成处理器。

    依赖 ffmpeg/ffprobe（pydub 已要求安装 ffmpeg）。
    """

    # ------------------------------------------------------------------
    # 内置 ffmpeg / ffprobe（零外部依赖）
    # ------------------------------------------------------------------
    _BIN_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
    )
    _FFMPEG_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    _FFPROBE_EXE = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

    _FFMPEG_CACHED: Optional[str] = None
    _FFPROBE_CACHED: Optional[str] = None

    @classmethod
    def _find_ffmpeg(cls) -> str:
        """查找 ffmpeg（优先 bin/ 内置，fallback 系统 PATH）。"""
        if cls._FFMPEG_CACHED:
            return cls._FFMPEG_CACHED
        bundled = os.path.join(cls._BIN_DIR, cls._FFMPEG_EXE)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        def _test(exe: str) -> bool:
            try:
                r = subprocess.run(
                    [exe, "-version"],
                    capture_output=True, timeout=5,
                    creationflags=creationflags,
                )
                return r.returncode == 0
            except Exception:
                return False

        if os.path.isfile(bundled) and _test(bundled):
            cls._FFMPEG_CACHED = bundled
            return bundled

        if _test(cls._FFMPEG_EXE):
            cls._FFMPEG_CACHED = cls._FFMPEG_EXE
            return cls._FFMPEG_EXE

        raise RuntimeError(f"未找到 ffmpeg，请确认 bin/{cls._FFMPEG_EXE} 存在或已加入系统 PATH")

    @classmethod
    def _find_ffprobe(cls) -> str:
        """查找 ffprobe（优先 bin/ 内置，fallback 系统 PATH）。"""
        if cls._FFPROBE_CACHED:
            return cls._FFPROBE_CACHED
        bundled = os.path.join(cls._BIN_DIR, cls._FFPROBE_EXE)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        def _test(exe: str) -> bool:
            try:
                r = subprocess.run(
                    [exe, "-version"],
                    capture_output=True, timeout=5,
                    creationflags=creationflags,
                )
                return r.returncode == 0
            except Exception:
                return False

        if os.path.isfile(bundled) and _test(bundled):
            cls._FFPROBE_CACHED = bundled
            return bundled

        if _test(cls._FFPROBE_EXE):
            cls._FFPROBE_CACHED = cls._FFPROBE_EXE
            return cls._FFPROBE_EXE

        raise RuntimeError(f"未找到 ffprobe，请确认 bin/{cls._FFPROBE_EXE} 存在或已加入系统 PATH")

    @staticmethod
    def _video_has_audio(video_path: str) -> bool:
        """判断视频是否含有音轨（轻量 ffprobe 探测）。"""
        ffprobe = VideoProcessor._find_ffprobe()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [
            ffprobe, "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0", video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                creationflags=creationflags,
            )
        except Exception:
            return False
        return bool(result.stdout.strip())

    # ------------------------------------------------------------------
    # 视频信息
    # ------------------------------------------------------------------

    @staticmethod
    def get_video_info(video_path: str) -> VideoInfo:
        """用 ffprobe 获取视频文件完整元信息。

        Args:
            video_path: 视频文件路径。

        Returns:
            VideoInfo 对象。

        Raises:
            FileNotFoundError: 文件不存在。
            RuntimeError: ffprobe 未找到或解析失败。
        """
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        probe = VideoProcessor._find_ffprobe()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        cmd = [
            probe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe 执行失败: {result.stderr.strip()}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ffprobe 输出解析失败: {e}")

        # 解析流信息
        video_stream = None
        audio_stream = None
        for s in data.get("streams", []):
            codec_type = s.get("codec_type", "")
            if codec_type == "video" and video_stream is None:
                video_stream = s
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = s

        # 视频参数
        width = 0
        height = 0
        vcodec = ""
        fps = 0.0
        if video_stream:
            width = video_stream.get("width", 0)
            height = video_stream.get("height", 0)
            vcodec = video_stream.get("codec_name", "")
            # 帧率（优先 avg_frame_rate，其次 r_frame_rate）
            fps_str = video_stream.get("avg_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) != 0 else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0

        # 音频参数
        acodec = ""
        sample_rate = 0
        channels = 0
        if audio_stream:
            acodec = audio_stream.get("codec_name", "")
            sample_rate = int(audio_stream.get("sample_rate", 0))
            channels = int(audio_stream.get("channels", 0))

        # 封装层信息
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))
        size_bytes = int(fmt.get("size", 0))
        size_mb = round(size_bytes / (1024 * 1024), 2)
        bitrate_bps = int(fmt.get("bit_rate", 0))
        video_bitrate_kbps = bitrate_bps // 1000

        return VideoInfo(
            path=video_path,
            duration_sec=duration,
            width=width,
            height=height,
            video_codec=vcodec,
            video_bitrate_kbps=video_bitrate_kbps,
            fps=round(fps, 2),
            audio_codec=acodec,
            audio_sample_rate=sample_rate,
            audio_channels=channels,
            has_audio=audio_stream is not None,
            file_size_mb=size_mb,
        )

    # ------------------------------------------------------------------
    # 音频提取
    # ------------------------------------------------------------------

    @staticmethod
    def extract_audio(
        video_path: str,
        output_path: str | None = None,
        fmt: str = "wav",
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> str:
        """从视频文件中提取音频轨道。

        Args:
            video_path: 视频文件路径。
            output_path: 输出音频路径。为 None 时自动生成（同目录同名 + 格式后缀）。
            fmt: 输出格式（"wav"/"mp3"/"aac"/"ogg"）。
            sample_rate: 目标采样率（Hz），None 保持原采样率。
            channels: 目标通道数，None 保持原通道数。

        Returns:
            输出音频文件的绝对路径。

        Raises:
            FileNotFoundError: 视频文件不存在。
            RuntimeError: ffmpeg 执行失败。
        """
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_path is None:
            base, _ = os.path.splitext(video_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{base}_audio_{timestamp}.{fmt}"

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        ffmpeg = VideoProcessor._find_ffmpeg()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        cmd = [ffmpeg, "-i", video_path, "-vn"]  # -vn = 无视频

        if sample_rate:
            cmd += ["-ar", str(sample_rate)]
        if channels:
            cmd += ["-ac", str(channels)]

        if fmt == "wav":
            cmd += ["-acodec", "pcm_s16le"]
        elif fmt == "mp3":
            cmd += ["-acodec", "libmp3lame", "-q:a", "2"]
        elif fmt == "aac":
            cmd += ["-acodec", "aac", "-b:a", "192k"]
        elif fmt == "ogg":
            cmd += ["-acodec", "libvorbis", "-q:a", "4"]

        cmd += ["-y", output_path]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"音频提取失败:\n{result.stderr.strip()[-500:]}"
            )
        return os.path.abspath(output_path)

    # ------------------------------------------------------------------
    # 音频合成到视频
    # ------------------------------------------------------------------

    @staticmethod
    def mux_audio_video(
        video_path: str,
        audio_path: str,
        output_path: str | None = None,
        mode: MuxMode = MuxMode.REPLACE,
        mix_volume: float = 0.5,
        video_codec: str = "copy",
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
    ) -> str:
        """将音频合成到视频中（替换或混合）。

        Args:
            video_path: 源视频文件路径。
            audio_path: 要合成的音频文件路径。
            output_path: 输出路径。为 None 时自动生成（video_path 加 _mixed 后缀）。
            mode: 合成模式。
                - REPLACE: 完全替换原视频的音频轨道。
                - MIX: 原音频 + 新音频混合（如加背景音乐）。
            mix_volume: 混合模式下新音频的相对音量（0.0-1.0），用于控制背景音大小。
            video_codec: 视频编码器。"copy" 为无编码复制（最快），也可指定如 "libx264"。
            audio_codec: 输出音频编码器。"aac" 兼容性最好。
            audio_bitrate: 音频码率，如 "192k"。

        Returns:
            输出视频文件的绝对路径。

        Raises:
            FileNotFoundError: 输入文件不存在。
            ValueError: 无效的合成模式。
            RuntimeError: ffmpeg 执行失败。
        """
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        if output_path is None:
            base, ext = os.path.splitext(video_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{base}_mixed_{timestamp}{ext}"

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        ffmpeg = VideoProcessor._find_ffmpeg()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        if mode is MuxMode.REPLACE:
            cmd = [
                ffmpeg,
                "-i", video_path,
                "-i", audio_path,
                "-c:v", video_codec,
                "-c:a", audio_codec,
                "-b:a", audio_bitrate,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                "-y", output_path,
            ]

        elif mode is MuxMode.MIX:
            # 混合原音频 + 新音频。
            # 关键：filter_complex 的输出必须用 -map 显式映射到输出，
            # 否则混合出的音频轨道会被丢弃（之前版本就漏了这一步，
            # 导致「混入背景音」后视频仍然是静音）。
            vol_new = mix_volume
            if VideoProcessor._video_has_audio(video_path):
                # 原音频保持 100%，新音频按 mix_volume 缩放后混合
                filter_str = (
                    f"[0:a]volume=1.0[a0];"
                    f"[1:a]volume={vol_new}[a1];"
                    f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
                )
            else:
                # 视频本身无音轨：以指定音量直接混入背景音（等价于替换）
                filter_str = f"[1:a]volume={vol_new}[aout]"
            cmd = [
                ffmpeg,
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", filter_str,
                "-c:v", video_codec,
                "-c:a", audio_codec,
                "-b:a", audio_bitrate,
                "-map", "0:v:0",
                "-map", "[aout]",
                "-shortest",
                "-y", output_path,
            ]

        else:
            raise ValueError(f"未知合成模式: {mode}")

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"视频音频合成失败:\n{result.stderr.strip()[-500:]}"
            )
        return os.path.abspath(output_path)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    @staticmethod
    def extract_and_load(
        video_path: str,
        output_dir: str | None = None,
        fmt: str = "wav",
    ) -> str:
        """提取音频到指定目录并返回路径。

        便捷封装，自动处理输出路径命名。
        """
        if output_dir is None:
            output_dir = os.path.dirname(video_path) or "."

        base_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(output_dir, generate_output_name(base_name, "_audio", fmt))

        return VideoProcessor.extract_audio(video_path, output_path, fmt=fmt)
