"""
测试 video_processor — 视频音频合成处理器。

覆盖：
  - VideoInfo 数据结构
  - MuxMode 枚举
  - ffmpeg/ffprobe 查找
  - 视频信息获取（需真实视频文件或 mock）
  - 音频提取（需真实视频文件）
  - 边界条件
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.video_processor import VideoProcessor, VideoInfo, MuxMode


# ======================================================================
# 数据结构
# ======================================================================


class TestVideoInfo:
    """VideoInfo 数据结构测试。"""

    def test_creation(self):
        info = VideoInfo(
            path="test.mp4",
            duration_sec=120.5,
            width=1920,
            height=1080,
            video_codec="h264",
            video_bitrate_kbps=5000,
            fps=30.0,
            audio_codec="aac",
            audio_sample_rate=48000,
            audio_channels=2,
            has_audio=True,
            file_size_mb=50.0,
        )
        assert info.path == "test.mp4"
        assert info.duration_sec == 120.5
        assert info.width == 1920
        assert info.height == 1080

    def test_resolution_property(self):
        info = VideoInfo(
            path="test.mp4", duration_sec=10, width=1280, height=720,
            video_codec="h264", video_bitrate_kbps=2000, fps=25.0,
            audio_codec="aac", audio_sample_rate=44100, audio_channels=2,
            has_audio=True, file_size_mb=10.0,
        )
        assert info.resolution == "1280x720"

    def test_duration_str_short(self):
        info = VideoInfo(
            path="test.mp4", duration_sec=95, width=0, height=0,
            video_codec="", video_bitrate_kbps=0, fps=0,
            audio_codec="", audio_sample_rate=0, audio_channels=0,
            has_audio=False, file_size_mb=0,
        )
        assert info.duration_str == "1:35"

    def test_duration_str_long(self):
        info = VideoInfo(
            path="test.mp4", duration_sec=3661, width=0, height=0,
            video_codec="", video_bitrate_kbps=0, fps=0,
            audio_codec="", audio_sample_rate=0, audio_channels=0,
            has_audio=False, file_size_mb=0,
        )
        assert info.duration_str == "1:01:01"

    def test_duration_str_zero(self):
        info = VideoInfo(
            path="test.mp4", duration_sec=0, width=0, height=0,
            video_codec="", video_bitrate_kbps=0, fps=0,
            audio_codec="", audio_sample_rate=0, audio_channels=0,
            has_audio=False, file_size_mb=0,
        )
        assert info.duration_str == "0:00"

    def test_no_audio(self):
        info = VideoInfo(
            path="test.mp4", duration_sec=10, width=1920, height=1080,
            video_codec="h264", video_bitrate_kbps=5000, fps=30.0,
            audio_codec="", audio_sample_rate=0, audio_channels=0,
            has_audio=False, file_size_mb=25.0,
        )
        assert not info.has_audio
        assert info.audio_codec == ""


# ======================================================================
# MuxMode 枚举
# ======================================================================


class TestMuxMode:
    """MuxMode 枚举测试。"""

    def test_values(self):
        assert MuxMode.REPLACE.value == 1
        assert MuxMode.MIX.value == 2

    def test_from_enum(self):
        mode = MuxMode.REPLACE
        assert mode is MuxMode.REPLACE
        assert mode is not MuxMode.MIX


# ======================================================================
# ffmpeg / ffprobe 查找
# ======================================================================


class TestFindFfmpeg:
    """ffmpeg/ffprobe 查找测试。"""

    def test_find_ffmpeg(self):
        """应当能找到 ffmpeg（优先内置 bin，fallback 系统 PATH）。"""
        path = VideoProcessor._find_ffmpeg()
        assert path == "ffmpeg" or os.path.isfile(path)

    def test_find_ffprobe(self):
        """应当能找到 ffprobe（优先内置 bin，fallback 系统 PATH）。"""
        path = VideoProcessor._find_ffprobe()
        assert path == "ffprobe" or os.path.isfile(path)


# ======================================================================
# 视频信息获取（需要真实视频文件）
# ======================================================================


class TestGetVideoInfo:
    """get_video_info 集成测试 — 用 ffmpeg 生成测试视频。"""

    def _create_test_video(self, output_path: str) -> None:
        """用 ffmpeg 生成一个 1 秒的测试视频（含音频）。"""
        import subprocess

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libx264", "-c:a", "aac",
            "-shortest", output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30,
                       creationflags=creationflags)

    def test_get_info_on_generated_video(self):
        """用生成的测试视频验证信息获取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test_video.mp4")
            self._create_test_video(video_path)

            assert os.path.isfile(video_path)

            info = VideoProcessor.get_video_info(video_path)
            assert info.path == video_path
            assert info.width == 320
            assert info.height == 240
            assert info.has_audio
            assert info.audio_channels > 0
            assert abs(info.duration_sec - 1.0) < 0.5
            assert info.file_size_mb > 0

    def test_get_info_no_audio_video(self):
        """不含音频的视频也应正常返回信息。"""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test_silent.mp4")
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x120:d=0.5",
                "-c:v", "libx264",
                "-an", "-shortest", video_path,
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=30,
                           creationflags=creationflags)

            assert os.path.isfile(video_path)
            info = VideoProcessor.get_video_info(video_path)
            assert not info.has_audio

    def test_nonexistent_file_raises(self):
        try:
            VideoProcessor.get_video_info("/nonexistent/video.mp4")
            assert False, "应抛出 FileNotFoundError"
        except FileNotFoundError:
            pass


# ======================================================================
# 音频提取（需要真实视频文件）
# ======================================================================


class TestExtractAudio:
    """音频提取集成测试。"""

    def _create_test_video(self, output_path: str) -> None:
        import subprocess
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac",
            "-shortest", output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30,
                       creationflags=creationflags)

    def test_extract_to_wav(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test.mp4")
            self._create_test_video(video_path)

            audio_path = os.path.join(tmpdir, "extracted.wav")
            result = VideoProcessor.extract_audio(video_path, audio_path, fmt="wav")

            assert os.path.isfile(result)
            assert os.path.getsize(result) > 0

            # 应能被 pydub 加载
            from pydub import AudioSegment
            seg = AudioSegment.from_file(result)
            assert seg.frame_rate > 0

    def test_extract_to_mp3(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test.mp4")
            self._create_test_video(video_path)

            audio_path = os.path.join(tmpdir, "extracted.mp3")
            result = VideoProcessor.extract_audio(video_path, audio_path, fmt="mp3")

            assert os.path.isfile(result)

    def test_extract_auto_path(self):
        """不指定输出路径时自动生成。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "test.mp4")
            self._create_test_video(video_path)

            result = VideoProcessor.extract_audio(video_path, fmt="wav")
            assert os.path.isfile(result)
            # 实现已改为时间戳命名：{base}_audio_{timestamp}.wav
            assert "_audio_" in result and result.endswith(".wav")

    def test_extract_nonexistent_raises(self):
        try:
            VideoProcessor.extract_audio("/nonexistent/v.mp4", "/tmp/out.wav")
            assert False
        except FileNotFoundError:
            pass


# ======================================================================
# 音频合成到视频（端到端）
# ======================================================================


class TestMuxAudioVideo:
    """视频音频合成集成测试。"""

    def _create_test_video(self, output_path: str) -> None:
        import subprocess
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=green:s=160x120:d=1",
            "-f", "lavfi", "-i", "sine=frequency=800:duration=1",
            "-c:v", "libx264", "-c:a", "aac",
            "-shortest", output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30,
                       creationflags=creationflags)

    def _create_test_audio(self, output_path: str) -> None:
        import subprocess
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=1200:duration=1",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30,
                       creationflags=creationflags)

    def test_replace_audio(self):
        """替换音频模式。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "video.mp4")
            self._create_test_video(video_path)

            audio_path = os.path.join(tmpdir, "new_audio.wav")
            self._create_test_audio(audio_path)

            output_path = os.path.join(tmpdir, "output.mp4")
            result = VideoProcessor.mux_audio_video(
                video_path, audio_path, output_path,
                mode=MuxMode.REPLACE,
            )

            assert os.path.isfile(result)
            assert os.path.getsize(result) > 0

            # 验证输出视频有音频
            info = VideoProcessor.get_video_info(result)
            assert info.has_audio

    def test_mix_audio(self):
        """混合音频模式。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "video.mp4")
            self._create_test_video(video_path)

            audio_path = os.path.join(tmpdir, "bgm.wav")
            self._create_test_audio(audio_path)

            output_path = os.path.join(tmpdir, "output_mix.mp4")
            result = VideoProcessor.mux_audio_video(
                video_path, audio_path, output_path,
                mode=MuxMode.MIX, mix_volume=0.3,
            )

            assert os.path.isfile(result)
            assert os.path.getsize(result) > 0

            # 关键回归：混合模式必须把背景音写入输出（之前版本漏掉
            # -map 导致音频轨道被丢弃，输出为静音视频）
            info = VideoProcessor.get_video_info(result)
            assert info.has_audio

    def test_mux_nonexistent_video_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "a.wav")
            self._create_test_audio(audio_path)
            try:
                VideoProcessor.mux_audio_video("/nonexistent/v.mp4", audio_path, "/tmp/o.mp4")
                assert False
            except FileNotFoundError:
                pass

    def test_invalid_mode_raises(self):
        """无效模式应在参数验证阶段抛 ValueError（先于文件存在性检查）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "v.mp4")
            audio_path = os.path.join(tmpdir, "a.wav")
            output_path = os.path.join(tmpdir, "o.mp4")
            self._create_test_video(video_path)
            self._create_test_audio(audio_path)

            try:
                # 用字符串（非 MuxMode 枚举值）绕过类型提示
                VideoProcessor.mux_audio_video(
                    video_path, audio_path, output_path,
                    mode="invalid",  # type: ignore
                )
                assert False, "应抛出 ValueError"
            except ValueError:
                pass

    def test_extract_and_load(self):
        """extract_and_load 便捷方法。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "video.mp4")
            self._create_test_video(video_path)

            output_dir = os.path.join(tmpdir, "audio_output")
            result = VideoProcessor.extract_and_load(video_path, output_dir=output_dir, fmt="wav")

            assert os.path.isfile(result)
            assert "audio_output" in result


# ======================================================================
# 边界条件
# ======================================================================


class TestEdgeCases:
    """边界条件测试。"""

    def test_video_info_path_preserved(self):
        """路径字段应原样保留。"""
        info = VideoInfo(
            path="/some/long/path/to/video.mp4",
            duration_sec=1, width=0, height=0,
            video_codec="", video_bitrate_kbps=0, fps=0,
            audio_codec="", audio_sample_rate=0, audio_channels=0,
            has_audio=False, file_size_mb=0,
        )
        assert info.path == "/some/long/path/to/video.mp4"

    def test_video_info_large_size(self):
        """大文件大小应正常显示。"""
        info = VideoInfo(
            path="big.mp4", duration_sec=3600, width=3840, height=2160,
            video_codec="hevc", video_bitrate_kbps=50000, fps=60.0,
            audio_codec="aac", audio_sample_rate=48000, audio_channels=6,
            has_audio=True, file_size_mb=45000.5,
        )
        assert info.file_size_mb == 45000.5
        assert info.resolution == "3840x2160"
