"""Desktop GUI for local, GPU-accelerated transcription.

Layering (each module imports only from the ones above it):

    theme -> catalog -> widgets -> queue -> dialogs -> window -> app
                          ^                              ^
                          +----------- worker -----------+

`theme` owns every colour/spacing/type token and generates the stylesheet, so
the two themes cannot drift apart. `window` is the only module that wires
signals to the pipeline.
"""

from __future__ import annotations

from .app import main
from .catalog import (
    DEFAULT_ADVANCED,
    FORMATS,
    LANGUAGES,
    MODELS,
    PARALLEL_JOB_CHOICES,
    PRESETS,
)
from .dialogs import AdvancedDialog
from .footer import Footer
from .queue import QueueItem, QueueModel, StatusDelegate
from .theme import (
    APP_NAME,
    DARK,
    LIGHT,
    MAX_LOG_LINES,
    MONO_FONT,
    ORG_NAME,
    RADIUS,
    SPACE,
    STATUS_STYLE,
    THEMES,
    TYPE,
    UI_FONT,
    build_stylesheet,
    make_app_icon,
)
from .watch import DEFAULT_SETTLE_SECONDS, FolderWatcher, is_locked, is_temporary
from .watchcard import WatchCard
from .widgets import Card, EmptyState, eyebrow, human_size, mono, read_clipboard
from .window import MainWindow
from .worker import TranscribeWorker

__all__ = [
    "APP_NAME",
    "DARK",
    "DEFAULT_ADVANCED",
    "DEFAULT_SETTLE_SECONDS",
    "FORMATS",
    "LANGUAGES",
    "LIGHT",
    "MAX_LOG_LINES",
    "MODELS",
    "MONO_FONT",
    "ORG_NAME",
    "PARALLEL_JOB_CHOICES",
    "PRESETS",
    "RADIUS",
    "SPACE",
    "STATUS_STYLE",
    "THEMES",
    "TYPE",
    "UI_FONT",
    "AdvancedDialog",
    "Card",
    "EmptyState",
    "FolderWatcher",
    "Footer",
    "MainWindow",
    "QueueItem",
    "QueueModel",
    "StatusDelegate",
    "TranscribeWorker",
    "WatchCard",
    "build_stylesheet",
    "eyebrow",
    "human_size",
    "is_locked",
    "is_temporary",
    "main",
    "make_app_icon",
    "mono",
    "read_clipboard",
]
