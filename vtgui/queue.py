"""The queue table: row model, status pills and the size/formatting they need."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QStyledItemDelegate,
)

from .theme import STATUS_STYLE
from .widgets import human_size


@dataclass
class QueueItem:
    path: Path
    size_mb: float = 0.0
    status: str = "Queued"
    result: str = ""
    outputs: list[str] = field(default_factory=list)
    note: str = ""


#: Statuses that mean "this row must not be sent through the model again".
FINISHED_STATUSES = ("Done", "Skipped")


class QueueModel(QAbstractTableModel):
    """Table model for the queue. Keeps the UI dumb and the data testable."""

    HEADERS = ("File", "Size", "Status", "Result")

    def __init__(self) -> None:
        super().__init__()
        self.items: list[QueueItem] = []

    # -- Qt plumbing ---------------------------------------------------------
    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent and parent.isValid() else len(self.items)

    def columnCount(self, _parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(  # noqa: N802 - Qt override
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return item.path.name
            if column == 1:
                return human_size(item.size_mb)
            if column == 2:
                return item.status
            return item.result
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [str(item.path)]
            if item.note:
                lines.append(item.note)
            lines.extend(item.outputs)
            return "\n".join(lines)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (1, 2):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.FontRole and column in (1, 3):
            font = QFont()
            font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
            font.setPointSizeF(max(8.0, QApplication.font().pointSizeF() - 0.5))
            return font
        return None

    # -- domain API ----------------------------------------------------------
    def add_paths(self, paths: list[Path]) -> int:
        known = {item.path.resolve() for item in self.items}
        added = 0
        for path in paths:
            resolved = path.resolve()
            if resolved in known:
                continue
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
            except OSError:
                size_mb = 0.0
            self.beginInsertRows(QModelIndex(), len(self.items), len(self.items))
            self.items.append(QueueItem(path=path, size_mb=size_mb))
            self.endInsertRows()
            known.add(resolved)
            added += 1
        return added

    def row_of(self, path: str | Path) -> int:
        target = Path(path).resolve()
        for row, item in enumerate(self.items):
            if item.path.resolve() == target:
                return row
        return -1

    def update_item(self, row: int, **changes) -> None:
        if not 0 <= row < len(self.items):
            return
        for key, value in changes.items():
            setattr(self.items[row], key, value)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.HEADERS) - 1))

    def remove_rows(self, rows: list[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if not 0 <= row < len(self.items):
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.items[row]
            self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.items.clear()
        self.endResetModel()

    def paths(self) -> list[Path]:
        return [item.path for item in self.items]

    def pending_rows(self) -> list[int]:
        """Rows that still need transcribing.

        A row stays in the table after it succeeds so its result is visible, but
        it must not be sent through the model again just because another file was
        added — that would silently re-transcribe finished work. Rows skipped
        because a transcript already exists are excluded for the same reason.
        """
        return [row for row, item in enumerate(self.items) if item.status not in FINISHED_STATUSES]

    def claim_pending(self) -> list[Path]:
        """Take the files still waiting and mark their rows as running.

        This is the queue's pull side: the worker calls it after every file, so a
        file added mid-run is picked up the moment the current one finishes. Taking
        (rather than peeking) matters — it is what stops two runs from grabbing the
        same row, and it is why `total` in the footer counts rows that were truly
        handed over.
        """
        rows = [row for row in self.pending_rows() if self.items[row].status != "Running"]
        if not rows:
            return []
        claimed: list[Path] = []
        for row in rows:
            self.update_item(row, status="Running", result="", note="")
            claimed.append(self.items[row].path)
        return claimed


class StatusDelegate(QStyledItemDelegate):
    """Paints the Status column as a tinted pill instead of plain text."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tints: dict[str, tuple[str, str]] = {}

    def set_palette(self, palette: dict) -> None:
        self.tints = {
            status: (palette[tint_key], palette[fg_key])
            for status, (tint_key, fg_key) in STATUS_STYLE.items()
        }

    def paint(self, painter: QPainter, option, index) -> None:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        background, foreground = self.tints.get(text, ("transparent", "#9B9BA4"))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(8, 8, -8, -8)
        if rect.width() < 24:
            rect.setWidth(24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(foreground))
        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(8, 0, -8, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
        painter.restore()
