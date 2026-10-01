"""Small shared presentation helpers used by more than one screen."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from vtcore.runtime import quiet_subprocess

from .theme import SPACE


def human_size(size_mb: float) -> str:
    """Adaptive file size, so a 0.4 MB clip does not read as '0 MB'."""
    if size_mb <= 0:
        return "-"
    if size_mb < 1:
        return f"{size_mb * 1024:.0f} KB"
    if size_mb < 1024:
        return f"{size_mb:.0f} MB"
    return f"{size_mb / 1024:.1f} GB"


def eyebrow(text: str) -> QLabel:
    """Uppercase micro-label with tracking, used above every card's content."""
    label = QLabel(text.upper())
    label.setObjectName("Eyebrow")
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    return label


def mono(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("MetaMono")
    return label


def elbow_field(label: str, widget: QWidget) -> QWidget:
    """A small caption stacked over its control, for label/input pairs.

    Shared by the settings grid and the Auto-transcribe card so both keep the same
    caption style and spacing.
    """
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE["xs"])
    text = QLabel(label)
    text.setObjectName("Meta")
    layout.addWidget(text)
    layout.addWidget(widget)
    return holder


class Card(QFrame):
    """Double-bezel container: an outer shell with an inner padded core."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["card"], SPACE["card"], SPACE["card"], SPACE["card"])
        outer.setSpacing(SPACE["gap"])
        outer.addWidget(eyebrow(title))

        body = QWidget()
        body.setObjectName("CardBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SPACE["gap"])
        outer.addWidget(body)

    def add(self, widget: QWidget | QHBoxLayout) -> None:
        if isinstance(widget, QHBoxLayout):
            self.body.addLayout(widget)
        else:
            self.body.addWidget(widget)


class EmptyState(QWidget):
    """Shown instead of the table while the queue is empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(SPACE["sm"])

        icon = QLabel()
        icon.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView).pixmap(QSize(40, 40))
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Nothing queued yet")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Paste a video path above, or use Browse to pick a file.")
        hint.setObjectName("EmptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(hint)


class ElidedLabel(QLabel):
    """A fixed-width label that shortens its text instead of clipping it.

    Two jobs in one. The progress bars beside these labels must never resize,
    which only holds if the label cannot grow - so the width is pinned. And text
    longer than the pin should degrade with an ellipsis, not be cut mid-glyph.

    Elision uses the label's own resolved font and the label's ordinary painting
    does the drawing, so the stylesheet still decides family and colour. The full
    string stays available through `full_text()` and is what the tooltip shows.
    """

    #: Qt's QWIDGETSIZE_MAX: the default maximumWidth, i.e. "not pinned".
    _UNPINNED = 16777215

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        self._full_text = ""
        super().__init__("", parent)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        """Set the complete value; what is shown is elided to the pinned width."""
        self._full_text = text
        super().setToolTip(text)
        self._apply_elision()

    def full_text(self) -> str:
        """The complete value, however much of it happens to fit."""
        return self._full_text

    def _apply_elision(self) -> None:
        """Elide against the pinned width, never against the current geometry.

        Geometry is only meaningful after a layout pass, so a freshly unpinned
        label would still be elided to its old size - which would make sizeHint()
        report the truncated text and break width calibration.
        """
        limit = self.maximumWidth()
        if limit >= self._UNPINNED:  # not pinned: everything fits by definition
            super().setText(self._full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(0, limit - 4))
        )


def pinned_mono(text: str, width: int) -> ElidedLabel:
    """A fixed-width monospace label: the numbers in the footer."""
    label = ElidedLabel(text)
    label.setObjectName("MetaMono")
    label.setFixedWidth(width)
    return label


def read_clipboard() -> str | None:
    """Best-effort clipboard read using what the OS already provides."""
    if sys.platform == "win32":
        commands = [["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]]
    elif sys.platform == "darwin":
        commands = [["pbpaste"]]
    else:
        commands = [["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"]]

    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **quiet_subprocess(),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None
