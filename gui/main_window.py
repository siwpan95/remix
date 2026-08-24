"""
混音器主窗口 — GUI 层。

只负责展示和用户交互，所有业务逻辑委托给 ``core`` 包。
使用 ``threading.Lock`` 保护文件列表的并发访问。

双标签页：
  Tab 1 — 音频混音：多轨音频混合、增益、淡入淡出、削波、归一化、滤波。
  Tab 2 — 视频合成：视频音频提取、替换、混音合成到视频。
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 依赖检查
try:
    from pydub import AudioSegment
except ImportError:
    print("错误：请安装 pydub：pip install pydub\n并确保已安装 ffmpeg", file=sys.stderr)
    sys.exit(1)

from core.audio_loader import AudioLoader
from core.audio_mixer import (
    AudioMixer,
    ClippingMode,
    NormalizeMode,
    MixEngine,
    MixConfig,
    TrackConfig,
)
from core.video_processor import VideoProcessor, VideoInfo, MuxMode
from utils.progress_reporter import ProgressReporter
from utils import generate_output_name


# ======================================================================
# 辅助组件
# ======================================================================


class TrackRow(ttk.Frame):
    """文件列表中的一行：序号 + 文件名 + 音量输入。"""

    def __init__(self, parent, name: str, index: int):
        super().__init__(parent)
        self.index = index
        self.name = name

        ttk.Label(self, text=f"{index + 1}.", width=3, anchor="e").pack(side=tk.LEFT)
        ttk.Label(self, text=name, width=28, anchor="w").pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(self, text="dB:", width=3).pack(side=tk.LEFT)
        self._gain_var = tk.StringVar(value="0.0")
        self._gain_entry = ttk.Entry(
            self, textvariable=self._gain_var, width=6, justify="center"
        )
        self._gain_entry.pack(side=tk.LEFT, padx=2)

    @property
    def gain_db(self) -> float:
        try:
            return float(self._gain_var.get())
        except ValueError:
            return 0.0


class Tooltip:
    """简易悬停提示。"""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height()
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self.text, justify=tk.LEFT,
            background="#ffffe0", relief=tk.SOLID, borderwidth=1,
            font=("Microsoft YaHei", 9)
        )
        label.pack()

    def _hide(self, _event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


# ======================================================================
# 主窗口
# ======================================================================


class MixerApp:
    """混音器主窗口 — 双标签页：音频混音 + 视频合成。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Remix — 音频混音 & 视频合成")
        self.root.geometry("600x700")
        self.root.minsize(500, 550)

        # ---- 数据 ----
        self._audio_paths: list[str] = []
        self._audio_lock = threading.Lock()

        self._video_path: str = ""
        self._video_audio_paths: list[str] = []
        self._video_lock = threading.Lock()

        # ---- 业务模块 ----
        self._loader = AudioLoader()
        self._reporter = ProgressReporter(root)

        # ---- 构建界面 ----
        self._create_notebook()
        self._start_polling()

    # ==================================================================
    # Notebook
    # ==================================================================

    def _create_notebook(self) -> None:
        self._notebook = ttk.Notebook(self.root)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Tab 1: 音频混音
        self._audio_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._audio_tab, text="  音频混音  ")
        self._build_audio_tab()

        # Tab 2: 视频合成
        self._video_tab = ttk.Frame(self._notebook)
        self._notebook.add(self._video_tab, text="  视频合成  ")
        self._build_video_tab()

    # ==================================================================
    # Tab 1 — 音频混音
    # ==================================================================

    def _build_audio_tab(self) -> None:
        parent = self._audio_tab
        self._build_audio_file_list(parent)
        self._build_audio_toolbar(parent)
        self._build_audio_effect_panel(parent)
        self._build_audio_progress_bar(parent)
        self._build_audio_bottom_bar(parent)

    def _build_audio_file_list(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="音频轨道列表", padding=8)
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        hdr = ttk.Frame(frame)
        hdr.pack(fill=tk.X, padx=2, pady=(0, 4))
        ttk.Label(hdr, text="#", width=3, anchor="e").pack(side=tk.LEFT)
        ttk.Label(hdr, text="文件名", width=28, anchor="w").pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(hdr, text="音量(dB)", width=9).pack(side=tk.LEFT)

        canvas_frame = ttk.Frame(frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self._audio_canvas = tk.Canvas(canvas_frame, height=160, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self._audio_canvas.yview)
        self._audio_inner = ttk.Frame(self._audio_canvas)

        self._audio_inner.bind(
            "<Configure>",
            lambda e: self._audio_canvas.configure(scrollregion=self._audio_canvas.bbox("all")),
        )
        self._audio_canvas.create_window((0, 0), window=self._audio_inner, anchor="nw")
        self._audio_canvas.configure(yscrollcommand=scrollbar.set)
        self._audio_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            self._audio_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._audio_canvas.bind("<MouseWheel>", _on_mousewheel)

        self._audio_track_rows: list[TrackRow] = []

    def _build_audio_toolbar(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=6, pady=4)

        ttk.Button(frame, text="添加音乐", command=self._audio_add_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame, text="移除末尾", command=self._audio_remove_last).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame, text="清空列表", command=self._audio_clear).pack(side=tk.LEFT, padx=2)

    def _build_audio_effect_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="混音参数", padding=8)
        frame.pack(fill=tk.X, padx=6, pady=(4, 0))

        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="淡入(ms):").pack(side=tk.LEFT)
        self._fade_in_var = tk.StringVar(value="0")
        ttk.Entry(row1, textvariable=self._fade_in_var, width=6, justify="center").pack(
            side=tk.LEFT, padx=(2, 8)
        )
        ttk.Label(row1, text="淡出(ms):").pack(side=tk.LEFT)
        self._fade_out_var = tk.StringVar(value="0")
        ttk.Entry(row1, textvariable=self._fade_out_var, width=6, justify="center").pack(
            side=tk.LEFT, padx=(2, 8)
        )
        ttk.Label(row1, text="交叉淡化(ms):").pack(side=tk.LEFT)
        self._crossfade_var = tk.StringVar(value="0")
        ttk.Entry(row1, textvariable=self._crossfade_var, width=6, justify="center").pack(
            side=tk.LEFT, padx=2
        )

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=4)

        ttk.Label(row2, text="削波:").pack(side=tk.LEFT, padx=(0, 2))
        self._clip_var = tk.StringVar(value="soft")
        ttk.Radiobutton(row2, text="软", variable=self._clip_var, value="soft").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="硬", variable=self._clip_var, value="hard").pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row2, text="归一化:").pack(side=tk.LEFT, padx=(0, 2))
        self._norm_var = tk.StringVar(value="rms")
        ttk.Radiobutton(row2, text="RMS", variable=self._norm_var, value="rms").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="峰值", variable=self._norm_var, value="peak").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="无", variable=self._norm_var, value="none").pack(side=tk.LEFT, padx=(0, 12))

        self._compress_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="动态压缩", variable=self._compress_var).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row2, text="压缩阈值(dB):").pack(side=tk.LEFT)
        self._comp_thr_var = tk.StringVar(value="-20")
        ttk.Entry(row2, textvariable=self._comp_thr_var, width=5, justify="center").pack(side=tk.LEFT, padx=2)

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=2)

        ttk.Label(row3, text="导出格式:").pack(side=tk.LEFT)
        self._audio_fmt_var = tk.StringVar(value="wav")
        fmt_combo = ttk.Combobox(
            row3, textvariable=self._audio_fmt_var,
            values=["wav", "mp3", "ogg", "flac"],
            state="readonly", width=6,
        )
        fmt_combo.pack(side=tk.LEFT, padx=(2, 12))

        ttk.Label(row3, text="高通(Hz):").pack(side=tk.LEFT)
        self._hp_var = tk.StringVar(value="0")
        ttk.Entry(row3, textvariable=self._hp_var, width=6, justify="center").pack(
            side=tk.LEFT, padx=(2, 8)
        )
        ttk.Label(row3, text="低通(Hz):").pack(side=tk.LEFT)
        self._lp_var = tk.StringVar(value="0")
        ttk.Entry(row3, textvariable=self._lp_var, width=6, justify="center").pack(side=tk.LEFT, padx=2)

    def _build_audio_progress_bar(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=6, pady=4)

        self._audio_progress_var = tk.DoubleVar()
        self._audio_progress_bar = ttk.Progressbar(
            frame, orient=tk.HORIZONTAL, mode="determinate", variable=self._audio_progress_var
        )
        self._audio_progress_bar.pack(fill=tk.X, expand=True)

    def _build_audio_bottom_bar(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=6, pady=(2, 6))

        self._audio_start_btn = ttk.Button(frame, text="开始混音", command=self._audio_start_mixing)
        self._audio_start_btn.pack(side=tk.LEFT, padx=5)

        self._audio_status_label = ttk.Label(frame, text="就绪")
        self._audio_status_label.pack(side=tk.LEFT, padx=10)

    # ==================================================================
    # Tab 1 — 音频混音：文件操作
    # ==================================================================

    def _audio_add_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return
        with self._audio_lock:
            for f in files:
                if f not in self._audio_paths:
                    idx = len(self._audio_paths)
                    self._audio_paths.append(f)
                    row = TrackRow(self._audio_inner, os.path.basename(f), idx)
                    row.pack(fill=tk.X, pady=1)
                    self._audio_track_rows.append(row)

    def _audio_remove_last(self) -> None:
        with self._audio_lock:
            if not self._audio_paths:
                return
            self._audio_paths.pop()
            row = self._audio_track_rows.pop()
            row.destroy()

    def _audio_clear(self) -> None:
        with self._audio_lock:
            self._audio_paths.clear()
            for row in self._audio_track_rows:
                row.destroy()
            self._audio_track_rows.clear()

    def _audio_get_paths(self) -> list[str]:
        with self._audio_lock:
            return list(self._audio_paths)

    # ==================================================================
    # Tab 1 — 音频混音：流程
    # ==================================================================

    def _audio_build_config(self) -> MixConfig:
        clip = ClippingMode.SOFT if self._clip_var.get() == "soft" else ClippingMode.HARD
        norm_map = {"peak": NormalizeMode.PEAK, "rms": NormalizeMode.RMS, "none": NormalizeMode.NONE}
        norm = norm_map.get(self._norm_var.get(), NormalizeMode.RMS)

        def _to_int(var, default=0):
            try: return int(var.get())
            except ValueError: return default

        def _to_float(var, default=0.0):
            try: return float(var.get())
            except ValueError: return default

        tracks = [TrackConfig(gain_db=row.gain_db) for row in self._audio_track_rows]

        return MixConfig(
            clip_mode=clip,
            normalize_mode=norm,
            engine=MixEngine.MANUAL,
            fade_in_ms=_to_int(self._fade_in_var),
            fade_out_ms=_to_int(self._fade_out_var),
            crossfade_ms=_to_int(self._crossfade_var),
            compress=self._compress_var.get(),
            compress_threshold=_to_float(self._comp_thr_var, -20.0),
            compress_ratio=4.0,
            low_pass_cutoff=_to_int(self._lp_var),
            high_pass_cutoff=_to_int(self._hp_var),
            tracks=tracks,
        )

    def _audio_start_mixing(self) -> None:
        paths = self._audio_get_paths()
        if len(paths) < 2:
            messagebox.showwarning("文件不足", "请至少添加两个音频文件")
            return
        self._audio_start_btn.configure(state=tk.DISABLED)
        self._audio_status_label.config(text="正在混音...")
        self._audio_progress_var.set(0)

        config = self._audio_build_config()
        thread = threading.Thread(
            target=self._audio_mix_work, args=(paths, config), daemon=True
        )
        thread.start()

    def _audio_mix_work(self, paths: list[str], config: MixConfig) -> None:
        try:
            segments = self._loader.load_batch(
                paths,
                on_progress=lambda msg: self._reporter.status(msg),
            )
            if not segments:
                raise RuntimeError("没有成功加载任何音频文件")

            segments = AudioLoader.unify(segments)

            if config.engine is MixEngine.OVERLAY:
                mixed = AudioMixer.mix_with_pydub(
                    segments, config=config,
                    progress_callback=lambda c, t: self._reporter.progress(int(c / t * 100)),
                )
            else:
                sample_arrays = AudioLoader.extract_samples(segments)
                seg0 = segments[0]
                mixed = AudioMixer.mix_to_segment(
                    sample_arrays,
                    sample_width=seg0.sample_width,
                    frame_rate=seg0.frame_rate,
                    channels=seg0.channels,
                    config=config,
                    progress_callback=lambda c, t: self._reporter.progress(int(c / t * 100)),
                )

            # 注：淡入淡出 / 压缩 / 滤波等后处理已在 core 层
            # （mix_to_segment / mix_with_pydub）统一完成，此处无需重复。

            fmt = self._audio_fmt_var.get()
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(base_dir, "output")
            os.makedirs(output_dir, exist_ok=True)
            ext = fmt if fmt else "wav"
            output_path = os.path.join(output_dir, generate_output_name("mixed_output", "", ext))

            AudioLoader.export(mixed, output_path, fmt=fmt)
            self._reporter.done(output_path)

        except Exception as e:
            self._reporter.error(str(e))

    # ==================================================================
    # Tab 2 — 视频合成
    # ==================================================================

    def _build_video_tab(self) -> None:
        parent = self._video_tab
        self._build_video_file_section(parent)
        self._build_video_info_section(parent)
        self._build_video_audio_section(parent)
        self._build_video_mode_section(parent)
        self._build_video_progress_bar(parent)
        self._build_video_bottom_bar(parent)

    def _build_video_file_section(self, parent: ttk.Frame) -> None:
        """视频文件选择区域。"""
        frame = ttk.LabelFrame(parent, text="视频文件", padding=8)
        frame.pack(fill=tk.X, padx=6, pady=(6, 0))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X)

        self._video_path_var = tk.StringVar(value="（未选择）")
        ttk.Entry(row, textvariable=self._video_path_var, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(row, text="选择视频...", command=self._video_select).pack(side=tk.RIGHT)

    def _build_video_info_section(self, parent: ttk.Frame) -> None:
        """视频信息展示区域。"""
        frame = ttk.LabelFrame(parent, text="视频信息", padding=8)
        frame.pack(fill=tk.X, padx=6, pady=(4, 0))

        self._video_info_text = tk.Text(
            frame, height=5, width=50, state=tk.DISABLED,
            font=("Consolas", 10), bg="#f5f5f5", relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self._video_info_text.pack(fill=tk.X)

    def _build_video_audio_section(self, parent: ttk.Frame) -> None:
        """视频合成用的音频列表。"""
        frame = ttk.LabelFrame(parent, text="合成音频（替换或混入视频）", padding=8)
        frame.pack(fill=tk.X, padx=6, pady=(4, 0))

        hdr = ttk.Frame(frame)
        hdr.pack(fill=tk.X, padx=2, pady=(0, 4))
        ttk.Label(hdr, text="#", width=3, anchor="e").pack(side=tk.LEFT)
        ttk.Label(hdr, text="文件名", width=36, anchor="w").pack(side=tk.LEFT, padx=(4, 8))

        canvas_frame = ttk.Frame(frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self._video_canvas = tk.Canvas(canvas_frame, height=90, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self._video_canvas.yview)
        self._video_audio_inner = ttk.Frame(self._video_canvas)

        self._video_audio_inner.bind(
            "<Configure>",
            lambda e: self._video_canvas.configure(scrollregion=self._video_canvas.bbox("all")),
        )
        self._video_canvas.create_window((0, 0), window=self._video_audio_inner, anchor="nw")
        self._video_canvas.configure(yscrollcommand=scrollbar.set)
        self._video_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._video_audio_rows: list[tk.Frame] = []

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_row, text="添加音频", command=self._video_add_audio).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="清空", command=self._video_clear_audio).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="从视频提取音频", command=self._video_extract_audio).pack(side=tk.RIGHT, padx=2)

    def _build_video_mode_section(self, parent: ttk.Frame) -> None:
        """合成模式 + 参数。"""
        frame = ttk.LabelFrame(parent, text="合成参数", padding=8)
        frame.pack(fill=tk.X, padx=6, pady=(4, 0))

        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="模式:").pack(side=tk.LEFT, padx=(0, 4))
        self._mux_mode_var = tk.StringVar(value="replace")
        ttk.Radiobutton(row1, text="替换原音频", variable=self._mux_mode_var, value="replace").pack(side=tk.LEFT)
        ttk.Radiobutton(row1, text="混入背景音", variable=self._mux_mode_var, value="mix").pack(side=tk.LEFT, padx=(4, 0))

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=2)

        ttk.Label(row2, text="背景音量:").pack(side=tk.LEFT)
        self._mix_vol_var = tk.DoubleVar(value=0.5)
        self._mix_vol_scale = ttk.Scale(
            row2, from_=0.0, to=1.0, variable=self._mix_vol_var,
            orient=tk.HORIZONTAL, length=120,
        )
        self._mix_vol_scale.pack(side=tk.LEFT, padx=4)
        ttk.Label(row2, textvariable=self._mix_vol_var, width=4).pack(side=tk.LEFT)

        # 绑定模式切换：MIX 模式显示音量调节
        def _on_mode_change(*args):
            if self._mux_mode_var.get() == "mix":
                self._mix_vol_scale.configure(state=tk.NORMAL)
            else:
                self._mix_vol_scale.configure(state=tk.DISABLED)
        self._mux_mode_var.trace_add("write", _on_mode_change)
        _on_mode_change()

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=2)

        ttk.Label(row3, text="输出视频编码:").pack(side=tk.LEFT)
        self._video_codec_var = tk.StringVar(value="copy")
        codec_combo = ttk.Combobox(
            row3, textvariable=self._video_codec_var,
            values=["copy", "libx264", "libx265"],
            state="readonly", width=10,
        )
        codec_combo.pack(side=tk.LEFT, padx=(4, 12))
        Tooltip(codec_combo, "copy=无编码最快\nlibx264=H.264\nlibx265=H.265/HEVC")

        ttk.Label(row3, text="音频编码:").pack(side=tk.LEFT)
        self._audio_codec_var = tk.StringVar(value="aac")
        acodec_combo = ttk.Combobox(
            row3, textvariable=self._audio_codec_var,
            values=["aac", "mp3", "copy", "libmp3lame"],
            state="readonly", width=10,
        )
        acodec_combo.pack(side=tk.LEFT, padx=4)

    def _build_video_progress_bar(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=6, pady=4)

        self._video_progress_var = tk.DoubleVar()
        self._video_progress_bar = ttk.Progressbar(
            frame, orient=tk.HORIZONTAL, mode="indeterminate",
        )
        self._video_progress_bar.pack(fill=tk.X, expand=True)

    def _build_video_bottom_bar(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=6, pady=(2, 6))

        self._video_start_btn = ttk.Button(frame, text="开始合成", command=self._video_start_mux)
        self._video_start_btn.pack(side=tk.LEFT, padx=5)

        self._video_status_label = ttk.Label(frame, text="就绪")
        self._video_status_label.pack(side=tk.LEFT, padx=10)

    # ==================================================================
    # Tab 2 — 视频合成：操作
    # ==================================================================

    def _video_select(self) -> None:
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self._video_path = path
        self._video_path_var.set(path)

        # 在后台线程获取视频信息
        self._video_start_btn.configure(state=tk.DISABLED)
        self._video_status_label.config(text="正在读取视频信息...")
        thread = threading.Thread(target=self._video_load_info, args=(path,), daemon=True)
        thread.start()

    def _video_load_info(self, path: str) -> None:
        try:
            info = VideoProcessor.get_video_info(path)
            # 构建信息文本
            lines = [
                f"时长: {info.duration_str}",
                f"分辨率: {info.resolution}  |  帧率: {info.fps} fps",
                f"视频编码: {info.video_codec}  |  码率: {info.video_bitrate_kbps} kbps",
            ]
            if info.has_audio:
                lines.append(
                    f"原音频: {info.audio_codec}  |  "
                    f"{info.audio_sample_rate} Hz  |  "
                    f"{'立体声' if info.audio_channels >= 2 else '单声道'}"
                )
            else:
                lines.append("原音频: （无）")
            lines.append(f"文件大小: {info.file_size_mb} MB")

            # 用 after 在主线程更新 UI
            def _update():
                self._video_info_text.configure(state=tk.NORMAL)
                self._video_info_text.delete("1.0", tk.END)
                self._video_info_text.insert("1.0", "\n".join(lines))
                self._video_info_text.configure(state=tk.DISABLED)
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="视频已就绪")
            self.root.after(0, _update)

        except Exception as e:
            def _error():
                self._video_info_text.configure(state=tk.NORMAL)
                self._video_info_text.delete("1.0", tk.END)
                self._video_info_text.insert("1.0", f"读取失败: {e}")
                self._video_info_text.configure(state=tk.DISABLED)
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="读取失败")
            self.root.after(0, _error)

    def _video_add_audio(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择音频文件用于合成",
            filetypes=[
                ("音频文件", "*.mp3 *.wav *.ogg *.flac *.m4a *.aac"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return
        with self._video_lock:
            for f in files:
                if f not in self._video_audio_paths:
                    idx = len(self._video_audio_paths)
                    self._video_audio_paths.append(f)
                    row = ttk.Frame(self._video_audio_inner)
                    ttk.Label(row, text=f"{idx + 1}.", width=3, anchor="e").pack(side=tk.LEFT)
                    ttk.Label(row, text=os.path.basename(f), width=36, anchor="w").pack(
                        side=tk.LEFT, padx=(4, 8)
                    )
                    row.pack(fill=tk.X, pady=1)
                    self._video_audio_rows.append(row)

    def _video_clear_audio(self) -> None:
        with self._video_lock:
            self._video_audio_paths.clear()
            for row in self._video_audio_rows:
                row.destroy()
            self._video_audio_rows.clear()

    def _video_extract_audio(self) -> None:
        """从当前视频提取音频。"""
        if not self._video_path:
            messagebox.showwarning("未选择视频", "请先选择一个视频文件")
            return
        if not os.path.isfile(self._video_path):
            messagebox.showwarning("文件不存在", "视频文件不存在，请重新选择")
            return

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # 默认输出路径
        base_name = os.path.splitext(os.path.basename(self._video_path))[0]
        default_filename = generate_output_name(base_name, "_extracted", "wav")
        output_path = os.path.join(output_dir, default_filename)

        # 让用户确认路径
        save_path = filedialog.asksaveasfilename(
            title="保存提取的音频",
            initialdir=output_dir,
            initialfile=default_filename,
            filetypes=[
                ("WAV", "*.wav"),
                ("MP3", "*.mp3"),
                ("AAC", "*.aac"),
            ],
        )
        if not save_path:
            return

        self._video_start_btn.configure(state=tk.DISABLED)
        self._video_status_label.config(text="正在提取音频...")
        self._video_progress_bar.start(10)

        thread = threading.Thread(
            target=self._video_extract_work, args=(save_path,), daemon=True
        )
        thread.start()

    def _video_extract_work(self, output_path: str) -> None:
        try:
            _, ext = os.path.splitext(output_path)
            fmt = ext.lstrip(".").lower() or "wav"
            result_path = VideoProcessor.extract_audio(
                self._video_path, output_path, fmt=fmt,
            )

            def _done():
                self._video_progress_bar.stop()
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="提取完成")
                messagebox.showinfo("提取完成", f"音频已保存至：\n{result_path}")
            self.root.after(0, _done)

        except Exception as e:
            def _error():
                self._video_progress_bar.stop()
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="提取失败")
                messagebox.showerror("错误", str(e))
            self.root.after(0, _error)

    def _video_start_mux(self) -> None:
        """开始视频音频合成。"""
        if not self._video_path or not os.path.isfile(self._video_path):
            messagebox.showwarning("未选择视频", "请先选择一个视频文件")
            return

        with self._video_lock:
            audio_paths = list(self._video_audio_paths)

        if not audio_paths:
            messagebox.showwarning("未添加音频", "请至少添加一个音频文件用于合成")
            return

        # 如果有多个音频，先提示只使用第一个
        if len(audio_paths) > 1:
            if not messagebox.askyesno(
                "多个音频",
                f"检测到 {len(audio_paths)} 个音频文件。\n"
                "视频合成一次只能使用一个音频轨道。\n"
                "是否使用第一个音频继续？",
            ):
                return

        audio_path = audio_paths[0]
        if not os.path.isfile(audio_path):
            messagebox.showwarning("音频不存在", f"音频文件不存在：{audio_path}")
            return

        # 确认输出路径
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        video_name = os.path.splitext(os.path.basename(self._video_path))[0]
        _, video_ext = os.path.splitext(self._video_path)
        mode_suffix = "_mixed" if self._mux_mode_var.get() == "mix" else "_newaudio"
        default_filename = generate_output_name(video_name, mode_suffix, video_ext.lstrip("."))
        default_output = os.path.join(output_dir, default_filename)

        save_path = filedialog.asksaveasfilename(
            title="保存合成视频",
            initialdir=output_dir,
            initialfile=default_filename,
            filetypes=[
                ("MP4", "*.mp4"),
                ("MKV", "*.mkv"),
                ("所有文件", "*.*"),
            ],
        )
        if not save_path:
            return

        self._video_start_btn.configure(state=tk.DISABLED)
        self._video_status_label.config(text="正在合成...")
        self._video_progress_bar.start(10)

        mode = MuxMode.MIX if self._mux_mode_var.get() == "mix" else MuxMode.REPLACE
        mix_vol = self._mix_vol_var.get()
        vcodec = self._video_codec_var.get()
        acodec = self._audio_codec_var.get()

        thread = threading.Thread(
            target=self._video_mux_work,
            args=(audio_path, save_path, mode, mix_vol, vcodec, acodec),
            daemon=True,
        )
        thread.start()

    def _video_mux_work(
        self, audio_path: str, output_path: str,
        mode: MuxMode, mix_vol: float,
        vcodec: str, acodec: str,
    ) -> None:
        try:
            result_path = VideoProcessor.mux_audio_video(
                self._video_path, audio_path, output_path,
                mode=mode, mix_volume=mix_vol,
                video_codec=vcodec, audio_codec=acodec,
            )

            def _done():
                self._video_progress_bar.stop()
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="合成完成")
                messagebox.showinfo("合成完成", f"视频已保存至：\n{result_path}")
            self.root.after(0, _done)

        except Exception as e:
            err_msg = str(e)

            def _error():
                self._video_progress_bar.stop()
                self._video_start_btn.configure(state=tk.NORMAL)
                self._video_status_label.config(text="合成失败")
                messagebox.showerror("错误", err_msg)
            self.root.after(0, _error)

    # ==================================================================
    # 进度轮询
    # ==================================================================

    def _start_polling(self) -> None:
        self._reporter.start_polling(
            on_status=self._on_reporter_status,
            on_progress=self._on_reporter_progress,
            on_done=self._on_reporter_done,
            on_error=self._on_reporter_error,
        )

    def _on_reporter_status(self, text: str) -> None:
        current_tab = self._notebook.index(self._notebook.select())
        if current_tab == 0:
            self._audio_status_label.config(text=text)

    def _on_reporter_progress(self, pct: int) -> None:
        current_tab = self._notebook.index(self._notebook.select())
        if current_tab == 0:
            self._audio_progress_var.set(pct)

    def _on_reporter_done(self, output_path: str) -> None:
        self._audio_status_label.config(text="混音完成！")
        self._audio_start_btn.configure(state=tk.NORMAL)
        messagebox.showinfo("完成", f"音频已保存至：\n{output_path}")

    def _on_reporter_error(self, msg: str) -> None:
        self._audio_status_label.config(text="混音出错")
        self._audio_start_btn.configure(state=tk.NORMAL)
        messagebox.showerror("错误", f"混音过程出错：\n{msg}")
