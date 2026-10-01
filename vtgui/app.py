"""Process entry point: build the QApplication and show the window."""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import transcribe as core

from .theme import APP_NAME, ORG_NAME
from .window import MainWindow


def install_exit_guards(app: QApplication) -> None:
    """Make every exit path release the model, not just closing the window.

    ``closeEvent`` handles the normal case, but the app can also leave through
    the Quit menu, a startup failure or an unhandled exception. ``aboutToQuit``
    covers Qt-initiated exits and ``atexit`` covers interpreter shutdown; both
    are idempotent, so overlapping calls are harmless.

    Nothing here is needed for a hard kill (Task Manager, ``taskkill /F``): the
    driver reclaims the VRAM when the process dies. These guards exist for the
    exits where the process outlives the window.
    """
    app.aboutToQuit.connect(core.unload_model)
    atexit.register(core.unload_model)


def allow_ctrl_c(app: QApplication) -> QTimer:
    """Let Ctrl+C quit a console run instead of being swallowed by Qt.

    Qt's event loop runs in C++, so a Python signal handler only gets a chance
    between bytecodes. A timer that does nothing keeps the interpreter ticking,
    which is the standard way to make SIGINT work in a Qt application.
    """
    with contextlib.suppress(ValueError, OSError, AttributeError):
        signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.start(200)
    keepalive.timeout.connect(lambda: None)
    return keepalive


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")

    install_exit_guards(app)
    # Bound to a local for the session: `main` stays on the stack until the event
    # loop returns, and a collected timer would stop delivering Ctrl+C.
    keepalive = allow_ctrl_c(app)  # noqa: F841 - lifetime handle

    window = MainWindow()
    # Open full screen: the settings column is laid out to fit without scrolling,
    # and it is sized for a maximised window. A remembered geometry is still
    # honoured for the *size* only when the user restores the window themselves.
    window.showMaximized()

    for argument in sys.argv[1:]:
        if not argument.startswith("-"):
            window.add_paths(f'"{argument}"')

    return app.exec()
