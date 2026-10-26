#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""remix 无头自检（不创建 tkinter 窗口）。

main.py 在创建 Tk 窗口前做依赖检查，core/ 下的混音引擎与加载器都是
「零 GUI 依赖」的纯逻辑，这里直接驱动它们。验证四件事：

  1. 混音引擎：多轨叠加 + 硬/软削波、分轨增益、峰值/RMS 归一化、淡入淡出、
     equal-power 交叉淡化（含序列模式长度）；
  2. 加载器：用标准库 wave 现场生成 440Hz 单声道 WAV 并加载，参数/时长方正确；
  3. 参数统一与导出往返：不同采样率/声道的两轨统一后导出 WAV，再读回逐样本一致；
  4. 内置 ffmpeg/ffprobe 能被 VideoProcessor 自发现（不是 GUI，纯路径解析）。

用法：python selfcheck.py
退出码：0 成功 / 1 失败。
"""

import math
import os
import shutil
import struct
import sys
import tempfile
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:  # 统一按 UTF-8 输出，避免 GBK 控制台把中文自检结果打成乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

# 与 main.py 一致：把项目内置 bin/ 放到 PATH 最前，pydub 才能找到 ffmpeg
BIN_DIR = os.path.join(ROOT, "bin")
if os.path.isdir(BIN_DIR):
    os.environ["PATH"] = BIN_DIR + os.pathsep + os.environ.get("PATH", "")

from pydub import AudioSegment  # noqa: E402

from core.audio_loader import AudioLoader, AudioParams  # noqa: E402
from core.audio_mixer import (AudioMixer, ClippingMode, MixConfig,  # noqa: E402
                              MixEngine, NormalizeMode, TrackConfig)
from core.video_processor import VideoProcessor  # noqa: E402


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def expect(cond, msg):
    if not cond:
        fail(msg)


def config(**kw):
    """显式给出削波/归一化，避免默认值（软削波 + RMS）干扰定点断言。"""
    base = dict(clip_mode=ClippingMode.HARD, normalize_mode=NormalizeMode.NONE,
                engine=MixEngine.MANUAL)
    base.update(kw)
    return MixConfig(**base)


def check_mixer():
    # 硬削波：两轨 30000 之和 60000 → 截断到 S16_MAX
    hard = AudioMixer.mix([[30000] * 8, [30000] * 8], config())
    expect(hard == [32767] * 8, "硬削波结果不对：%r" % hard[:3])
    neg = AudioMixer.mix([[-30000] * 4, [-30000] * 4], config())
    expect(neg == [-32768] * 4, "硬削波负向截断不对：%r" % neg[:3])

    # 软削波：tanh 饱和，必须仍落在 S16 内且单调有界
    soft_cfg = config(clip_mode=ClippingMode.SOFT)
    s_small = AudioMixer.mix([[20000] * 2], soft_cfg)
    s_big = AudioMixer.mix([[60000] * 2], soft_cfg)
    expect(s_small[0] > 0 and s_small[0] < s_big[0] < 32767,
           "软削波未表现出单调饱和：%d / %d" % (s_small[0], s_big[0]))
    expect(AudioMixer.mix([[0] * 3], soft_cfg) == [0, 0, 0], "软削波把静音改了")

    # 分轨增益：-6.0206dB ≈ 减半
    gain = AudioMixer.mix([[10000, -10000]], config(tracks=[TrackConfig(gain_db=-6.0206)]))
    expect(gain == [5000, -5000], "分轨增益 -6dB 不对：%r" % gain)

    # 峰值归一化：目标 0.95 → int(32767*0.95)=31128
    peak = AudioMixer.mix([[1000, -500, 200]], config(normalize_mode=NormalizeMode.PEAK, peak_target=0.95))
    expect(max(abs(v) for v in peak) == 31128, "峰值归一化目标不对：%d" % max(abs(v) for v in peak))
    expect(AudioMixer.get_peak_dbfs(peak) < 0, "归一化后仍满幅")

    # RMS 归一化：把 -30dBFS 的恒定信号拉到约 -3dBFS（软削波保护会留零点几 dB 余量）
    before = [1000] * 2000
    after = AudioMixer.mix([before], config(normalize_mode=NormalizeMode.RMS, rms_target_db=-3.0))
    rms_before = AudioMixer.get_rms_dbfs(before)
    rms_after = AudioMixer.get_rms_dbfs(after)
    expect(abs(rms_after - (-3.0)) < 0.6, "RMS 归一化未接近 -3dBFS：%.2f" % rms_after)
    expect(rms_after > rms_before + 20, "RMS 归一化没有提升响度：%.2f -> %.2f" % (rms_before, rms_after))

    # 序列模式（交叉淡化）：500 + 500 样本、重叠 100 → 900 样本
    cf = AudioMixer.mix([[1000] * 500, [-1000] * 500],
                        config(crossfade_ms=100))
    expect(len(cf) == 900, "交叉淡化后长度不是 900：%d" % len(cf))
    expect(abs(cf[400] - 1000) <= 30, "交叉淡化重叠区起点不对：%d" % cf[400])
    expect(abs(cf[450]) <= 30, "交叉淡化中点未按 equal-power 抵消：%d" % cf[450])
    expect(abs(cf[500] + 1000) <= 30, "交叉淡化后段电平不对：%d" % cf[500])
    expect(abs(cf[599] + 1000) <= 30, "交叉淡化重叠区终点不对：%d" % cf[599])

    return 6


def check_fade():
    rate, ms = 44100, 250
    n = rate * ms // 1000
    seg = AudioMixer.mix_to_segment([[1000] * n, [2000] * n], 2, rate, 1,
                                    config(fade_in_ms=100, fade_out_ms=100))
    samples = seg.get_array_of_samples()
    expect(len(seg) == ms, "淡入淡出改变时长：%d" % len(seg))
    expect(len(samples) == n, "淡入淡出改变样本数：%d != %d" % (len(samples), n))
    expect(samples[0] == 0 and samples[1] == 0, "淡入起点不为 0：%r" % list(samples[:2]))
    expect(samples[-1] == 0 and samples[-2] <= 1, "淡出终点不为 0：%r" % list(samples[-2:]))
    mid = samples[n // 2]
    expect(abs(mid - 3000) <= 20, "淡变中段电平不对：%d" % mid)
    return n


def make_tone(path, rate=22050, ms=250, freq=440, amp=12000):
    n = rate * ms // 1000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
                          for i in range(n))
        w.writeframes(frames)
    return n


def check_loader_roundtrip():
    tmp = tempfile.mkdtemp(prefix="remix-selfcheck-")
    try:
        src = os.path.join(tmp, "tone.wav")
        n = make_tone(src)

        loader = AudioLoader()
        seg = loader.load(src)
        expect(len(seg) == 250, "加载后时长不对：%d ms" % len(seg))
        expect((seg.frame_rate, seg.channels, seg.sample_width) == (22050, 1, 2),
               "加载后参数不对：%r" % ((seg.frame_rate, seg.channels, seg.sample_width),))
        expect(len(seg.get_array_of_samples()) == n, "加载后样本数不对")

        info = AudioLoader.get_info(src, seg)
        expect(info.duration_ms == 250 and abs(info.duration_sec - 0.25) < 1e-6,
               "get_info 时长不对：%r" % (info.duration_ms,))
        expect(info.params == AudioParams(22050, 1, 2), "get_info 参数不对：%r" % (info.params,))
        expect(-30 < info.rms_dbfs < 0, "get_info dBFS 不合理：%r" % info.rms_dbfs)

        # 参数统一：44.1k 立体声静音 + 22.05k 单声道 → 都变成第一轨参数
        other = AudioSegment.silent(250, frame_rate=44100).set_channels(2).set_sample_width(2)
        unified = AudioLoader.unify([seg, other])
        expect(len(unified) == 2, "unify 返回数量不对")
        for i, s in enumerate(unified):
            expect((s.frame_rate, s.channels, s.sample_width) == (22050, 1, 2),
                   "第 %d 轨统一后参数不对：%r" % (i, (s.frame_rate, s.channels, s.sample_width)))
            expect(len(s) == 250, "第 %d 轨统一后时长不对：%d" % (i, len(s)))

        # 导出 → 重新加载：参数/时长/逐样本必须一致
        out = os.path.join(tmp, "exported.wav")
        returned = AudioLoader.export(seg, out)
        expect(returned == out and os.path.isfile(out), "export 未返回/写出目标路径")
        seg2 = loader.load(out)
        expect((seg2.frame_rate, seg2.channels, seg2.sample_width) == (22050, 1, 2),
               "导出再加载后参数不对")
        expect(len(seg2) == 250, "导出再加载后时长不对：%d" % len(seg2))
        a1 = list(seg.get_array_of_samples())
        a2 = list(seg2.get_array_of_samples())
        expect(a1 == a2, "导出往返后样本不一致（%d vs %d）" % (len(a1), len(a2)))

        sil = AudioLoader.make_silence(120)
        expect(len(sil) == 120 and sil.dBFS == float("-inf"), "make_silence 结果不对")
        return n, len(a1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_ffmpeg_discovery():
    AudioSegment.converter = VideoProcessor._find_ffmpeg()
    AudioSegment.ffprobe = VideoProcessor._find_ffprobe()
    ffmpeg = AudioSegment.converter
    ffprobe = AudioSegment.ffprobe
    expect(isinstance(ffmpeg, str) and os.path.isfile(ffmpeg), "ffmpeg 未找到：%r" % ffmpeg)
    expect(isinstance(ffprobe, str) and os.path.isfile(ffprobe), "ffprobe 未找到：%r" % ffprobe)
    expect(os.path.basename(ffmpeg).lower().startswith("ffmpeg"), "ffmpeg 路径可疑：%r" % ffmpeg)
    return ffmpeg, ffprobe


def main():
    n_checks = check_mixer()
    n_fade = check_fade()
    n_src, n_rt = check_loader_roundtrip()
    ffmpeg, _ffprobe = check_ffmpeg_discovery()
    print("selfcheck ok: 混音 %d 项（硬/软削波、-6dB 增益、峰值 0.95、RMS≈-3dBFS、交叉淡化 900 样本）/ "
          "淡入淡出 %d 样本首尾归零 / WAV 加载 %d 样本→统一参数→导出往返 %d 样本逐样本一致 / "
          "内置 ffmpeg 自发现 %s"
          % (n_checks, n_fade, n_src, n_rt, os.path.basename(ffmpeg)))


if __name__ == "__main__":
    main()
