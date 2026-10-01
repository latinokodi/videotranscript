"""Static choices the UI offers: presets, models, languages, formats, defaults."""

from __future__ import annotations

PRESETS: list[tuple[str, str, int]] = [
    ("Best quality", "large-v3", 5),
    ("Balanced", "large-v3-turbo", 5),
    ("Fast", "large-v3-turbo", 1),
    ("Draft", "tiny", 1),
    ("Custom", "", 0),
]


MODELS = [
    "large-v3",
    "large-v3-turbo",
    "distil-large-v3",
    "large-v2",
    "medium",
    "medium.en",
    "small",
    "small.en",
    "base",
    "tiny",
]


LANGUAGES = [
    ("Detect automatically", "auto"),
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Dutch", "nl"),
    ("Russian", "ru"),
    ("Polish", "pl"),
    ("Turkish", "tr"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
    ("Chinese", "zh"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Ukrainian", "uk"),
]


FORMATS = ["srt", "vtt", "txt", "json", "tsv"]


#: How many files the app will transcribe at the same time. A short list because
#: this is a VRAM budget rather than a free speed dial: every extra job needs its
#: own CUDA workspace, and past 3 a 12 GB card starts to run out with `large-v3`.
PARALLEL_JOB_CHOICES = (1, 2, 3)


DEFAULT_ADVANCED = {
    "beam_size": 5,
    "max_line_chars": 42,
    "max_duration": 7.0,
    "gap_break": 0.8,
    "initial_prompt": None,
    # Custom vocabulary: fed to Whisper as hotwords so names and jargon come out
    # spelled the way the speaker says them (the biggest accuracy win we measured).
    "hotwords": None,
    # "wrong=right" lines, parsed by core.parse_corrections when a run starts.
    "fixes": "",
    "normalise_text": True,
    "no_vad": False,
    "no_context": False,
    "loop_guard": True,
    "repetition_penalty": 1.1,
    "no_repeat_ngram_size": 4,
    "hallucination_silence_threshold": None,
}
