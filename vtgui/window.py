"""The main window: queue, settings, progress and log wiring."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

import transcribe as core

from .catalog import (
    DEFAULT_ADVANCED,
    FORMATS,
    LANGUAGES,
    MODELS,
    PARALLEL_JOB_CHOICES,
    PRESETS,
)
from .dialogs import AdvancedDialog
from .events import PipelineEventsMixin
from .footer import Footer
from .queue import QueueModel, StatusDelegate
from .theme import (
    APP_NAME,
    DARK,
    MAX_LOG_LINES,
    ORG_NAME,
    SPACE,
    THEMES,
    build_stylesheet,
    make_app_icon,
)
from .watch import DEFAULT_SETTLE_SECONDS
from .watchcard import WatchCard
from .widgets import (
    Card,
    EmptyState,
    elbow_field,
    read_clipboard,
)
from .worker import TranscribeWorker


class MainWindow(PipelineEventsMixin, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.model = QueueModel()
        self.status_delegate = StatusDelegate(self)
        self.worker: TranscribeWorker | None = None
        # Folder monitoring lives in its own module; the window keeps a reference
        # so it can persist its state and stop it on the way out.
        self.watch: WatchCard | None = None
        self.advanced: dict = dict(DEFAULT_ADVANCED)
        self._file_started_at = time.time()
        self._file_duration = 0.0
        self._current_progress = 0.0
        self._last_outdir: Path | None = None
        self._total_files = 0
        self._done_files = 0
        self._syncing = False
        self._stage_name = "idle"
        self._download_detail = ""
        self._stage_started_at = time.time()
        self._last_browse_dir = ""

        self.setWindowTitle("videotranscript")
        # Wide enough that the footer's fixed-width labels (calibrated at runtime)
        # plus both progress bars always fit without eliding.
        self.setMinimumSize(1140, 660)
        self.resize(1280, 820)

        self._build_ui()
        self._build_menus()

        # Settings are written shortly after any change, not only on a clean exit,
        # so a crash or a kill cannot lose the user's setup.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._save_settings)

        self._restore_settings()
        self.apply_theme(self.settings.value("theme", "dark", str))
        self._connect_persistence()

        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._audit_environment()
        self._refresh_actions()

    # ------------------------------------------------------------- construction

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        outer.setSpacing(SPACE["md"])

        outer.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([780, 560])
        self.splitter = splitter
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_footer())
        self.statusBar().showMessage("Ready")

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"] + 2, SPACE["md"], SPACE["sm"] + 2)
        layout.setSpacing(SPACE["sm"])

        mark = QLabel()
        mark.setPixmap(make_app_icon(DARK).pixmap(QSize(34, 34)))
        self.mark = mark
        layout.addWidget(mark)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("videotranscript")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Local GPU transcription with word-accurate timestamps")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        self.chip_device = QLabel("detecting GPU…")
        self.chip_device.setObjectName("Chip")
        self.chip_models = QLabel("models loaded: 0")
        self.chip_models.setObjectName("Chip")
        layout.addWidget(self.chip_device)
        layout.addWidget(self.chip_models)
        return bar

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])

        # --- source card ---
        source = Card("Source")
        row = QHBoxLayout()
        row.setSpacing(SPACE["sm"])
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Paste a video path, then press Enter")
        self.path_input.setClearButtonEnabled(True)
        self.path_input.setAccessibleName("Video path")
        self.path_input.returnPressed.connect(self.add_from_input)
        row.addWidget(self.path_input, 1)

        self.btn_paste = QPushButton("Paste")
        self.btn_paste.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        self.btn_paste.setToolTip("Paste the path from the clipboard (Ctrl+V in the box also works)")
        self.btn_paste.setAccessibleName("Paste path from clipboard")
        self.btn_paste.clicked.connect(self.paste_path)
        row.addWidget(self.btn_paste)

        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.btn_browse.setToolTip("Choose video or audio files (Ctrl+O)")
        self.btn_browse.clicked.connect(self.browse_files)
        row.addWidget(self.btn_browse)

        self.btn_add = QPushButton("Add")
        self.btn_add.setObjectName("Primary")
        self.btn_add.setToolTip("Queue the path in the box (Enter)")
        self.btn_add.clicked.connect(self.add_from_input)
        row.addWidget(self.btn_add)
        source.add(row)
        layout.addWidget(source)

        # --- queue card ---
        queue = Card("Queue")
        actions = QHBoxLayout()
        actions.setSpacing(SPACE["sm"])

        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_start.setToolTip("Transcribe everything queued (F5)")
        self.btn_start.clicked.connect(self.start_batch)
        actions.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_cancel.setToolTip("Stop after the file being processed (Esc)")
        self.btn_cancel.clicked.connect(self.cancel_batch)
        actions.addWidget(self.btn_cancel)

        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setToolTip("Remove the selected rows (Del)")
        self.btn_remove.clicked.connect(self.remove_selected)
        actions.addWidget(self.btn_remove)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Empty the queue")
        self.btn_clear.clicked.connect(self.clear_queue)
        actions.addWidget(self.btn_clear)

        actions.addStretch(1)

        self.btn_output = QPushButton("Open output")
        self.btn_output.setObjectName("Quiet")
        self.btn_output.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.btn_output.setToolTip("Reveal the transcripts in Explorer (Ctrl+E)")
        self.btn_output.clicked.connect(self.open_output)
        actions.addWidget(self.btn_output)
        queue.add(actions)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(2, self.status_delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("Transcription queue")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.empty_state = EmptyState()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_state)
        self.stack.setMinimumHeight(220)
        queue.add(self.stack)
        layout.addWidget(queue, 1)
        return panel

    def _build_right(self) -> QWidget:
        # Two cards side by side, not stacked. The settings column's problem is
        # height, not width - a maximised window has width to spare - so pairing
        # Quality with Output roughly halves the column and is what lets every
        # option be on screen without a scrollbar.
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, SPACE["sm"], 0)
        layout.setSpacing(SPACE["card"])

        quality = Card("Quality")
        self.preset_combo = QComboBox()
        for label, _model, _beam in PRESETS:
            self.preset_combo.addItem(label)
        self.preset_combo.setAccessibleName("Quality preset")
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)

        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        self.model_combo.setAccessibleName("Whisper model")
        self.model_combo.currentTextChanged.connect(self._model_changed)

        self.language_combo = QComboBox()
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)
        self.language_combo.setAccessibleName("Spoken language")

        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic (GPU if available)", "auto")
        self.device_combo.addItem("CUDA (NVIDIA GPU)", "cuda")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.setAccessibleName("Compute device")

        self.jobs_combo = QComboBox()
        for count in PARALLEL_JOB_CHOICES:
            self.jobs_combo.addItem(f"{count} file" + ("s" if count > 1 else ""), count)
        self.jobs_combo.setAccessibleName("Files transcribed at once")
        self.jobs_combo.currentIndexChanged.connect(self._jobs_changed)
        self.jobs_combo.setToolTip(
            "How many files to transcribe at the same time.\n\n"
            "They share the one loaded model, so the extra cost is the per-job\n"
            "CUDA workspace (~1 GB each) on top of the model's own ~3.8 GB for\n"
            "large-v3. Leave it at 1 unless the GPU chip shows free headroom."
        )

        # A 2-column grid of caption-over-control fields, in two rows: 5 controls
        # plus the buttons have to fit a maximised screen without a scrollbar, and
        # every saved row here is a row the footer and log get to keep.
        select_grid = QGridLayout()
        select_grid.setHorizontalSpacing(SPACE["sm"])
        select_grid.setVerticalSpacing(SPACE["gap"])
        for column in range(2):
            select_grid.setColumnStretch(column, 1)
        select_grid.addWidget(elbow_field("Preset", self.preset_combo), 0, 0)
        select_grid.addWidget(elbow_field("Model", self.model_combo), 0, 1)
        select_grid.addWidget(elbow_field("Language", self.language_combo), 1, 0)
        select_grid.addWidget(elbow_field("Device", self.device_combo), 1, 1)
        select_grid.addWidget(elbow_field("Transcribe at once", self.jobs_combo), 2, 0)
        select_holder = QWidget()
        select_holder.setLayout(select_grid)
        quality.add(select_holder)

        # Buttons share one row: stacked, they cost a full extra row of height that
        # the settings column does not have to spare on a maximised screen.
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACE["sm"])

        self.btn_advanced = QPushButton("Advanced settings…")
        self.btn_advanced.clicked.connect(self.edit_advanced)
        button_row.addWidget(self.btn_advanced)

        self.btn_unload = QPushButton("Unload model from memory")
        self.btn_unload.setToolTip(
            "Free the Whisper model and the VRAM it holds.\n"
            "The next run loads it again (a few seconds; longer only if it has to\n"
            "be downloaded first). Disabled while a file is being transcribed."
        )
        self.btn_unload.setAccessibleName("Unload model from memory")
        self.btn_unload.clicked.connect(self.unload_model)
        button_row.addWidget(self.btn_unload)
        quality.add(button_row)
        layout.addWidget(quality)

        output = Card("Output")
        self.format_boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE["sm"])
        grid.setVerticalSpacing(SPACE["sm"] - 4)
        for index, fmt in enumerate(FORMATS):
            box = QCheckBox(fmt)
            box.setChecked(fmt in ("srt", "txt", "json"))
            box.setAccessibleName(f".{fmt} output")
            self.format_boxes[fmt] = box
            grid.addWidget(box, 0, index)  # one compact row
        holder = QWidget()
        holder.setLayout(grid)
        output.add(holder)

        self.radio_same = QRadioButton("Next to each video")
        self.radio_custom = QRadioButton("Custom folder")
        group = QButtonGroup(self)
        group.addButton(self.radio_same)
        group.addButton(self.radio_custom)
        self.radio_same.setChecked(True)
        output.add(self.radio_same)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(SPACE["sm"])
        self.radio_custom.toggled.connect(self._destination_changed)
        folder_row.addWidget(self.radio_custom)
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("output folder")
        self.folder_input.setAccessibleName("Custom output folder")
        self.folder_input.setEnabled(False)
        folder_row.addWidget(self.folder_input, 1)
        self.btn_folder = QPushButton("…")
        self.btn_folder.setFixedWidth(36)
        self.btn_folder.setEnabled(False)
        self.btn_folder.setToolTip("Choose the output folder")
        self.btn_folder.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.btn_folder)
        output.add(folder_row)

        self.check_overwrite = QCheckBox("Overwrite existing transcripts")
        output.add(self.check_overwrite)

        # Quality and Output sit side by side: together they are the tallest pair
        # in the column, and pairing them is what buys the vertical room for the
        # footer and the log without a scrollbar.
        pair = QWidget()
        pair_layout = QHBoxLayout(pair)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setSpacing(SPACE["card"])
        pair_layout.addWidget(quality, 1)
        pair_layout.addWidget(output, 1)
        layout.addWidget(pair, 0)

        self.watch = self._build_watch_card()
        layout.addWidget(self.watch, 0)

        activity = Card("Activity")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(MAX_LOG_LINES)
        self.log.setAccessibleName("Activity log")
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        font.setPointSizeF(max(8.0, QApplication.font().pointSizeF() - 0.5))
        self.log.setFont(font)
        self.log.setMinimumHeight(72)
        activity.add(self.log)

        # No scroll container: the window opens maximised and the whole column is
        # laid out to fit. A scroll area here was the bug - the settings were
        # taller than the viewport, so the bottom card needed a scroll that was
        # easy to miss. The log takes any slack, so the options never absorb it.
        layout.addWidget(activity, 1)
        self.settings_panel = pair
        self.activity_card = activity
        return panel

    def _build_watch_card(self) -> WatchCard:
        """Folder monitoring, built and owned by its own module."""
        card = WatchCard(self)
        card.file_ready.connect(self.add_watched_file)
        card.message.connect(self._on_watch_message)
        card.changed.connect(self._schedule_save)
        return card

    def _build_footer(self) -> Footer:
        """The footer is its own widget; the window just uses its parts."""
        self.footer = Footer(self)
        # Re-published on the window because the progress code addresses them by
        # name throughout (and the tests do too). One line beats threading a
        # footer object through every handler.
        for name in (
            "stage_label",
            "file_progress",
            "file_pct",
            "overall_progress",
            "queue_pct",
            "meta_label",
        ):
            setattr(self, name, getattr(self.footer, name))
        return self.footer

    def _build_menus(self) -> None:
        def act(text: str, shortcut: str | QKeySequence | None, slot, tip: str = "") -> QAction:
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(shortcut)
            if tip:
                action.setStatusTip(tip)
            action.triggered.connect(slot)
            return action

        file_menu = self.menuBar().addMenu("&File")
        self.act_add_files = act("Add &files…", "Ctrl+O", self.browse_files, "Queue video files")
        self.act_add_folder = act("Add f&older…", "Ctrl+Shift+O", self.browse_folder, "Queue a folder")
        file_menu.addAction(self.act_add_files)
        file_menu.addAction(self.act_add_folder)
        file_menu.addSeparator()
        self.act_open_output = act("&Open output folder", "Ctrl+E", self.open_output)
        file_menu.addAction(self.act_open_output)
        file_menu.addSeparator()
        file_menu.addAction(act("&Quit", "Ctrl+Q", self.close))

        queue_menu = self.menuBar().addMenu("&Queue")
        self.act_start = act("&Start", "F5", self.start_batch, "Transcribe the queue")
        self.act_cancel = act("&Cancel", "Esc", self.cancel_batch)
        self.act_remove = act("&Remove selected", "Del", self.remove_selected)
        self.act_clear = act("C&lear", "Ctrl+Shift+Del", self.clear_queue)
        for action in (self.act_start, self.act_cancel, self.act_remove, self.act_clear):
            queue_menu.addAction(action)

        view_menu = self.menuBar().addMenu("&View")
        self.act_dark = QAction("&Dark theme", self, checkable=True)
        self.act_light = QAction("&Light theme", self, checkable=True)
        self.act_dark.triggered.connect(lambda: self.apply_theme("dark"))
        self.act_light.triggered.connect(lambda: self.apply_theme("light"))
        view_menu.addAction(self.act_dark)
        view_menu.addAction(self.act_light)
        view_menu.addSeparator()
        view_menu.addAction(act("Clear &log", "Ctrl+L", self.log.clear))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(act("&Quick start", "F1", self.show_help))
        help_menu.addAction(act("&About", None, self.show_about))

    # ------------------------------------------------------------------ theme

    def apply_theme(self, name: str) -> None:
        palette = THEMES.get(name, DARK)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(palette))
        self._calibrate_footer()
        self.status_delegate.set_palette(palette)
        self.mark.setPixmap(make_app_icon(palette).pixmap(QSize(34, 34)))
        self.setWindowIcon(make_app_icon(palette))
        self.act_dark.setChecked(name == "dark")
        self.act_light.setChecked(name == "light")
        self.settings.setValue("theme", name)
        self.table.viewport().update()

    def _calibrate_footer(self) -> None:
        """Pin the footer to its worst-case text; see vtgui/footer.py."""
        self.footer.calibrate()

    # ------------------------------------------------------------------ audit

    def _audit_environment(self) -> None:
        self.append_log("videotranscript ready", level="info")
        if not core.require_ffmpeg():
            self.chip_device.setText("ffmpeg missing")
            self.chip_device.setObjectName("Chip")
            self.append_log("ffmpeg is not on PATH - decoding will be degraded", level="warning")
        else:
            self.append_log("ffmpeg found on PATH", level="info")

        try:
            import ctranslate2

            count = ctranslate2.get_cuda_device_count()
        except Exception:
            count = 0

        name = "CPU only"
        if count:
            try:
                probe = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                    **core.quiet_subprocess(),
                )
                first = probe.stdout.strip().splitlines()
                name = first[0].strip() if first else "CUDA GPU"
            except (OSError, subprocess.SubprocessError):
                name = "CUDA GPU"
            self.append_log(f"CUDA ready - {name}", level="success")
        else:
            self.append_log("No CUDA device detected - runs will use the CPU", level="warning")

        self.chip_device.setText(name)
        self.chip_device.setProperty("accent", bool(count))
        self.chip_device.style().unpolish(self.chip_device)
        self.chip_device.style().polish(self.chip_device)
        self._refresh_chips()

    def _refresh_chips(self) -> None:
        cached, limit = core.model_cache_state()
        self.chip_models.setText(f"model in memory: {cached}/{limit}" if cached else "model: not loaded")

    # ------------------------------------------------------------------- queue

    def add_from_input(self) -> None:
        self.add_paths(self.path_input.text())

    def add_paths(self, text: str) -> None:
        raw = core.parse_path_list(text)
        if not raw:
            return
        found = core.expand_inputs(raw)
        if not found:
            self.warn("Nothing to add", "No media file was found at that path.")
            self.append_log(f"nothing usable in: {text[:120]}", level="warning")
            return
        added = self.model.add_paths(found)
        if added:
            self._last_browse_dir = str(found[0].parent)
            self.path_input.clear()
            self._schedule_save()
            self.append_log(f"queued {added} file(s)", level="info")
            self.statusBar().showMessage(f"Queued {added} file(s)", 4000)
        else:
            self.statusBar().showMessage("Already queued", 3000)
        self._mark_existing(found)
        self._refresh_actions()
        if self.is_running:
            # The batch is live and pulls from the queue after every file, so these
            # rows are picked up on their own - starting a second worker would only
            # put two runs on one GPU.
            self.statusBar().showMessage(
                f"{added} file(s) added - they run when the current file finishes", 5000
            )
        elif added and self.auto_start:
            self.start_batch()

    def _mark_existing(self, paths: list[Path]) -> int:
        """Flag queued files whose transcripts are already on disk.

        Done the moment a path is added, not when the run starts, so the answer is
        on screen before the GPU does any work. Detection happens once here *and*
        once more inside the pipeline: this pass is the notification, the pipeline
        pass is the guarantee (a transcript can appear while the queue waits).
        """
        if self.check_overwrite.isChecked():
            return 0
        cfg = self.build_config()
        already: list[str] = []
        first_output: Path | None = None
        for path in paths:
            row = self.model.row_of(path)
            if row < 0 or self.model.items[row].status in ("Done", "Running"):
                continue
            found = core.existing_transcripts(path, cfg)
            if not found:
                continue
            outputs = [str(p) for p in found]
            first_output = first_output or found[0]
            self.model.update_item(
                row,
                status="Skipped",
                result=f"already transcribed · {len(outputs)} file(s)",
                outputs=outputs,
                note="Transcript already exists - re-run by removing the row and adding it again.",
            )
            already.append(path.name)
            self.append_log(f"{path.name}: transcript already exists - skipped", level="warning")
            for existing in outputs:
                self.append_log(f"  found {existing}", level="muted")
        if already:
            self._last_outdir = first_output.parent if first_output else None
            # A status message rather than a modal: this fires from automatic
            # auto-start too, and a dialog there would interrupt for something the
            # user did not ask to be interrupted about.
            self.statusBar().showMessage(f"{len(already)} file(s) skipped - transcripts already exist", 8000)
        return len(already)

    def browse_files(self) -> None:
        patterns = (
            "Media files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.flac);;All files (*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add video or audio files", self._last_browse_dir, patterns
        )
        if paths:
            self.add_paths(" ".join(f'"{p}"' for p in paths))

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of media", self._last_browse_dir)
        if folder:
            self.add_paths(f'"{folder}"')

    def paste_path(self) -> None:
        text = read_clipboard()
        if not text:
            self.warn("Clipboard empty", "Nothing readable on the clipboard - paste with Ctrl+V instead.")
            return
        self.path_input.setText(text)
        self.add_paths(text)

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows:
            self.statusBar().showMessage("Select a row to remove", 3000)
            return
        self.model.remove_rows(rows)
        self._refresh_actions()

    def clear_queue(self) -> None:
        if self.is_running:
            self.statusBar().showMessage("Cancel the run before clearing the queue", 4000)
            return
        self.model.clear()
        self._refresh_actions()

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the output folder", self.folder_input.text() or self._last_browse_dir
        )
        if folder:
            self.folder_input.setText(folder)

    def _destination_changed(self) -> None:
        custom = self.radio_custom.isChecked()
        self.folder_input.setEnabled(custom)
        self.btn_folder.setEnabled(custom)

    # ------------------------------------------------------------------ watch

    def add_watched_file(self, path: Path) -> None:
        """A file finished arriving: queue it and let the normal flow run it.

        Reuses `add_paths` on purpose, so a watched file behaves exactly like a
        pasted one — same duplicate check, same destination, same auto-start, and
        the same "transcript already exists" skip. The watch card owns the polling
        and the controls; deciding what to do with a ready file is the window's job.
        """
        before = self.model.rowCount()
        self.statusBar().showMessage(f"New file detected: {Path(path).name}", 5000)
        self.add_paths(f'"{path}"')
        added = self.model.rowCount() > before
        self.watch.note_queued(added)
        if added:
            self.append_log(f"watched folder: queued {Path(path).name}", level="info")
        else:
            # add_paths already said why (duplicate, or transcript already there);
            # counting it as queued would make the status line lie.
            self.append_log(f"watched folder: {Path(path).name} was already handled", level="muted")

    def _on_watch_message(self, level: str, text: str) -> None:
        self.append_log(text, level=level)

    def _restore_watch(self) -> None:
        """Hand the remembered monitoring settings back to the card."""
        self.watch.restore(
            self.settings.value("watch_folder", "", str),
            int(self.settings.value("watch_settle", DEFAULT_SETTLE_SECONDS, int)),
            self.settings.value("watch_active", False, bool),
        )

    def _remember_watch(self) -> None:
        folder, settle, active = self.watch.state()
        self.settings.setValue("watch_folder", folder)
        self.settings.setValue("watch_settle", settle)
        self.settings.setValue("watch_active", active)

    # ------------------------------------------------------------------ config

    def build_config(self) -> core.Config:
        formats = [fmt for fmt, box in self.format_boxes.items() if box.isChecked()] or ["srt"]
        custom = self.radio_custom.isChecked()
        folder = self.folder_input.text().strip()
        # "fixes" is the dialog's text blob; core.Config wants a parsed mapping.
        options = dict(self.advanced)
        fixes = options.pop("fixes", "")
        return core.Config(
            model=self.model_combo.currentText(),
            language=self.language_combo.currentData(),
            device=self.device_combo.currentData(),
            formats=formats,
            output_dir=folder if custom and folder else None,
            overwrite=self.check_overwrite.isChecked(),
            quiet=True,
            parallel_jobs=self.jobs_combo.currentData(),
            corrections=core.parse_corrections(fixes),
            **options,
        )

    def _preset_changed(self, index: int) -> None:
        if getattr(self, "_syncing", False):
            return
        label, model, beam = PRESETS[index]
        if not model:
            return
        self._syncing = True
        self.model_combo.setCurrentText(model)
        self._syncing = False
        self.advanced["beam_size"] = beam
        self.append_log(f"preset {label.lower()} - {model}, beam {beam}", level="info")

    def _model_changed(self, _text: str) -> None:
        if getattr(self, "_syncing", False):
            return
        self._syncing = True
        self.preset_combo.setCurrentIndex(len(PRESETS) - 1)  # Custom
        self._syncing = False

    def _jobs_changed(self, _index: int) -> None:
        """Say it out loud: parallel jobs are a VRAM decision, not a free dial."""
        count = self.jobs_combo.currentData()
        if count in (None, 1):
            return
        self.append_log(
            f"{count} files will run at once - each adds its own CUDA workspace on top of the loaded model",
            level="info",
        )

    def edit_advanced(self) -> None:
        dialog = AdvancedDialog(self.advanced, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.advanced.update(dialog.values())
            vocabulary = core.parse_corrections(self.advanced.get("fixes"))
            extras = ""
            if self.advanced.get("hotwords"):
                extras = f", vocab {len(self.advanced['hotwords'].split(','))} term(s)"
            if vocabulary:
                extras += f", {len(vocabulary)} replacement(s)"
            self.append_log(
                f"advanced: beam {self.advanced['beam_size']}, "
                f"line <= {self.advanced['max_line_chars']} chars, "
                f"VAD {'off' if self.advanced['no_vad'] else 'on'}{extras}",
                level="info",
            )

    # ------------------------------------------------------------------ running

    @property
    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    @property
    def auto_start(self) -> bool:
        return self.settings.value("auto_start", True, bool)

    def start_batch(self) -> None:
        if self.is_running:
            self.statusBar().showMessage("Already running", 3000)
            return

        # Only rows that have not already succeeded. Re-sending a finished file
        # would re-transcribe it for no reason - and it happens easily, because
        # adding a file auto-starts the queue. Claiming also flips the rows to
        # Running, so the pull the worker does later cannot grab them twice.
        files = self.model.claim_pending()
        if not files:
            if self.model.rowCount():
                self.warn(
                    "Nothing to do",
                    "Every queued file is already transcribed.\n\n"
                    "To run one again, select it and press Remove, then add it again.",
                )
            else:
                self.warn("Queue is empty", "Add a video first - paste a path or use Browse.")
                self.path_input.setFocus()
            return

        self._total_files = len(files)
        self._done_files = 0
        self.file_progress.setValue(0)
        self.set_queue_progress(0)
        jobs = self.jobs_combo.currentData() or 1
        at_once = "" if jobs == 1 else f", {jobs} at a time"
        self.append_log(
            f"starting {len(files)} file(s) on {self.device_combo.currentText()}{at_once}",
            level="info",
        )
        # The queue advances itself: after every file the core asks for more work,
        # so anything added mid-run starts the moment the GPU is free. The model
        # loaded for the first file therefore serves the whole session.
        self.worker = TranscribeWorker(files, self.build_config(), self, next_files=self.model.claim_pending)
        self.worker.event.connect(self.on_pipeline_event)
        self.worker.finished.connect(self._refresh_actions)
        self.worker.start()
        self._refresh_actions()

    def cancel_batch(self) -> None:
        if not self.is_running or self.worker is None:
            return
        self.worker.cancel()
        self.append_log("cancelling after the current file…", level="warning")
        self.statusBar().showMessage("Cancelling after the current file…", 5000)
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        running = self.is_running
        empty = self.model.rowCount() == 0
        self.btn_start.setEnabled(not running and not empty)
        self.btn_cancel.setEnabled(running)
        self.btn_remove.setEnabled(not running and not empty)
        self.btn_clear.setEnabled(not running and not empty)
        self.btn_add.setEnabled(True)
        # Unloading mid-file would pull the model out from under the worker.
        resident, _limit = core.model_cache_state()
        self.btn_unload.setEnabled(resident > 0 and not running)
        self.act_start.setEnabled(not running and not empty)
        self.act_cancel.setEnabled(running)
        self.stack.setCurrentWidget(self.empty_state if empty else self.table)
        self._refresh_chips()

    def unload_model(self) -> None:
        """Free the resident model now: the button, and the last step of closing."""
        freed = core.unload_model()
        if freed:
            self.append_log(f"unloaded {freed} model(s) - GPU memory freed", level="info")
            self.statusBar().showMessage("Model unloaded", 4000)
        else:
            self.statusBar().showMessage("No model was loaded", 3000)
        self._refresh_actions()

    # ------------------------------------------------------------- event pump

    # -- progress helpers ----------------------------------------------------

    # ------------------------------------------------------------------- output

    def open_output(self) -> None:
        target = self._last_outdir
        if target is None:
            folder = self.folder_input.text().strip()
            if folder:
                target = Path(folder)
            elif self.model.items:
                target = self.model.items[0].path.parent
        if target is None or not Path(target).exists():
            self.statusBar().showMessage("No output folder yet", 4000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # --------------------------------------------------------------------- misc

    def append_log(self, text: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        marker = {"error": "!", "warning": "!", "success": "+", "muted": " "}.get(level, "·")
        self.log.appendPlainText(f"{stamp}  {marker} {text}")

    def warn(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "Quick start",
            "<b>1. Add a video</b><br>"
            "Paste a path into the box and press Enter, or use <b>Browse…</b>.<br><br>"
            "<b>2. Press Start</b> (F5). Progress and results appear in the queue.<br><br>"
            "<b>Shortcuts</b><br>"
            "Ctrl+O add files &nbsp;·&nbsp; Ctrl+Shift+O add folder &nbsp;·&nbsp; F5 start<br>"
            "Esc cancel &nbsp;·&nbsp; Del remove &nbsp;·&nbsp; Ctrl+E open output &nbsp;·&nbsp; "
            "Ctrl+L clear log &nbsp;·&nbsp; F1 help<br><br>"
            "Transcripts are written next to each video as .srt + .txt + .json unless you "
            "choose a custom folder.",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About videotranscript",
            "<b>videotranscript</b><br>"
            "Local, GPU-accelerated transcription with word-level timestamps.<br><br>"
            "Engine: faster-whisper (CTranslate2) on CUDA.<br>"
            "No cloud services, no API keys, no uploads.",
        )

    # ---------------------------------------------------------------- settings

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("splitter")
        if splitter_state:
            self.splitter.restoreState(splitter_state)

        self.model_combo.setCurrentText(self.settings.value("model", "large-v3", str))
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(self.settings.value("language", "auto", str)))
        )
        device = self.settings.value("device", "auto", str)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(device)))
        jobs = int(self.settings.value("parallel_jobs", 1, int))
        self.jobs_combo.setCurrentIndex(max(0, self.jobs_combo.findData(jobs)))

        formats = json.loads(self.settings.value("formats", '["srt","txt","json"]', str))
        for fmt, box in self.format_boxes.items():
            box.setChecked(fmt in formats)

        self.radio_custom.setChecked(self.settings.value("custom_folder", False, bool))
        self.folder_input.setText(self.settings.value("folder", "", str))
        self._destination_changed()
        self.check_overwrite.setChecked(self.settings.value("overwrite", False, bool))

        stored = self.settings.value("advanced", "", str)
        if stored:
            with contextlib.suppress(ValueError, TypeError):
                self.advanced.update(json.loads(stored))

        self._syncing = True
        preset = self.settings.value("preset", "Best quality", str)
        index = self.preset_combo.findText(preset)
        self.preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self._syncing = False

        # The remembered thing is the *folder* Browse should open in, not the last
        # filename: the source box starts empty so it is never re-queued by accident.
        self._last_browse_dir = self.settings.value("last_browse_dir", "", str)
        self.path_input.clear()

        self._restore_watch()

    def _save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.settings.setValue("model", self.model_combo.currentText())
        self.settings.setValue("language", self.language_combo.currentData())
        self.settings.setValue("device", self.device_combo.currentData())
        self.settings.setValue("parallel_jobs", self.jobs_combo.currentData())
        chosen = [fmt for fmt, box in self.format_boxes.items() if box.isChecked()]
        self.settings.setValue("formats", json.dumps(chosen))
        self.settings.setValue("custom_folder", self.radio_custom.isChecked())
        self.settings.setValue("folder", self.folder_input.text())
        self.settings.setValue("overwrite", self.check_overwrite.isChecked())
        self.settings.setValue("advanced", json.dumps(self.advanced))
        self.settings.setValue("preset", self.preset_combo.currentText())
        # Only the browse folder is remembered; no filename is carried over.
        self.settings.setValue("last_browse_dir", self._last_browse_dir)
        # The watched folder is deliberately remembered: reopening the app should
        # resume monitoring the folder the user already chose.
        self._remember_watch()
        self.settings.remove("last_path")  # retired: it re-queued the previous file
        self.settings.sync()

    def _schedule_save(self) -> None:
        """Persist soon after a change, so a crash or a kill cannot lose settings."""
        self._save_timer.start()

    def _connect_persistence(self) -> None:
        """Every option change schedules a save; queued paths are remembered."""
        for combo in (self.model_combo, self.language_combo, self.device_combo, self.jobs_combo):
            combo.currentIndexChanged.connect(self._schedule_save)
        for box in self.format_boxes.values():
            box.toggled.connect(self._schedule_save)
        self.radio_same.toggled.connect(self._schedule_save)
        self.radio_custom.toggled.connect(self._schedule_save)
        self.folder_input.textChanged.connect(self._schedule_save)
        self.check_overwrite.toggled.connect(self._schedule_save)
        self.preset_combo.currentIndexChanged.connect(self._schedule_save)
        self.path_input.textChanged.connect(self._schedule_save)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if self.watch is not None:
            self.watch.shutdown()
        # Only a real background thread can still be mid-transcription; anything
        # else (including a stand-in used by tests) must never raise a modal here,
        # because that would block a headless close forever.
        worker = self.worker
        worker_wedged = False
        if isinstance(worker, QThread) and worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Transcription running",
                "A file is still being processed. Stop and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            worker.cancel()
            # Cancellation is only polled between files, so a long file keeps the
            # worker inside the model for a while yet.
            worker_wedged = not worker.wait(5000)
        self._save_settings()
        if worker_wedged:
            # Unloading now would pull the model out from under the running thread.
            # The process is on its way out anyway, and the driver reclaims the
            # VRAM when it dies, so leave the release to teardown.
            self.append_log(
                "still finishing the current file - leaving the model to process teardown",
                level="warning",
            )
        else:
            # Release the model explicitly instead of relying on interpreter
            # shutdown: a lingering reference would otherwise keep gigabytes of
            # VRAM pinned for as long as the process lived. (A killed process needs
            # no help - the driver reclaims the memory when the process dies.)
            core.unload_model()
        super().closeEvent(event)
