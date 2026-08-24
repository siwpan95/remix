import os
import sys
import threading
import queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 尝试导入 pydub，若无则给出提示
try:
    from pydub import AudioSegment
    from pydub.utils import make_chunks
except ImportError:
    messagebox.showerror("缺少依赖", "请安装 pydub：\npip install pydub\npydub 将使用项目内置的 ffmpeg（bin/ 目录）")
    sys.exit(1)

class MixerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("简易混音器")
        self.root.geometry("500x400")

        # 存储选择的文件路径
        self.file_paths = []

        # 线程通信队列
        self.progress_queue = queue.Queue()

        # 构建界面
        self.create_widgets()

        # 定期检查队列更新 UI
        self.process_queue()

    def create_widgets(self):
        # 文件列表区域
        frame_list = ttk.LabelFrame(self.root, text="音频文件列表", padding=10)
        frame_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.listbox = tk.Listbox(frame_list, selectmode=tk.EXTENDED, height=8)
        scrollbar = ttk.Scrollbar(frame_list, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 操作按钮
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(btn_frame, text="添加音乐", command=self.add_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="移除选中", command=self.remove_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空列表", command=self.clear_list).pack(side=tk.LEFT, padx=5)

        # 进度条
        progress_frame = ttk.Frame(self.root)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, length=400,
            mode='determinate', variable=self.progress_var
        )
        self.progress_bar.pack(fill=tk.X, expand=True)

        # 开始按钮与状态标签
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=10, pady=5)

        self.start_btn = ttk.Button(bottom_frame, text="开始混音", command=self.start_mixing)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(bottom_frame, text="就绪")
        self.status_label.pack(side=tk.LEFT, padx=10)

    def add_files(self):
        """打开文件选择对话框，添加音频文件到列表"""
        files = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频文件", "*.mp3 *.wav *.ogg *.flac *.m4a"), ("所有文件", "*.*")]
        )
        for f in files:
            if f not in self.file_paths:
                self.file_paths.append(f)
                self.listbox.insert(tk.END, os.path.basename(f))

    def remove_selected(self):
        """移除列表框中选中的文件"""
        selected_indices = self.listbox.curselection()
        # 从后向前删除，避免索引错乱
        for i in reversed(selected_indices):
            self.listbox.delete(i)
            del self.file_paths[i]

    def clear_list(self):
        """清空整个文件列表"""
        self.listbox.delete(0, tk.END)
        self.file_paths.clear()

    def start_mixing(self):
        """启动混音线程"""
        if len(self.file_paths) < 2:
            messagebox.showwarning("文件不足", "请至少添加两个音频文件")
            return

        self.start_btn.configure(state=tk.DISABLED)
        self.status_label.config(text="正在混音...")
        self.progress_var.set(0)

        # 开启新线程处理混音，避免界面冻结
        thread = threading.Thread(target=self.mix_audio, daemon=True)
        thread.start()

    def mix_audio(self):
        """混音核心逻辑（在子线程中执行）"""
        try:
            # 确定输出目录（项目根目录/output）
            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(base_dir, "output")
            os.makedirs(output_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, f"mixed_output_{timestamp}.wav")

            # 加载所有音频
            segments = []
            for path in self.file_paths:
                audio = AudioSegment.from_file(path)
                segments.append(audio)
                self.progress_queue.put(("status", f"已加载: {os.path.basename(path)}"))

            # 统一参数：取第一个音频的采样率、通道数、采样宽度
            target_rate = segments[0].frame_rate
            target_channels = segments[0].channels
            target_width = segments[0].sample_width

            # 将所有音频转换为统一格式
            converted = []
            for seg in segments:
                seg = seg.set_frame_rate(target_rate)
                seg = seg.set_channels(target_channels)
                seg = seg.set_sample_width(target_width)
                converted.append(seg)

            # 获取所有音频的样本数组（有符号16位）
            sample_arrays = [list(seg.get_array_of_samples()) for seg in converted]

            # 最长的样本数组长度
            max_len = max(len(arr) for arr in sample_arrays)
            total_samples = max_len
            self.progress_queue.put(("max", max_len))

            # 混合：直接相加，最后削波
            mixed = [0] * max_len
            for arr in sample_arrays:
                for i in range(len(arr)):
                    mixed[i] += arr[i]

            # 削波处理：限制在16位有符号整数范围
            clip_min = -32768
            clip_max = 32767
            for i in range(max_len):
                if mixed[i] > clip_max:
                    mixed[i] = clip_max
                elif mixed[i] < clip_min:
                    mixed[i] = clip_min

                # 每处理 1024 个样本更新一次进度
                if i % 1024 == 0 or i == max_len - 1:
                    progress = int((i + 1) / max_len * 100)
                    self.progress_queue.put(("progress", progress))

            # 从混合后的样本数组构建 AudioSegment
            mixed_audio = AudioSegment(
                data=bytes(mixed),          # 需要字节数据，但 mixed 是 list of ints
                sample_width=target_width,
                frame_rate=target_rate,
                channels=target_channels
            )

            # 导出为 wav
            mixed_audio.export(output_path, format="wav")

            # 通知完成
            self.progress_queue.put(("done", output_path))

        except Exception as e:
            self.progress_queue.put(("error", str(e)))

    def process_queue(self):
        """定时检查队列，更新 UI（主线程调用）"""
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                if msg[0] == "status":
                    self.status_label.config(text=msg[1])
                elif msg[0] == "max":
                    pass  # 可用于设置 progressbar 的最大值（但这里固定百分比）
                elif msg[0] == "progress":
                    self.progress_var.set(msg[1])
                elif msg[0] == "done":
                    self.status_label.config(text="混音完成！")
                    self.start_btn.configure(state=tk.NORMAL)
                    messagebox.showinfo("完成", f"音频已保存至：\n{msg[1]}")
                elif msg[0] == "error":
                    self.status_label.config(text="混音出错")
                    self.start_btn.configure(state=tk.NORMAL)
                    messagebox.showerror("错误", f"混音过程出错：\n{msg[1]}")
        except queue.Empty:
            pass
        finally:
            # 每隔 100 毫秒再次检查队列
            self.root.after(100, self.process_queue)

def main():
    root = tk.Tk()
    app = MixerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()