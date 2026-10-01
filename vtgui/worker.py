"""The background thread that runs the pipeline off the UI thread."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

import transcribe as core


class TranscribeWorker(QThread):
    """Runs the pipeline off the UI thread and replays its events as signals.

    `next_files` is what makes the queue advance by itself: the core calls it
    after every file, so anything queued while the batch was busy is picked up
    without a restart. Reading it here (on the worker thread) rather than
    restarting the worker between files also means the Whisper model is loaded
    once for the whole session instead of once per file.
    """

    event = Signal(str, dict)

    def __init__(
        self,
        files: list[Path],
        cfg: core.Config,
        parent: QObject | None = None,
        next_files: Callable[[], list[Path]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.files = files
        self.cfg = cfg
        self.next_files = next_files
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            core.run_batch(
                self.files,
                self.cfg,
                on_event=self.event.emit,
                should_cancel=lambda: self._cancel,
                next_files=self.next_files,
            )
        except Exception as exc:
            self.event.emit("fatal", {"error": f"{exc.__class__.__name__}: {exc}"})
        finally:
            self.event.emit("worker_exit", {})
