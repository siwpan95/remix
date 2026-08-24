"""
测试 audio_loader — 音频加载与参数管理。

覆盖：
  - 参数检测（get_params / detect_common_params / detect_best_params）
  - 参数统一（unify）
  - 样本提取（extract_samples）
  - 静音修剪（trim_silence）
  - 导出 / 格式推断
  - AudioInfo 元信息
  - 边界条件
"""

from __future__ import annotations

import array
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydub import AudioSegment

from core.audio_loader import AudioLoader, AudioParams, AudioInfo


# ======================================================================
# 测试辅助
# ======================================================================


def make_sine_segment(
    freq: float = 440.0,
    duration_ms: int = 500,
    frame_rate: int = 44100,
    amplitude: float = 0.5,
    channels: int = 1,
    sample_width: int = 2,
) -> AudioSegment:
    """生成正弦波 AudioSegment 对象。"""
    import math as _m
    n = int(frame_rate * duration_ms / 1000)
    peak = int(32767 * amplitude)
    samples = array.array("h", [
        int(peak * _m.sin(2 * _m.pi * freq * i / frame_rate))
        for i in range(n)
    ])
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=sample_width,
        frame_rate=frame_rate,
        channels=channels,
    )


def make_silent_segment(
    duration_ms: int = 500,
    frame_rate: int = 44100,
    channels: int = 1,
    sample_width: int = 2,
) -> AudioSegment:
    """生成静音段。"""
    n = int(frame_rate * duration_ms / 1000)
    samples = array.array("h", [0] * n)
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=sample_width,
        frame_rate=frame_rate,
        channels=channels,
    )


# ======================================================================
# 参数检测
# ======================================================================


class TestAudioParams:
    """AudioParams 测试。"""

    def test_dataclass_immutable(self):
        p = AudioParams(44100, 2, 2)
        assert p.frame_rate == 44100
        assert p.channels == 2
        assert p.sample_width == 2

    def test_equality(self):
        p1 = AudioParams(44100, 2, 2)
        p2 = AudioParams(44100, 2, 2)
        p3 = AudioParams(48000, 2, 2)
        assert p1 == p2
        assert p1 != p3


class TestGetParams:
    """get_params 测试。"""

    def test_stereo_44100_16bit(self):
        seg = make_sine_segment(freq=440, frame_rate=44100, channels=2, sample_width=2)
        p = AudioLoader.get_params(seg)
        assert p.frame_rate == 44100
        assert p.channels == 2
        assert p.sample_width == 2

    def test_mono_48000_16bit(self):
        seg = make_sine_segment(freq=440, frame_rate=48000, channels=1, sample_width=2)
        p = AudioLoader.get_params(seg)
        assert p.frame_rate == 48000
        assert p.channels == 1
        assert p.sample_width == 2


class TestDetectParams:
    """公共参数检测测试。"""

    def test_empty_raises(self):
        try:
            AudioLoader.detect_common_params([])
            assert False
        except ValueError:
            pass

    def test_common_uses_first(self):
        seg1 = make_sine_segment(frame_rate=44100, channels=2, sample_width=2)
        seg2 = make_sine_segment(frame_rate=48000, channels=1, sample_width=2)
        p = AudioLoader.detect_common_params([seg1, seg2])
        # 以第一个为准
        assert p.frame_rate == 44100
        assert p.channels == 2
        assert p.sample_width == 2

    def test_best_uses_max(self):
        seg1 = make_sine_segment(frame_rate=44100, channels=1, sample_width=2)
        seg2 = make_sine_segment(frame_rate=48000, channels=2, sample_width=2)
        p = AudioLoader.detect_best_params([seg1, seg2])
        assert p.frame_rate == 48000
        assert p.channels == 2
        assert p.sample_width == 2


# ======================================================================
# 参数统一
# ======================================================================


class TestUnify:
    """unify 测试。"""

    def test_empty_returns_empty(self):
        assert AudioLoader.unify([]) == []

    def test_unify_to_ref(self):
        seg1 = make_sine_segment(frame_rate=44100, channels=1, sample_width=2)
        seg2 = make_sine_segment(frame_rate=48000, channels=2, sample_width=2)
        result = AudioLoader.unify([seg1, seg2])
        assert len(result) == 2
        # 都统一到 seg1 的参数
        for s in result:
            assert s.frame_rate == 44100
            assert s.channels == 1
            assert s.sample_width == 2

    def test_unify_to_custom_target(self):
        seg = make_sine_segment(frame_rate=44100, channels=2, sample_width=2)
        target = AudioParams(22050, 1, 2)
        result = AudioLoader.unify([seg], target=target)
        s = result[0]
        assert s.frame_rate == 22050
        assert s.channels == 1
        assert s.sample_width == 2

    def test_unify_preserves_length(self):
        seg = make_sine_segment(duration_ms=1000, frame_rate=44100)
        result = AudioLoader.unify([seg])
        # 帧率不变的情况下，长度应接近
        assert abs(len(result[0]) - 1000) < 50


# ======================================================================
# 样本提取
# ======================================================================


class TestExtractSamples:
    """extract_samples 测试。"""

    def test_empty(self):
        assert AudioLoader.extract_samples([]) == []

    def test_mono_returns_ints(self):
        seg = make_sine_segment(duration_ms=100, amplitude=0.5)
        arrays = AudioLoader.extract_samples([seg])
        assert len(arrays) == 1
        assert len(arrays[0]) > 0
        assert isinstance(arrays[0][0], int)
        # 正弦波应有正有负
        assert any(v > 0 for v in arrays[0])
        assert any(v < 0 for v in arrays[0])

    def test_all_in_16bit_range(self):
        seg = make_sine_segment(duration_ms=100, amplitude=1.0)
        arrays = AudioLoader.extract_samples([seg])
        assert all(-32768 <= v <= 32767 for v in arrays[0])


# ======================================================================
# 静音修剪
# ======================================================================


class TestTrimSilence:
    """静音修剪测试。"""

    def test_pure_tone_not_trimmed(self):
        """纯正弦波不应被修剪。"""
        seg = make_sine_segment(duration_ms=500, amplitude=0.5)
        trimmed = AudioLoader.trim_silence(seg)
        # 正弦波全程有声，不应大幅缩短
        assert len(trimmed) >= len(seg) * 0.8

    def test_silence_trimmed(self):
        """首尾有静音应被修剪。"""
        # 构造：100ms 静音 + 300ms 正弦 + 100ms 静音
        silence = make_silent_segment(duration_ms=100)
        tone = make_sine_segment(duration_ms=300, amplitude=0.5)
        combined = silence + tone + silence

        trimmed = AudioLoader.trim_silence(
            combined, silence_thresh=-20.0, min_silence_ms=50, keep_margin_ms=0
        )
        # 修剪后应接近 300ms
        assert 250 <= len(trimmed) <= 400


# ======================================================================
# 导出
# ======================================================================


class TestExport:
    """导出测试。"""

    def test_export_wav(self):
        seg = make_sine_segment(duration_ms=100, amplitude=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.wav")
            result = AudioLoader.export(seg, path)
            assert result == path
            assert os.path.isfile(path)

    def test_export_mp3(self):
        seg = make_sine_segment(duration_ms=100, amplitude=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.mp3")
            result = AudioLoader.export(seg, path, fmt="mp3")
            assert os.path.isfile(path)

    def test_export_infers_format(self):
        seg = make_sine_segment(duration_ms=100, amplitude=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.ogg")
            result = AudioLoader.export(seg, path)
            assert os.path.isfile(path)


# ======================================================================
# 格式推断
# ======================================================================


class TestInferFormat:
    """格式推断测试。"""

    def test_known_formats(self):
        assert AudioLoader.infer_format("song.mp3") == "mp3"
        assert AudioLoader.infer_format("song.wav") == "wav"
        assert AudioLoader.infer_format("song.ogg") == "ogg"
        assert AudioLoader.infer_format("song.FLAC") == "flac"

    def test_unknown_fallsback(self):
        assert AudioLoader.infer_format("song.xyz") == "wav"

    def test_no_extension(self):
        assert AudioLoader.infer_format("song") == "wav"


# ======================================================================
# AudioInfo
# ======================================================================


class TestAudioInfo:
    """AudioInfo 元数据测试。"""

    def test_sine_info(self):
        seg = make_sine_segment(freq=440, duration_ms=500, amplitude=0.5)
        info = AudioLoader.get_info("fake_path.wav", seg)
        assert info.path == "fake_path.wav"
        assert info.params.frame_rate == 44100
        assert abs(info.duration_ms - 500) < 10
        assert abs(info.duration_sec - 0.5) < 0.01
        # 正弦波 RMS 约在 -9 dB 左右（视振幅）
        assert info.rms_dbfs < 0
        assert info.rms_dbfs > -40


# ======================================================================
# 静音生成
# ======================================================================


class TestMakeSilence:
    """静音生成测试。"""

    def test_length(self):
        seg = AudioLoader.make_silence(1000, frame_rate=44100)
        assert abs(len(seg) - 1000) < 20

    def test_all_zero(self):
        seg = AudioLoader.make_silence(100, frame_rate=44100)
        samples = seg.get_array_of_samples()
        assert all(s == 0 for s in samples)


# ======================================================================
# 加载器集成
# ======================================================================


class TestLoaderIntegration:
    """加载器集成测试。"""

    def test_load_and_info(self):
        """生成文件，加载，获取信息。"""
        seg = make_sine_segment(duration_ms=200, amplitude=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.wav")
            seg.export(path, format="wav")

            loader = AudioLoader()
            loaded = loader.load(path)
            assert isinstance(loaded, AudioSegment)
            assert abs(len(loaded) - 200) < 20

            info = loader.get_info(path, loaded)
            assert info.duration_ms >= 150

    def test_load_nonexistent_raises(self):
        loader = AudioLoader()
        try:
            loader.load("/nonexistent/path/file.wav")
            assert False
        except FileNotFoundError:
            pass
