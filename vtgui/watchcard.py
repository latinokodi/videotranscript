"""The Auto-transcribe card: pick a folder, and new files queue themselves.

This module owns the whole folder-monitoring feature — the polling rules live in
`watch.FolderWatcher`, and this widget is the controls, the status line and the
settings that drive it. Splitting it out of `window.py` keeps the three
responsibilities separate:

    watch.py      when is a file complete?        (no UI, no queue)
    watchcard.py  the controls and the settings   (no queue, no pipeline)
    window.py     what happens to a ready file    (queueing and running)

The widget therefore never queues anything itself. It reports a finished file
through `file_ready` and lets the window decide, which is what keeps this module
free of `QueueModel`, `Config` and the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from .theme import SPACE
from .watch import (
    DEFAULT_SETTLE_SECONDS,
    MAX_SETTLE_SECONDS,
    MIN_SETTLE_SECONDS,
    FolderWatcher,
)
from .widgets import Card, ElidedLabel, elbow_field


class WatchCard(Card):
    """Folder monitoring as one widget: controls, status and the poller.

    Signals
    -------
    file_ready(Path)
        A file has finished arriving. The window queues it.
    message(str, str)
        ``(level, text)`` for the Activity log.
    changed()
        The folder or settling window changed, so settings should be saved.
    """

    file_ready = Signal(Path)
    message = Signal(str, str)
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Auto-transcribe", parent)
        self.watcher = FolderWatcher(self, settle_seconds=DEFAULT_SETTLE_SECONDS)
        self.watcher.file_ready.connect(self.file_ready)
        self.watcher.message.connect(self.message)
        self._queued = 0
        self._browse_dir = ""
        self._build()

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        self.status = ElidedLabel("")
        self.status.setObjectName("Meta")
        self.status.setAccessibleName("Folder monitoring status")
        # Elides to whatever width the card has: the status is a long folder path
        # plus counts, and the card sits in a column that is deliberately narrow.
        self.status.setMinimumWidth(0)
        self.add(self.status)

        row = QHBoxLayout()
        row.setSpacing(SPACE["sm"])
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("folder to watch for new media")
        self.folder_input.setAccessibleName("Monitored folder")
        self.folder_input.setToolTip(
            "Pick a folder your downloads land in.\n\n"
            "Every poll finds media files in it, waits until each one has stopped\n"
            "growing AND is no longer held open by another program, and only then\n"
            "queues it. Temporary names (.part, .crdownload, ...) are ignored\n"
            "outright. Files already sitting in the folder are transcribed too."
        )
        self.folder_input.textChanged.connect(self._on_settings_changed)
        row.addWidget(self.folder_input, 1)

        self.btn_browse = QPushButton("…")
        self.btn_browse.setFixedWidth(36)
        self.btn_browse.setToolTip("Choose the folder to monitor")
        self.btn_browse.setAccessibleName("Choose monitored folder")
        self.btn_browse.clicked.connect(self.choose_folder)
        row.addWidget(self.btn_browse)
        self.add(row)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE["sm"])
        self.btn_toggle = QPushButton("Start watching")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setAccessibleName("Watch folder for new files")
        self.btn_toggle.setToolTip(
            "Queue new files here automatically.\n"
            "A file is only started once it has stopped changing and is unlocked."
        )
        self.btn_toggle.toggled.connect(self.toggle)
        controls.addWidget(self.btn_toggle, 1)

        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(MIN_SETTLE_SECONDS, MAX_SETTLE_SECONDS)
        self.settle_spin.setValue(DEFAULT_SETTLE_SECONDS)
        self.settle_spin.setSuffix(" s")
        self.settle_spin.setAccessibleName("Seconds a file must stay unchanged")
        self.settle_spin.setToolTip(
            "How long a file must stay unchanged before it is treated as complete.\n"
            "Raise it for slow or stalling downloads; lower it for a snappier start."
        )
        self.settle_spin.valueChanged.connect(self._on_settings_changed)
        controls.addWidget(elbow_field("Settle", self.settle_spin))
        self.add(controls)
        self.refresh_status()

    # -- public API used by the window ---------------------------------------

    @property
    def active(self) -> bool:
        return self.watcher.active

    @property
    def settle_seconds(self) -> int:
        return self.settle_spin.value()

    @property
    def folder(self) -> str:
        return self.folder_input.text().strip()

    def note_queued(self, added: bool) -> None:
        """Record what the window did with the file it was handed."""
        if added:
            self._queued += 1
        self.refresh_status()

    # -- controls ------------------------------------------------------------

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder to watch for new media",
            self.folder or self._browse_dir,
        )
        if folder:
            self.folder_input.setText(folder)

    def toggle(self, on: bool) -> None:
        """Start or stop monitoring; the button always reflects the real state.

        Returns nothing because failure is reported through `message`, which the
        window turns into the Activity log — this module does not raise dialogs.
        """
        if on:
            folder = self.folder
            if not folder:
                self.message.emit(
                    "warning",
                    "choose the folder to monitor first - the one your downloads land in",
                )
                self._show_state(False)
                return
            if not self.watcher.start(Path(folder), self.settle_seconds):
                self.message.emit("warning", f"cannot watch {folder} - it is not a readable folder")
                self._show_state(False)
                return
            self._queued = 0
        else:
            self.watcher.stop()
        self._show_state(self.watcher.active)
        self.refresh_status()
        self.changed.emit()

    def shutdown(self) -> None:
        """Stop polling before teardown; a timer firing into a dead window is ugly."""
        self.watcher.stop()

    # -- persistence ---------------------------------------------------------

    def restore(self, folder: str, settle: int, was_active: bool) -> None:
        """Restore remembered settings, resuming monitoring when it was on.

        `was_active` is applied last and only if the folder still exists, so a
        deleted or renamed folder leaves monitoring off rather than erroring at
        every poll.
        """
        self.folder_input.setText(folder)
        self.settle_spin.setValue(min(MAX_SETTLE_SECONDS, max(MIN_SETTLE_SECONDS, settle)))
        if was_active and folder and Path(folder).is_dir():
            self.watcher.start(Path(folder), self.settle_seconds)
            self._queued = 0
        elif was_active:
            self.message.emit("warning", "watched folder is gone - monitoring is off")
        self._show_state(self.watcher.active)
        self.refresh_status()

    def state(self) -> tuple[str, int, bool]:
        """``(folder, settle seconds, active)`` for the settings store."""
        return self.folder, self.settle_seconds, self.watcher.active

    def set_browse_dir(self, folder: str) -> None:
        """Where the folder picker opens when nothing has been chosen yet."""
        self._browse_dir = folder

    # -- status --------------------------------------------------------------

    def refresh_status(self) -> None:
        if not self.watcher.active:
            self.status.setText("Not watching")
            self.status.setToolTip("")
            return
        parts = [f"Watching {self.watcher.folder}"]
        settling = self.watcher.settling_names()
        if settling:
            shown = ", ".join(settling[:3])
            more = f" +{len(settling) - 3}" if len(settling) > 3 else ""
            parts.append(f"arriving: {shown}{more}")
        if self._queued:
            parts.append(f"{self._queued} queued")
        text = " · ".join(parts)
        # The card is narrow and a folder path is long, so this row elides. The
        # tooltip keeps the whole value reachable - a clipped path would hide
        # *which* folder is being watched, which is the one thing this line is for.
        self.status.set_full_text(text)

    def _show_state(self, on: bool) -> None:
        """Set the toggle without re-entering the slot that called us."""
        self.btn_toggle.blockSignals(True)
        self.btn_toggle.setChecked(on)
        self.btn_toggle.blockSignals(False)
        self.btn_toggle.setText("Stop watching" if on else "Start watching")

    def _on_settings_changed(self) -> None:
        """A live change to the folder or settling window takes effect at once."""
        if self.watcher.active and self.folder:
            self.watcher.start(Path(self.folder), self.settle_seconds)
        self.refresh_status()
        self.changed.emit()
