"""
线程安全的进度报告器。

在子线程中报告进度，通过 tkinter 的 ``after()``
在主线程中消费队列消息并更新 UI。
"""

from __future__ import annotations

import queue
from enum import Enum, auto
from typing import Any, Callable


class MsgType(Enum):
    STATUS = auto()
    PROGRESS = auto()
    DONE = auto()
    ERROR = auto()


class ProgressReporter:
    """线程安全的消息队列 + tkinter 轮询集成。"""

    _MSG = MsgType  # 便捷别名

    def __init__(self, root):
        self.root = root
        self._queue: queue.Queue = queue.Queue()

    # ------------------------------------------------------------------
    # 生产者 API（子线程调用）
    # ------------------------------------------------------------------

    def status(self, text: str) -> None:
        """报告状态文本。"""
        self._queue.put((MsgType.STATUS, text))

    def progress(self, percent: int) -> None:
        """报告进度百分比 (0-100)。"""
        self._queue.put((MsgType.PROGRESS, percent))

    def done(self, output_path: str) -> None:
        """报告完成，携带输出路径。"""
        self._queue.put((MsgType.DONE, output_path))

    def error(self, msg: str) -> None:
        """报告错误。"""
        self._queue.put((MsgType.ERROR, msg))

    # ------------------------------------------------------------------
    # 消费者 API（主线程调用）
    # ------------------------------------------------------------------

    def start_polling(
        self,
        on_status: Callable[[str], Any],
        on_progress: Callable[[int], Any],
        on_done: Callable[[str], Any],
        on_error: Callable[[str], Any],
        interval_ms: int = 100,
    ) -> None:
        """开始轮询队列，按 ``interval_ms`` 间隔检查。

        Args:
            on_status: 状态文本回调。
            on_progress: 进度百分比回调。
            on_done: 完成回调，参数为输出路径。
            on_error: 错误回调，参数为错误消息。
            interval_ms: 轮询间隔（毫秒）。
        """

        def _poll():
            try:
                while True:
                    msg = self._queue.get_nowait()
                    msg_type, payload = msg

                    if msg_type is MsgType.STATUS:
                        on_status(payload)
                    elif msg_type is MsgType.PROGRESS:
                        on_progress(payload)
                    elif msg_type is MsgType.DONE:
                        on_done(payload)
                        return  # 完成即停止轮询
                    elif msg_type is MsgType.ERROR:
                        on_error(payload)
                        return
            except queue.Empty:
                self.root.after(interval_ms, _poll)

        self.root.after(interval_ms, _poll)
