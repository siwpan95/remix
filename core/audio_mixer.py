"""
音频混合引擎 — 纯业务逻辑，零 GUI 依赖。

支持：
  - 硬削波 / 软削波（tanh）两种模式
  - 峰值归一化 / RMS 归一化
  - 淡入淡出、交叉淡化
  - 分轨增益调节
  - 动态范围压缩、高低通滤波
  - 手动混音 / pydub overlay 两种引擎

所有函数均为纯函数或最小副作用，方便直接单测。
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np
from pydub import AudioSegment


# ======================================================================
# 枚举 & 配置
# ======================================================================


class ClippingMode(Enum):
    """削波模式"""

    HARD = auto()  # 硬削波：直接截断到 [−32768, 32767]
    SOFT = auto()  # 软削波：tanh 压缩，过渡更平滑


class NormalizeMode(Enum):
    """归一化模式"""

    NONE = auto()   # 不归一化
    PEAK = auto()   # 峰值归一化 → 拉到目标 peak
    RMS = auto()    # RMS 归一化 → 按均方根能量对齐


class MixEngine(Enum):
    """混音引擎"""

    MANUAL = auto()  # 手动逐样本相加（精确控制削波）
    OVERLAY = auto()  # pydub 内置 overlay()（简单可靠）


@dataclass
class TrackConfig:
    """单轨混音参数。"""
    gain_db: float = 0.0          # 分轨增益 (dB)


@dataclass
class MixConfig:
    """混音全局配置。"""
    clip_mode: ClippingMode = ClippingMode.SOFT
    normalize_mode: NormalizeMode = NormalizeMode.RMS
    engine: MixEngine = MixEngine.MANUAL

    # 淡入淡出
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    crossfade_ms: int = 0

    # 归一化目标
    peak_target: float = 0.95     # 峰值归一化目标 (0-1)
    rms_target_db: float = -3.0   # RMS 归一化目标 (dBFS)

    # 可选后处理
    compress: bool = False
    compress_threshold: float = -20.0
    compress_ratio: float = 4.0
    low_pass_cutoff: int = 0      # 低通滤波截止频率 (Hz)，0=关闭
    high_pass_cutoff: int = 0     # 高通滤波截止频率 (Hz)，0=关闭

    # 分轨配置
    tracks: list[TrackConfig] = field(default_factory=list)


# ======================================================================
# 混音器
# ======================================================================


class AudioMixer:
    """混音器 — 将多轨音频混合为单轨。"""

    # pydub 16-bit 采样范围
    _S16_MIN: int = -32768
    _S16_MAX: int = 32767

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    @classmethod
    def mix(
        cls,
        sample_arrays: list[list[int]],
        config: MixConfig | None = None,
        progress_callback=None,
    ) -> list[int]:
        """将多个样本数组混合为单个数组（手动引擎）。

        Args:
            sample_arrays: 各音轨的 int 样本列表（pydub 16-bit signed 格式）。
            config: 混音配置。为 None 时使用默认设置。
            progress_callback: 可选回调 (current, total) 用于进度报告。

        Returns:
            混合后的 int 样本列表。
        """
        if config is None:
            config = MixConfig()

        if not sample_arrays:
            return []

        # 交叉淡化：按 crossfade_ms 顺序串联多轨（轨间 equal-power 淡化）
        if config.crossfade_ms > 0 and len(sample_arrays) >= 2:
            return cls._mix_sequence(sample_arrays, config, progress_callback)

        # 1. 应用分轨增益
        arrays = list(sample_arrays)
        for i, arr in enumerate(arrays):
            if i < len(config.tracks) and config.tracks[i].gain_db != 0:
                arrays[i] = cls._apply_gain(arr, config.tracks[i].gain_db)

        # 2. numpy 向量化叠加（int64 累加，避免多轨叠加整数溢出）
        max_len = max(len(arr) for arr in arrays)
        mixed = np.zeros(max_len, dtype=np.int64)
        for arr in arrays:
            a = np.asarray(arr, dtype=np.int64)
            mixed[: len(a)] += a

        # 3. 削波
        if progress_callback:
            progress_callback(0, max_len)
        if config.clip_mode is ClippingMode.SOFT:
            mixed = cls._soft_clip(mixed)
        else:
            mixed = cls._hard_clip(mixed)
        if progress_callback:
            progress_callback(max_len, max_len)

        # 4. 归一化
        if config.normalize_mode is NormalizeMode.PEAK:
            mixed = cls._peak_normalize(mixed, config.peak_target)
        elif config.normalize_mode is NormalizeMode.RMS:
            mixed = cls._rms_normalize(mixed, config.rms_target_db)

        return mixed

    @classmethod
    def _mix_sequence(
        cls,
        sample_arrays: list[list[int]],
        config: MixConfig,
        progress_callback=None,
    ) -> list[int]:
        """顺序串联多轨，在衔接处做 equal-power 交叉淡化。

        当 ``MixConfig.crossfade_ms > 0`` 时由 :meth:`mix` 调用：轨道按时间顺序
        首尾相接，相邻轨道重叠 ``crossfade_ms`` 并做 equal-power 权重混合
        （cos² + sin² = 1，能量守恒，无突兀的增益跳变）。

        返回的样本列表仍会经过统一的削波与归一化处理。
        """
        arrays = list(sample_arrays)
        n = len(arrays)

        # 分轨增益
        for i, arr in enumerate(arrays):
            if i < len(config.tracks) and config.tracks[i].gain_db != 0:
                arrays[i] = cls._apply_gain(arr, config.tracks[i].gain_db)

        cf = max(config.crossfade_ms, 0)
        nparr = [np.asarray(a, dtype=np.float64) for a in arrays]

        # 计算每轨起始偏移：第 i 轨起点 = 前一轨终点 - crossfade
        offsets = [0]
        for i in range(1, n):
            offsets.append(offsets[i - 1] + len(nparr[i - 1]) - cf)
        # 防止短轨 + 长 crossfade 导致起点为负：整体平移使最小偏移为 0
        min_off = min(offsets)
        if min_off < 0:
            offsets = [o - min_off for o in offsets]
        total = offsets[-1] + len(nparr[-1])
        if total <= 0:
            total = max(len(a) for a in nparr)

        if progress_callback:
            progress_callback(0, total)

        mixed = np.zeros(total, dtype=np.float64)

        for i in range(n):
            arr = nparr[i]
            L = offsets[i]
            w = np.ones(len(arr), dtype=np.float64)

            # 头部淡入（与上一轨重叠区）
            if i > 0 and cf > 0:
                ov = min(cf, len(arr), len(nparr[i - 1]))
                t = np.linspace(0.0, 1.0, ov)
                w[:ov] = np.sin(t * np.pi / 2.0) ** 2

            # 尾部淡出（与下一轨重叠区）
            if i < n - 1 and cf > 0:
                ov = min(cf, len(arr), len(nparr[i + 1]))
                t = np.linspace(0.0, 1.0, ov)
                w[-ov:] = np.cos(t * np.pi / 2.0) ** 2

            mixed[L:L + len(arr)] += arr * w

        # 转回 int64 后走统一削波 / 归一化
        mixed = np.round(mixed).astype(np.int64)
        if progress_callback:
            progress_callback(total, total)

        if config.clip_mode is ClippingMode.SOFT:
            mixed = cls._soft_clip(mixed)
        else:
            mixed = cls._hard_clip(mixed)

        if config.normalize_mode is NormalizeMode.PEAK:
            mixed = cls._peak_normalize(mixed, config.peak_target)
        elif config.normalize_mode is NormalizeMode.RMS:
            mixed = cls._rms_normalize(mixed, config.rms_target_db)

        return mixed

    @classmethod
    def mix_to_segment(
        cls,
        sample_arrays: list[list[int]],
        sample_width: int,
        frame_rate: int,
        channels: int,
        config: MixConfig | None = None,
        progress_callback=None,
    ) -> AudioSegment:
        """混合并直接返回 pydub AudioSegment（手动引擎）。

        内部处理了 int → bytes 的正确转换，修正了原版
        ``bytes(list_of_ints)`` 当样本值 <0 或 >255 时崩溃的 bug。

        淡入淡出 / 压缩 / 滤波等后处理统一在此处完成（与 overlay 引擎一致），
        确保直接调用 core API 也能获得完整效果，而非依赖 GUI 层补救。
        """
        mixed = cls.mix(sample_arrays, config=config, progress_callback=progress_callback)
        raw_bytes = cls._samples_to_bytes(mixed, sample_width)
        seg = AudioSegment(
            data=raw_bytes,
            sample_width=sample_width,
            frame_rate=frame_rate,
            channels=channels,
        )

        # 后处理（与 overlay 引擎一致）：淡入淡出 / 压缩 / 滤波
        if config and config.fade_in_ms > 0:
            seg = seg.fade_in(config.fade_in_ms)
        if config and config.fade_out_ms > 0:
            seg = seg.fade_out(config.fade_out_ms)
        if config and config.compress:
            from pydub.effects import compress_dynamic_range
            seg = compress_dynamic_range(
                seg,
                threshold=config.compress_threshold,
                ratio=config.compress_ratio,
            )
        if config and config.low_pass_cutoff > 0:
            from pydub.effects import low_pass_filter
            seg = low_pass_filter(seg, config.low_pass_cutoff)
        if config and config.high_pass_cutoff > 0:
            from pydub.effects import high_pass_filter
            seg = high_pass_filter(seg, config.high_pass_cutoff)

        return seg

    @classmethod
    def mix_with_pydub(
        cls,
        segments: list[AudioSegment],
        config: MixConfig | None = None,
        progress_callback=None,
    ) -> AudioSegment:
        """使用 pydub 内置 overlay() / append() 混音（overlay 引擎）。

        这是更「官方」的混音方式，底层由 pydub/ffmpeg 处理。
        支持交叉淡化、淡入淡出、分轨增益、滤波、压缩等。
        """
        if config is None:
            config = MixConfig()

        if not segments:
            return AudioSegment.empty()

        n = len(segments)

        # 分轨增益
        for i in range(n):
            gain = 0.0
            if i < len(config.tracks):
                gain = config.tracks[i].gain_db
            if gain != 0:
                segments[i] = segments[i].apply_gain(gain)

        # 交叉淡化：按 crossfade_ms 顺序串联多轨（pydub append 自带 equal-power 淡化）
        if config.crossfade_ms > 0 and n >= 2:
            mixed = segments[0]
            for i in range(1, n):
                if progress_callback:
                    progress_callback(i, n)
                mixed = mixed.append(segments[i], crossfade=config.crossfade_ms)
        else:
            # 基础混合：pydub overlay 加法（同时叠加）
            mixed = segments[0]
            for i in range(1, n):
                seg = segments[i]
                if progress_callback:
                    progress_callback(i, n)
                mixed = mixed.overlay(seg)

        # 淡入淡出
        if config.fade_in_ms > 0:
            mixed = mixed.fade_in(config.fade_in_ms)
        if config.fade_out_ms > 0:
            mixed = mixed.fade_out(config.fade_out_ms)

        # 归一化
        if config.normalize_mode is NormalizeMode.PEAK:
            from pydub.effects import normalize
            headroom = (1.0 - config.peak_target)
            mixed = normalize(mixed, headroom=headroom)
        elif config.normalize_mode is NormalizeMode.RMS:
            # 按目标 RMS 调整
            mixed = cls._normalize_rms_pydub(mixed, config.rms_target_db)

        # 动态压缩
        if config.compress:
            from pydub.effects import compress_dynamic_range
            mixed = compress_dynamic_range(
                mixed,
                threshold=config.compress_threshold,
                ratio=config.compress_ratio,
            )

        # 滤波
        if config.low_pass_cutoff > 0:
            from pydub.effects import low_pass_filter
            mixed = low_pass_filter(mixed, config.low_pass_cutoff)
        if config.high_pass_cutoff > 0:
            from pydub.effects import high_pass_filter
            mixed = high_pass_filter(mixed, config.high_pass_cutoff)

        if progress_callback:
            progress_callback(1, 1)

        return mixed

    # ------------------------------------------------------------------
    # 归一化实现
    # ------------------------------------------------------------------

    @classmethod
    def _peak_normalize(cls, samples: list[int], target: float = 0.95) -> list[int]:
        """峰值归一化：拉到指定百分比满幅（向量化）。"""
        arr = np.asarray(samples, dtype=np.int64)
        peak = int(np.max(np.abs(arr))) if arr.size else 0
        if peak == 0 or peak >= cls._S16_MAX:
            return arr.tolist()

        target_peak = int(cls._S16_MAX * target)
        gain = target_peak / peak
        # 限制增益上限，防止极端静音信号被无限放大
        gain = min(gain, 32.0)
        # 应用增益 + 安全钳位到 S16 范围
        result = np.clip(
            np.round(arr * gain).astype(np.int64), cls._S16_MIN, cls._S16_MAX
        )
        return result.tolist()

    @classmethod
    def _rms_normalize(cls, samples: list[int], target_db: float = -3.0) -> list[int]:
        """RMS 归一化：按均方根能量对齐到目标 dBFS（向量化）。

        相比峰值归一化，RMS 归一化更贴近人耳感知响度。
        对播客、人声混合等场景效果更好。
        """
        arr = np.asarray(samples, dtype=np.int64)
        if arr.size == 0:
            return arr.tolist()

        # 当前 RMS（线性均方根）
        rms_current = np.sqrt(np.mean(arr.astype(np.float64) ** 2))
        if rms_current == 0:
            return arr.tolist()

        # 目标 RMS（线性值）
        rms_target = cls._S16_MAX * (10 ** (target_db / 20.0))
        gain = rms_target / rms_current

        # 应用增益 + 软削波保护（向量化）
        result = np.round(arr.astype(np.float64) * gain).astype(np.int64)
        return cls._soft_clip(result)

    @classmethod
    def _normalize_rms_pydub(cls, seg: AudioSegment, target_db: float) -> AudioSegment:
        """用 pydub 的 AudioSegment 做 RMS 归一化。"""
        current_db = seg.dBFS
        if current_db == float("-inf"):
            return seg
        delta = target_db - current_db
        return seg.apply_gain(delta)

    # ------------------------------------------------------------------
    # 增益
    # ------------------------------------------------------------------

    @classmethod
    def _apply_gain(cls, samples: list[int], gain_db: float) -> list[int]:
        """对样本数组施加 dB 增益。

        注意：不在此处削波——增益后可能超过 S16 范围的值
        会由后续 mix() 中的统一削波步骤处理。
        """
        if gain_db == 0:
            return samples
        multiplier = 10 ** (gain_db / 20.0)
        return [int(round(v * multiplier)) for v in samples]

    # ------------------------------------------------------------------
    # 削波算法
    # ------------------------------------------------------------------

    @classmethod
    def _hard_clip(cls, mixed) -> list[int]:
        """硬削波：直接截断到 16-bit 范围（向量化）。"""
        arr = np.asarray(mixed, dtype=np.int64)
        result = np.clip(arr, cls._S16_MIN, cls._S16_MAX)
        return result.astype(np.int64).tolist()

    @classmethod
    def _soft_clip(cls, mixed) -> list[int]:
        """软削波：用 tanh 曲线压缩，避免突兀的硬截断（向量化）。

        算法：
            先将样本归一化到 [-1, 1]，应用 tanh(k * norm)，再缩放回 16-bit 范围。
            - 小声量区域 (norm ≈ 0): tanh(x) ≈ x，基本保真
            - 大声量区域 (norm → ±1): tanh(1) ≈ 0.76，平滑饱和
            - k 控制膝点锐度：k=1 过渡最缓，k=2 更接近硬削波
        """
        arr = np.asarray(mixed, dtype=np.int64)
        knee = 1.2  # 膝点锐度：>1 更早进入饱和区
        norm = arr / cls._S16_MAX
        result = np.round(np.tanh(norm * knee) * cls._S16_MAX).astype(np.int64)
        return result.tolist()

    # ------------------------------------------------------------------
    # 字节打包
    # ------------------------------------------------------------------

    @classmethod
    def _samples_to_bytes(cls, samples: list[int], sample_width: int) -> bytes:
        """将 int 样本列表正确打包为原始音频字节。

        修正原版 ``bytes(mixed)`` 的 bug：
        - int 超出 0-255 时，bytes() 会抛 ValueError
        - 需要按 sample_width 使用正确字节序打包
        """
        if sample_width == 1:
            arr = array.array("B", [max(0, min(255, s + 128)) for s in samples])
        elif sample_width == 2:
            arr = array.array("h", [max(-32768, min(32767, s)) for s in samples])
        elif sample_width == 4:
            arr = array.array("i", samples)
        else:
            raise ValueError(f"不支持的采样宽度: {sample_width}")
        return arr.tobytes()

    # ------------------------------------------------------------------
    # 信息获取
    # ------------------------------------------------------------------

    @classmethod
    def get_peak_dbfs(cls, samples: list[int]) -> float:
        """获取样本数组的峰值 dBFS。"""
        peak = max(abs(v) for v in samples) if samples else 0
        if peak == 0:
            return float("-inf")
        return 20.0 * math.log10(peak / cls._S16_MAX)

    @classmethod
    def get_rms_dbfs(cls, samples: list[int]) -> float:
        """获取样本数组的 RMS dBFS。"""
        n = len(samples)
        if n == 0:
            return float("-inf")
        rms = math.sqrt(sum(v * v for v in samples) / n)
        if rms == 0:
            return float("-inf")
        return 20.0 * math.log10(rms / cls._S16_MAX)
