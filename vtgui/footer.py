"""The footer: the two progress bars and every number beside them.

Split out of `window.py` because it is a self-contained concern with one
non-obvious rule that is easy to break by accident:

    **Nothing in this row may change width because text changed.**

Two things enforce that, and both are needed:

1. Every label that shows changing values is *pinned* to a fixed width, measured
   from its own worst-case text with the font actually in use (`calibrate`).
2. The two progress bars are pinned as well, and the **stage label** is the single
   cell that takes the stretch.

Pinning the labels but leaving the bars stretchy was the original bug: the bars
were then the only flexible cells, so they sat at their floor in a normal window
and visibly grew and shrank whenever the window changed - which reads as the bars
"resizing during processing". If you add a widget here, decide which cell flexes,
and keep it to one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QWidget

from .theme import SPACE
from .widgets import ElidedLabel, pinned_mono

#: Label pins, in px, used before `calibrate` has run. `calibrate` replaces them
#: with measured values, so these only matter for the first paint.
STAGE_FLOOR = 210
PCT_WIDTH = 54
QUEUE_PCT_WIDTH = 84
META_WIDTH = 324
FILE_BAR_WIDTH = 300
QUEUE_BAR_WIDTH = 180

#: The longest text each pinned label must be able to show, used for measurement.
SAMPLES = {
    "stage": "Reading audio from long_test.mp4",
    "file_pct": "working…",
    "queue_pct": "100% of queue",
    "meta": "1/3 files · 24s elapsed · 11.2× realtime · ~1:03 left",
}

_UNPINNED = 1_000_000


class Footer(QFrame):
    """Stage, file bar, file %, queue bar, queue %, and the throughput line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        # Stage: the one flexible cell, so a long filename elides rather than
        # shoving the bars around.
        self.stage_label = ElidedLabel("Idle")
        self.stage_label.setMinimumWidth(STAGE_FLOOR)
        self.stage_label.setAccessibleName("Current step")
        layout.addWidget(self.stage_label, 1)

        # Percentages sit *beside* the bars, never inside them: text painted over
        # the accent fill would fall below the contrast floor.
        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        self.file_progress.setTextVisible(False)
        self.file_progress.setFixedWidth(FILE_BAR_WIDTH)
        self.file_progress.setAccessibleName("Current file progress")
        layout.addWidget(self.file_progress)

        self.file_pct = pinned_mono("—", PCT_WIDTH)
        self.file_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.file_pct.setAccessibleName("Current file percentage")
        layout.addWidget(self.file_pct)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setFixedWidth(QUEUE_BAR_WIDTH)
        self.overall_progress.setAccessibleName("Overall queue progress")
        layout.addWidget(self.overall_progress)

        self.queue_pct = pinned_mono("0% of queue", QUEUE_PCT_WIDTH)
        self.queue_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.queue_pct.setAccessibleName("Queue percentage")
        layout.addWidget(self.queue_pct)

        self.meta_label = pinned_mono("0 files", META_WIDTH)
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.meta_label)

    def calibrate(self) -> None:
        """Pin every label to its own worst case, measured with the real font.

        Widths cannot be hardcoded: which font actually renders depends on the
        machine's stack (Segoe UI Variable, Cascadia Mono, ...), and a wrong guess
        either clips text or leaves a label able to grow. Re-running this is safe
        and is what a theme change does.
        """
        for label, sample in (
            (self.stage_label, SAMPLES["stage"]),
            (self.file_pct, SAMPLES["file_pct"]),
            (self.queue_pct, SAMPLES["queue_pct"]),
            (self.meta_label, SAMPLES["meta"]),
        ):
            current = label.full_text()
            label.setMinimumWidth(0)
            label.setMaximumWidth(_UNPINNED)  # measure without eliding first
            label.set_full_text(sample)
            if label is self.stage_label:
                # Deliberately *not* pinned to the sample: a flexible stage cell is
                # what absorbs window resizing. A modest floor is all it needs.
                label.setMinimumWidth(min(STAGE_FLOOR, label.sizeHint().width()))
            else:
                label.setFixedWidth(label.sizeHint().width() + 4)
            label.set_full_text(current)

        # A bar is wide enough for its own headline plus the rounded ends.
        for bar in (self.file_progress, self.overall_progress):
            bar.setMinimumWidth(0)
            bar.setMaximumWidth(_UNPINNED)
            bar.setFixedWidth(bar.fontMetrics().horizontalAdvance("100%") + SPACE["xl"] * 2)
