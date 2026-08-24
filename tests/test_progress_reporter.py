"""
测试 progress_reporter — 线程安全进度报告。

覆盖：
  - 消息发送（status / progress / done / error）
  - 队列消费轮询
  - 完成/错误时停止轮询
  - 边界（空消息、快速连续发送）
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.progress_reporter import ProgressReporter, MsgType


# ---- 辅助 ----

def _make_tk():
    """创建 Tk 根窗口，如果 Tcl/tkinter 环境不可用则跳过测试。"""
    try:
        import tkinter as tk
    except Exception:
        pytest.skip("tkinter 未安装，跳过 GUI 相关测试")
    try:
        root = tk.Tk()
        root.withdraw()
        return root
    except Exception as e:
        pytest.skip(f"Tk 环境不可用: {e}")


class TestProgressReporter:
    """ProgressReporter 单元测试。"""

    def test_status_message(self):
        """status 消息应正确入队。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            r.status("加载中...")

            msg = r._queue.get_nowait()
            assert msg == (MsgType.STATUS, "加载中...")
        finally:
            root.destroy()

    def test_progress_message(self):
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            r.progress(50)

            msg = r._queue.get_nowait()
            assert msg == (MsgType.PROGRESS, 50)
        finally:
            root.destroy()

    def test_done_message(self):
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            r.done("/tmp/output.wav")

            msg = r._queue.get_nowait()
            assert msg == (MsgType.DONE, "/tmp/output.wav")
        finally:
            root.destroy()

    def test_error_message(self):
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            r.error("文件不存在")

            msg = r._queue.get_nowait()
            assert msg == (MsgType.ERROR, "文件不存在")
        finally:
            root.destroy()

    def test_multiple_messages_in_order(self):
        """多条消息应保持发送顺序。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            r.status("开始")
            r.progress(25)
            r.progress(50)
            r.status("处理中")
            r.progress(100)
            r.done("/tmp/out.wav")

            messages = []
            while True:
                try:
                    messages.append(r._queue.get_nowait())
                except:
                    from queue import Empty
                    break

            assert len(messages) == 6
            assert messages[0] == (MsgType.STATUS, "开始")
            assert messages[-1] == (MsgType.DONE, "/tmp/out.wav")
        finally:
            root.destroy()

    def test_thread_safety(self):
        """多线程并发写队列不应崩溃。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)

            def worker(start, step):
                for i in range(10):
                    r.progress(start + i * step)
                    time.sleep(0.001)

            threads = [
                threading.Thread(target=worker, args=(0, 1)),
                threading.Thread(target=worker, args=(.10, 1)),
                threading.Thread(target=worker, args=(.20, 1)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # 应收到 30 条消息
            count = 0
            while True:
                try:
                    r._queue.get_nowait()
                    count += 1
                except:
                    from queue import Empty
                    break
            assert count == 30
        finally:
            root.destroy()

    def test_start_polling_calls_callback(self):
        """start_polling 注册的回调应在消息到达时被调用。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            results = []

            r.start_polling(
                on_status=lambda t: results.append(("status", t)),
                on_progress=lambda p: results.append(("progress", p)),
                on_done=lambda p: results.append(("done", p)),
                on_error=lambda e: results.append(("error", e)),
                interval_ms=1,  # 极短间隔确保 update() 能触发
            )

            # 发送消息
            r.status("测试状态")

            # after() 回调需要真实时间流逝
            import time
            for _ in range(10):
                root.update()
                time.sleep(0.005)
                if results:
                    break

            assert len(results) >= 1
            assert results[0] == ("status", "测试状态")
        finally:
            root.destroy()

    def test_polling_stops_on_done(self):
        """done 消息后轮询应立即停止。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            results = []

            r.start_polling(
                on_status=lambda t: results.append(("status", t)),
                on_progress=lambda p: results.append(("progress", p)),
                on_done=lambda p: results.append(("done", p)),
                on_error=lambda e: results.append(("error", e)),
                interval_ms=10,
            )

            r.progress(50)
            r.done("out.wav")
            r.progress(100)  # done 之后这条应被忽略

            import time
            for _ in range(10):
                root.update()
                time.sleep(0.005)
                if results:
                    break

            # done 出现了
            assert ("done", "out.wav") in results
            # done 之后的 progress 不应出现
            progress_count = sum(1 for r_ in results if r_[0] == "progress")
            assert progress_count == 1
        finally:
            root.destroy()

    def test_polling_stops_on_error(self):
        """error 消息后轮询应立即停止。"""
        root = _make_tk()
        try:
            r = ProgressReporter(root)
            results = []

            r.start_polling(
                on_status=lambda t: results.append(("status", t)),
                on_progress=lambda p: results.append(("progress", p)),
                on_done=lambda p: results.append(("done", p)),
                on_error=lambda e: results.append(("error", e)),
                interval_ms=10,
            )

            r.error("崩溃了")
            r.progress(50)  # error 之后应被忽略

            # after() 回调需要真实时间流逝，加入 sleep
            import time
            for _ in range(10):
                root.update()
                time.sleep(0.005)
                if results:
                    break

            assert ("error", "崩溃了") in results
            progress_count = sum(1 for r_ in results if r_[0] == "progress")
            assert progress_count == 0
        finally:
            root.destroy()


class TestMsgType:
    """MsgType 枚举测试。"""

    def test_unique_values(self):
        values = [MsgType.STATUS.value, MsgType.PROGRESS.value,
                  MsgType.DONE.value, MsgType.ERROR.value]
        assert len(set(values)) == 4

    def test_shortcut(self):
        assert ProgressReporter._MSG is MsgType
