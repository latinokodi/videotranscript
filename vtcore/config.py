"""The single options object shared by the GUI and the CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .runtime import pick_device, require_ffmpeg

FORMATS = ("srt", "vtt", "txt", "tsv", "json")


DEFAULT_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass
class Config:
    """Every pipeline option in one place, shared by the CLI and the GUI."""

    model: str = "large-v3"
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str | None = None
    language: str = "auto"
    beam_size: int = 5
    # A *list* is Whisper's escape hatch from decoder repetition loops: the first
    # attempt stays at 0.0 (deterministic), and the higher temperatures are only
    # used when compression_ratio/log-prob says a segment came out degenerate.
    # Pinning this to a single 0.0 removes that recovery path entirely.
    temperature: float | list[float] = field(default_factory=lambda: list(DEFAULT_TEMPERATURES))
    initial_prompt: str | None = None
    # Custom vocabulary: names and jargon handed to Whisper as "hotwords" so the
    # decoder prefers these spellings. Measured on a real interview, adding the
    # guest's name and the terms he uses fixed 4 of 5 proper-noun errors for
    # about 10% extra decode time - the single biggest accuracy win available.
    hotwords: str | None = None
    # Deterministic post-decode replacements for whatever biasing did not catch,
    # e.g. {"Balanchine": "balance sheet"}. Word-boundary, case-insensitive.
    corrections: dict[str, str] = field(default_factory=dict)
    # Tidy the spacing artifacts Whisper emits around numbers ("75 %", "5 ,000")
    # and hyphenated terms ("Euro -Yen"). Set False to keep the raw decode.
    normalise_text: bool = True
    no_vad: bool = False
    vad_min_silence: int = 500
    no_context: bool = False
    loop_guard: bool = True
    # Decode-time loop suppression. 1.0 is "off"; a little penalty makes the
    # decoder pay for reusing tokens, and an n-gram block forbids repeating a
    # word sequence outright. Together these stop most loops before they form,
    # which is far better than repairing the transcript afterwards.
    repetition_penalty: float = 1.1
    no_repeat_ngram_size: int = 4
    # Skip silence that surrounds a suspected hallucination (seconds).
    hallucination_silence_threshold: float | None = None

    max_cue_chars: int = 84
    max_line_chars: int = 42
    min_cue_chars: int = 20
    max_duration: float = 7.0
    gap_break: float = 0.8

    formats: list[str] = field(default_factory=lambda: ["srt", "txt", "json"])
    output_dir: str | None = None
    overwrite: bool = False
    quiet: bool = False

    # How many files to transcribe at once. 1 is the default because it is the
    # predictable choice everywhere; raising it trades VRAM for overlap, since
    # each extra job needs its own CUDA workspace (and its own 16 kHz temp WAV)
    # on top of the shared model.
    parallel_jobs: int = 1

    # resolved at runtime by resolve_runtime()
    ffmpeg: str | None = None
    resolved_device: str = ""
    resolved_compute: str = ""

    def resolve_runtime(self) -> None:
        self.ffmpeg = require_ffmpeg()
        device, compute = pick_device(self.device)
        self.resolved_device = device
        self.resolved_compute = self.compute_type or compute
        # Guard the pool size here rather than trusting the caller: a 0 would hang
        # the batch and a negative would raise deep inside ThreadPoolExecutor.
        self.parallel_jobs = max(1, int(self.parallel_jobs))


EventFn = Callable[[str, dict], None]
