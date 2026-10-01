"""The advanced-settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from .theme import SPACE


class AdvancedDialog(QDialog):
    """Beam search, VAD and subtitle shaping - kept out of the main flow."""

    def __init__(self, current: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current = dict(current)
        self.setWindowTitle("Advanced settings")
        self.setModal(True)
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        form.setSpacing(SPACE["sm"])

        self.beam = QSpinBox()
        self.beam.setRange(1, 10)
        self.beam.setValue(int(current["beam_size"]))
        self.beam.setToolTip("Higher is slower and slightly more accurate. 5 is a good default.")

        self.line_chars = QSpinBox()
        self.line_chars.setRange(20, 80)
        self.line_chars.setValue(int(current["max_line_chars"]))
        self.line_chars.setToolTip("Maximum characters per subtitle line.")

        self.max_seconds = QDoubleSpinBox()
        self.max_seconds.setRange(1.0, 20.0)
        self.max_seconds.setSingleStep(0.5)
        self.max_seconds.setValue(float(current["max_duration"]))
        self.max_seconds.setToolTip("Longest a single subtitle cue may last.")

        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.1, 3.0)
        self.gap.setSingleStep(0.1)
        self.gap.setValue(float(current["gap_break"]))
        self.gap.setToolTip("A silence longer than this starts a new cue.")

        self.prompt = QLineEdit(current.get("initial_prompt") or "")
        self.prompt.setPlaceholderText("e.g. Kubernetes, Postgres, gRPC")

        self.hotwords = QLineEdit(current.get("hotwords") or "")
        self.hotwords.setPlaceholderText("e.g. Bessent, GPIF, Ethena")
        self.hotwords.setToolTip(
            "Names, brands and jargon the transcript must spell correctly.\n"
            "Biases the decoder towards these words, so it hears them instead of\n"
            "a similar-sounding common word (measured: 4 of 5 name errors fixed).\n"
            "Costs roughly 10% more decoding time. Separate with commas."
        )

        self.fixes = QPlainTextEdit(current.get("fixes") or "")
        self.fixes.setPlaceholderText("Balanchine=balance sheet\nBesson=Bessent")
        self.fixes.setFixedHeight(72)
        self.fixes.setToolTip(
            "Correct anything the decoder still gets wrong, one pair per line as\n"
            "wrong=right. Applied word-boundary and case-insensitively after\n"
            "transcription, so it can never damage unrelated words."
        )

        self.tidy_box = QCheckBox("Tidy spacing around numbers and hyphens")
        self.tidy_box.setChecked(bool(current.get("normalise_text", True)))
        self.tidy_box.setToolTip(
            'Whisper writes "75 %", "5 ,000" and "Euro -Yen". Fixing those is safe\n'
            "and keeps subtitles readable. Turn off to keep the raw decode."
        )

        self.vad = QCheckBox("Filter silence (voice activity detection)")
        self.vad.setChecked(not current["no_vad"])
        self.context = QCheckBox("Use previous text as context")
        self.context.setChecked(not current["no_context"])
        self.loop_guard_box = QCheckBox("Drop repeated text from decoder loops")
        self.loop_guard_box.setChecked(current.get("loop_guard", True))
        self.loop_guard_box.setToolTip(
            "Whisper can lock up and repeat one sentence for the rest of a file.\n"
            "Leave this on: looping lines are collapsed and reported in the log."
        )
        self.penalty_box = QCheckBox("Discourage repeated phrases while decoding")
        self.penalty_box.setChecked(float(current.get("repetition_penalty") or 1.0) > 1.0)
        self.penalty_box.setToolTip(
            "Applies a repetition penalty (1.1) during decoding, so a loop is much\n"
            "less likely to form in the first place."
        )
        self.ngram_box = QCheckBox("Block repeated word sequences")
        self.ngram_box.setChecked(int(current.get("no_repeat_ngram_size") or 0) > 0)
        self.ngram_box.setToolTip(
            "Forbids the decoder from repeating any 4-word sequence.\n"
            "Turn this off if your material genuinely repeats phrases verbatim."
        )

        form.addRow("Beam size", self.beam)
        form.addRow("Line width", self.line_chars)
        form.addRow("Max cue (s)", self.max_seconds)
        form.addRow("Pause break (s)", self.gap)
        form.addRow("Names / jargon", self.prompt)
        form.addRow("Spell these right", self.hotwords)
        form.addRow("Fix misheard words", self.fixes)
        form.addRow("", self.tidy_box)
        form.addRow("", self.vad)
        form.addRow("", self.context)
        form.addRow("Loop defences", self.loop_guard_box)
        form.addRow("", self.penalty_box)
        form.addRow("", self.ngram_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "beam_size": self.beam.value(),
            "max_line_chars": self.line_chars.value(),
            "max_duration": self.max_seconds.value(),
            "gap_break": self.gap.value(),
            "initial_prompt": self.prompt.text().strip() or None,
            "hotwords": self.hotwords.text().strip() or None,
            "fixes": self.fixes.toPlainText().strip(),
            "normalise_text": self.tidy_box.isChecked(),
            "no_vad": not self.vad.isChecked(),
            "no_context": not self.context.isChecked(),
            "loop_guard": self.loop_guard_box.isChecked(),
            "repetition_penalty": 1.1 if self.penalty_box.isChecked() else 1.0,
            "no_repeat_ngram_size": 4 if self.ngram_box.isChecked() else 0,
            "hallucination_silence_threshold": self.current.get("hallucination_silence_threshold"),
        }
