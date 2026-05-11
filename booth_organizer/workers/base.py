from __future__ import annotations

from PySide6.QtCore import QRunnable, QObject, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int)
    status = Signal(str)        # human-readable status update
    file_done = Signal(str)     # filename that completed
    file_failed = Signal(str, str)  # filename, error message


class BaseWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            result = self._run()
        except Exception as e:
            if not self._is_cancelled:
                self.signals.error.emit(str(e))
        else:
            if not self._is_cancelled:
                self.signals.finished.emit(result)

    def _run(self):
        raise NotImplementedError
