"""
测试 audio_mixer — 混音引擎。

覆盖：
  - 手动混音（相加）
  - 硬削波 / 软削波
  - 峰值归一化 / RMS 归一化
  - 分轨增益
  - 样本→字节转换（修正原版 bytes() bug）
  - dBFS 测量
  - 边界条件（空输入、全零、极端值）
"""

from __future__ import annotations

import array
import math
import sys
import os

# 确保能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydub import AudioSegment

from core.audio_mixer import (
    AudioMixer,
    ClippingMode,
    NormalizeMode,
    MixEngine,
    MixConfig,
    TrackConfig,
)


# ======================================================================
# 测试辅助
# ======================================================================


def make_sine_samples(
    freq: float = 440.0,
    duration_ms: int = 500,
    frame_rate: int = 44100,
    amplitude: float = 0.5,
) -> list[int]:
    """生成正弦波样本列表（16-bit signed int）。"""
    n = int(frame_rate * duration_ms / 1000)
    peak = int(32767 * amplitude)
    return [
        int(peak * math.sin(2 * math.pi * freq * i / frame_rate))
        for i in range(n)
    ]


def make_silent_samples(n: int) -> list[int]:
    """生成全零样本。"""
    return [0] * n


def make_dc_samples(value: int, n: int) -> list[int]:
    """生成恒定值样本（用于测试削波/限幅）。"""
    return [value] * n


def make_sine_segment(
    freq: float = 440.0,
    duration_ms: int = 500,
    frame_rate: int = 44100,
    amplitude: float = 0.5,
    channels: int = 1,
    sample_width: int = 2,
) -> AudioSegment:
    """生成正弦波 AudioSegment 对象。"""
    samples = make_sine_samples(freq, duration_ms, frame_rate, amplitude)
    raw_bytes = AudioMixer._samples_to_bytes(samples, sample_width)
    return AudioSegment(
        data=raw_bytes,
        sample_width=sample_width,
        frame_rate=frame_rate,
        channels=channels,
    )


# ======================================================================
# 混音核心
# ======================================================================


class TestMixBasic:
    """基础混音测试。"""

    def test_empty_returns_empty(self):
        assert AudioMixer.mix([]) == []

    def test_single_track_identity(self):
        """单轨混音 ≈ 原样（削波后可能微调极值，大致不变）。"""
        arr = make_sine_samples(amplitude=0.8)
        result = AudioMixer.mix([arr], config=MixConfig(normalize_mode=NormalizeMode.NONE))
        assert len(result) == len(arr)
        # 正弦波峰值应该在正半周出现
        assert max(result) > 10000  # 0.8 * 32767 ≈ 26213
        assert min(result) < -10000
        # 起始应接近零（sin(0) = 0）
        assert abs(result[0]) < 2000

    def test_two_tracks_sum(self):
        """两轨直接相加，验证混合结果。"""
        arr1 = make_dc_samples(10000, 100)
        arr2 = make_dc_samples(5000, 100)
        result = AudioMixer.mix(
            [arr1, arr2],
            config=MixConfig(clip_mode=ClippingMode.HARD, normalize_mode=NormalizeMode.NONE),
        )
        assert result == [15000] * 100

    def test_align_shorter_to_longer(self):
        """短轨应与长轨对齐（短轨后面补零）。"""
        arr1 = make_dc_samples(10000, 200)
        arr2 = make_dc_samples(5000, 100)
        result = AudioMixer.mix(
            [arr1, arr2],
            config=MixConfig(clip_mode=ClippingMode.HARD, normalize_mode=NormalizeMode.NONE),
        )
        assert len(result) == 200
        # 前 100：10000+5000=15000，后 100：只有 arr1 = 10000
        assert result[:100] == [15000] * 100
        assert result[100:] == [10000] * 100


# ======================================================================
# 削波
# ======================================================================


class TestClipping:
    """削波算法测试。"""

    def test_hard_clip_above_max(self):
        """超过 S16_MAX 的值应被截断。"""
        arr = make_dc_samples(50000, 10)
        result = AudioMixer._hard_clip(arr)
        assert all(v <= 32767 for v in result)

    def test_hard_clip_below_min(self):
        """低于 S16_MIN 的值应被截断。"""
        arr = make_dc_samples(-50000, 10)
        result = AudioMixer._hard_clip(arr)
        assert all(v >= -32768 for v in result)

    def test_hard_clip_within_range(self):
        """范围内的值不变。"""
        arr = [0, 100, -200, 32767, -32768, 16000]
        result = AudioMixer._hard_clip(arr)
        assert result == arr

    def test_soft_clip_all_in_range(self):
        """软削波后的所有值应在 S16 范围内。"""
        arr = make_dc_samples(100000, 100)
        result = AudioMixer._soft_clip(arr)
        assert all(-32768 <= v <= 32767 for v in result)

    def test_soft_clip_saturation(self):
        """软削波应对极大值产生渐近饱和效果。"""
        arr = [0, 1000, 30000, 60000, 100000]
        result = AudioMixer._soft_clip(arr)
        # 使用 knee=1.2 时小值有轻微增益 (×1.2)，但仍在可控范围
        # v=1000 → tanh(1000/32767*1.2)*32767 ≈ 1199
        assert abs(result[1] - 1200) < 10  # 接近预期的 tanh 输出
        # 所有值应在 S16 范围内
        assert all(-32768 <= v <= 32767 for v in result)
        # 饱和：最大值应小于 32767（tanh(1.2)≈0.83, ×32767≈27264）
        assert max(result) < 32767


# ======================================================================
# 归一化
# ======================================================================


class TestNormalization:
    """归一化测试。"""

    def test_peak_normalize_silence(self):
        """全零不应该被放大。"""
        arr = [0] * 100
        result = AudioMixer._peak_normalize(arr)
        assert result == arr

    def test_peak_normalize_half_scale(self):
        """半幅信号应该被放大到目标峰值。"""
        arr = make_dc_samples(16384, 100)  # ~50%
        result = AudioMixer._peak_normalize(arr, target=0.95)
        expected = int(32767 * 0.95)
        assert all(abs(v - expected) <= 1 for v in result)

    def test_peak_normalize_already_loud(self):
        """已经很大的信号应被降低到目标峰值。"""
        arr = make_dc_samples(32000, 50)  # peak ≈ 97.7%
        result = AudioMixer._peak_normalize(arr, target=0.95)
        # 目标峰值 = 32767 * 0.95 = 31128
        expected = int(32767 * 0.95)
        assert all(abs(v - expected) <= 1 for v in result)

    def test_rms_normalize_silence(self):
        arr = [0] * 100
        result = AudioMixer._rms_normalize(arr)
        assert result == arr

    def test_rms_normalize_brings_to_target(self):
        """RMS 归一化应调整到目标 dBFS。"""
        arr = make_dc_samples(8000, 1000)  # RMS ≈ 8000
        result = AudioMixer._rms_normalize(arr, target_db=-3.0)
        rms_after = math.sqrt(sum(v * v for v in result) / len(result))
        rms_db = 20 * math.log10(rms_after / 32767)
        assert abs(rms_db - (-3.0)) < 2.0  # 软削波会引入微小偏差


# ======================================================================
# 增益
# ======================================================================


class TestGain:
    """增益测试。"""

    def test_zero_gain_no_change(self):
        arr = [100, 200, 300]
        assert AudioMixer._apply_gain(arr, 0) == arr

    def test_positive_gain(self):
        arr = [100, -200, 300]
        result = AudioMixer._apply_gain(arr, 6.0)  # +6 dB, 10^(6/20) ≈ 1.995
        # _apply_gain 不做削波，直接乘法取整
        # 100 * 1.995 ≈ 199.5 → 200
        # -200 * 1.995 ≈ -399.1 → -399
        # 300 * 1.995 ≈ 598.6 → 599
        assert abs(result[0] - 200) <= 1
        assert abs(result[1] + 399) <= 1
        assert abs(result[2] - 599) <= 1

    def test_negative_gain(self):
        arr = [1000, -2000, 3000]
        result = AudioMixer._apply_gain(arr, -6.0)  # -6 dB, 10^(-6/20) ≈ 0.501
        # 1000 * 0.501 ≈ 501.2 → 501
        # -2000 * 0.501 ≈ -1002.4 → -1002
        assert abs(result[0] - 501) <= 1
        assert abs(result[1] + 1002) <= 1


# ======================================================================
# 样本→字节转换（修正原版 bug）
# ======================================================================


class TestSamplesToBytes:
    """样本→字节打包测试——这是修正原版 bug 的核心。"""

    def test_16bit_roundtrip(self):
        """16-bit 样本转字节再转回，应一致。"""
        original = [0, 100, -200, 32767, -32768, 16000, -1]
        raw = AudioMixer._samples_to_bytes(original, 2)
        # 用 array 读回
        recovered = list(array.array("h", raw))
        assert recovered == original

    def test_bytes_method_no_longer_crashes(self):
        """原版 bytes(mixed) 的崩溃已被修复：
        bytes([300]) → ValueError
        bytes([-1]) → ValueError
        新方法不应在这里崩溃。
        """
        AudioMixer._samples_to_bytes([300], 2)   # 不应崩溃
        AudioMixer._samples_to_bytes([-1], 2)    # 不应崩溃
        AudioMixer._samples_to_bytes([1000], 2)  # 不应崩溃

    def test_8bit_conversion(self):
        """8-bit 音频应正确打包为 unsigned byte。"""
        samples = [0, 100, -100, 127]
        raw = AudioMixer._samples_to_bytes(samples, 1)
        recovered = list(array.array("B", raw))
        assert len(recovered) == len(samples)
        # 8-bit 用 unsigned 0-255，值会被 clamp
        assert all(0 <= v <= 255 for v in recovered)

    def test_unknown_width_raises(self):
        try:
            AudioMixer._samples_to_bytes([1], 3)
            assert False, "应抛出 ValueError"
        except ValueError:
            pass


# ======================================================================
# dBFS 测量
# ======================================================================


class TestDBFS:
    """dBFS 测量测试。"""

    def test_peak_dbfs_silence(self):
        assert AudioMixer.get_peak_dbfs([0] * 100) == float("-inf")

    def test_peak_dbfs_full_scale(self):
        dbfs = AudioMixer.get_peak_dbfs([32767])
        assert abs(dbfs - 0.0) < 0.01

    def test_peak_dbfs_half_scale(self):
        dbfs = AudioMixer.get_peak_dbfs([16384])
        assert abs(dbfs - (-6.02)) < 0.1  # -6 dB

    def test_rms_dbfs_silence(self):
        assert AudioMixer.get_rms_dbfs([0] * 100) == float("-inf")

    def test_rms_dbfs_dc(self):
        # DC 信号的 RMS 等于其绝对值
        samples = [16000] * 1000
        dbfs = AudioMixer.get_rms_dbfs(samples)
        expected = 20 * math.log10(16000 / 32767)
        assert abs(dbfs - expected) < 0.01


# ======================================================================
# MixConfig / TrackConfig
# ======================================================================


class TestMixConfig:
    """配置类测试。"""

    def test_default_config(self):
        cfg = MixConfig()
        assert cfg.clip_mode is ClippingMode.SOFT
        assert cfg.normalize_mode is NormalizeMode.RMS
        assert cfg.engine.value == MixEngine.MANUAL.value

    def test_track_gain_in_mix(self):
        """分轨增益应在混音中生效。"""
        arr1 = make_dc_samples(10000, 100)
        arr2 = make_dc_samples(10000, 100)

        cfg = MixConfig(
            clip_mode=ClippingMode.HARD,
            normalize_mode=NormalizeMode.NONE,
            tracks=[TrackConfig(gain_db=0), TrackConfig(gain_db=-6.0)],  # 第二轨 -6dB ≈ ×0.5
        )
        result = AudioMixer.mix([arr1, arr2], config=cfg)
        # arr1=10000, arr2≈5012, 相加=15012
        assert all(14950 <= v <= 15050 for v in result)


# ======================================================================
# mix_to_segment（端到端）
# ======================================================================


class TestMixToSegment:
    """端到端混音→AudioSegment 测试。"""

    def test_creates_valid_segment(self):
        arr1 = make_sine_samples(freq=440, amplitude=0.5)
        arr2 = make_sine_samples(freq=880, amplitude=0.3)

        seg = AudioMixer.mix_to_segment(
            [arr1, arr2],
            sample_width=2,
            frame_rate=44100,
            channels=1,
        )
        assert isinstance(seg, AudioSegment)
        assert len(seg) > 0
        assert seg.frame_rate == 44100
        assert seg.channels == 1


# ======================================================================
# mix_with_pydub（overlay 引擎）
# ======================================================================


class TestMixWithPydub:
    """pydub overlay 引擎测试。"""

    def test_overlay_mix(self):
        seg1 = make_sine_segment(freq=440, amplitude=0.5, duration_ms=500)
        seg2 = make_sine_segment(freq=880, amplitude=0.3, duration_ms=500)

        result = AudioMixer.mix_with_pydub([seg1, seg2])
        assert isinstance(result, AudioSegment)
        assert len(result) > 0

    def test_overlay_with_fade(self):
        seg1 = make_sine_segment(freq=440, amplitude=0.5, duration_ms=1000)
        seg2 = make_sine_segment(freq=880, amplitude=0.3, duration_ms=500)

        cfg = MixConfig(fade_in_ms=100, fade_out_ms=200, engine=MixEngine.OVERLAY)
        result = AudioMixer.mix_with_pydub([seg1, seg2], config=cfg)
        assert isinstance(result, AudioSegment)

    def test_overlay_with_gain(self):
        seg1 = make_sine_segment(freq=440, amplitude=0.5, duration_ms=500)
        seg2 = make_sine_segment(freq=880, amplitude=0.5, duration_ms=500)

        # 无增益，带归一化 → 作为参照
        cfg_no_gain = MixConfig(normalize_mode=NormalizeMode.NONE, engine=MixEngine.OVERLAY)
        ref = AudioMixer.mix_with_pydub([seg1, seg2], config=cfg_no_gain)

        # -10dB 增益，无归一化 → 应该比参照更安静
        cfg = MixConfig(
            tracks=[TrackConfig(gain_db=0), TrackConfig(gain_db=-10)],
            normalize_mode=NormalizeMode.NONE,
            engine=MixEngine.OVERLAY,
        )
        result = AudioMixer.mix_with_pydub([seg1, seg2], config=cfg)
        # -10dB 降低了第二轨音量，混合结果应比参照安静
        assert result.dBFS < ref.dBFS

    def test_overlay_with_compression(self):
        seg1 = make_sine_segment(freq=440, amplitude=0.8, duration_ms=500)
        seg2 = make_sine_segment(freq=880, amplitude=0.8, duration_ms=500)

        cfg = MixConfig(compress=True, engine=MixEngine.OVERLAY)
        result = AudioMixer.mix_with_pydub([seg1, seg2], config=cfg)
        assert isinstance(result, AudioSegment)

    def test_overlay_with_filter(self):
        seg = make_sine_segment(freq=2000, amplitude=0.5, duration_ms=500)
        cfg = MixConfig(
            low_pass_cutoff=500,
            high_pass_cutoff=100,
            engine=MixEngine.OVERLAY,
        )
        result = AudioMixer.mix_with_pydub([seg], config=cfg)
        assert isinstance(result, AudioSegment)


class TestCrossfade:
    """交叉淡化（顺序串联 + equal-power）测试。"""

    def test_crossfade_sequencing_shortens(self):
        """多轨交叉淡化应按串联长度拼接：sum(len) - crossfade*(n-1)。"""
        a = make_sine_samples(duration_ms=100, frame_rate=1000)   # 100 样本
        b = make_sine_samples(freq=880, duration_ms=100, frame_rate=1000)
        c = make_sine_samples(freq=330, duration_ms=100, frame_rate=1000)
        cfg = MixConfig(
            crossfade_ms=20,
            clip_mode=ClippingMode.HARD,
            normalize_mode=NormalizeMode.NONE,
        )
        result = AudioMixer.mix([a, b, c], config=cfg)
        # 100*3 - 20*2 = 260
        assert len(result) == 260
        assert all(-32768 <= v <= 32767 for v in result)

    def test_single_track_no_sequence(self):
        """单轨设 crossfade 不应串联，长度保持不变。"""
        a = make_sine_samples(duration_ms=200, frame_rate=1000)
        cfg = MixConfig(
            crossfade_ms=50,
            clip_mode=ClippingMode.HARD,
            normalize_mode=NormalizeMode.NONE,
        )
        result = AudioMixer.mix([a], config=cfg)
        assert len(result) == 200

    def test_crossfade_overlay_engine(self):
        """overlay 引擎的交叉淡化也应产出有效 AudioSegment。"""
        seg1 = make_sine_segment(duration_ms=300)
        seg2 = make_sine_segment(freq=880, duration_ms=300)
        cfg = MixConfig(crossfade_ms=50, engine=MixEngine.OVERLAY)
        result = AudioMixer.mix_with_pydub([seg1, seg2], config=cfg)
        assert isinstance(result, AudioSegment)
        assert len(result) > 0


# ======================================================================
# 边界 & 压力
# ======================================================================


class TestEdgeCases:
    """边界条件测试。"""

    def test_all_negative_samples(self):
        arr = make_dc_samples(-20000, 100)
        result = AudioMixer.mix(
            [arr, arr],
            config=MixConfig(clip_mode=ClippingMode.HARD, normalize_mode=NormalizeMode.NONE),
        )
        # 两轨相加 = -40000，硬削波到 -32768
        assert all(v == -32768 for v in result)

    def test_progress_callback_called(self):
        calls = []
        arr = make_dc_samples(1000, 1000)
        AudioMixer.mix(
            [arr],
            config=MixConfig(normalize_mode=NormalizeMode.NONE),
            progress_callback=lambda c, t: calls.append((c, t)),
        )
        # 削波阶段每 4096 样本回调一次
        assert len(calls) >= 1

    def test_very_long_mix(self):
        """大批量样本不应溢出或崩溃。"""
        arr = make_dc_samples(5000, 20000)
        result = AudioMixer.mix([arr, arr])
        assert len(result) == 20000
        assert all(-32768 <= v <= 32767 for v in result)
