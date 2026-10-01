"""Core pipeline for local, GPU-accelerated transcription.

Layering (each module imports only from the ones above it):

    errors  ->  text  ->  cues  ->  postprocess  ->  writers
                  ^        ^                          ^
                  |        |                          |
              runtime  ->  config  ->  models  ->  pipeline  ->  cli
                  ^
                  |
                media

Importing this package also performs the one-time runtime setup (UTF-8 streams
and the Windows CUDA DLL path), so callers get a correctly configured
environment just by importing `vtcore`.
"""

from __future__ import annotations

from .config import DEFAULT_TEMPERATURES, FORMATS, Config, EventFn
from .cues import BREAK_PUNCT, MIN_BREAK_CHARS, SENTENCE_END, Cue, Word, build_cues
from .errors import FFmpegError, ModelLoadError, TranscribeError
from .media import (
    AUDIO_EXTS,
    MEDIA_EXTS,
    VIDEO_EXTS,
    expand_inputs,
    extract_audio,
    parse_path_list,
    probe_duration,
    safe_unlink,
)
from .models import (
    ensure_model_downloaded,
    hf_repo_id,
    load_model,
    model_cache_state,
    unload_model,
)
from .pipeline import run_batch, skipped_meta, transcribe_file
from .postprocess import collapse_loops, polish_cues
from .runtime import pick_device, quiet_subprocess, require_ffmpeg
from .text import (
    apply_corrections,
    fmt_timestamp,
    human_time,
    log,
    normalise_text,
    parse_corrections,
    wrap_lines,
)
from .writers import (
    existing_transcripts,
    output_dir_for,
    output_target,
    write_json,
    write_outputs,
    write_srt,
    write_ts_txt,
    write_txt,
    write_vtt,
)

__all__ = [
    "AUDIO_EXTS",
    "BREAK_PUNCT",
    "DEFAULT_TEMPERATURES",
    "FORMATS",
    "MEDIA_EXTS",
    "MIN_BREAK_CHARS",
    "SENTENCE_END",
    "VIDEO_EXTS",
    "Config",
    "Cue",
    "EventFn",
    "FFmpegError",
    "ModelLoadError",
    "TranscribeError",
    "Word",
    "apply_corrections",
    "build_cues",
    "collapse_loops",
    "ensure_model_downloaded",
    "existing_transcripts",
    "expand_inputs",
    "extract_audio",
    "fmt_timestamp",
    "hf_repo_id",
    "human_time",
    "load_model",
    "log",
    "model_cache_state",
    "normalise_text",
    "output_dir_for",
    "output_target",
    "parse_corrections",
    "parse_path_list",
    "pick_device",
    "polish_cues",
    "probe_duration",
    "quiet_subprocess",
    "require_ffmpeg",
    "run_batch",
    "safe_unlink",
    "skipped_meta",
    "transcribe_file",
    "unload_model",
    "wrap_lines",
    "write_json",
    "write_outputs",
    "write_srt",
    "write_ts_txt",
    "write_txt",
    "write_vtt",
]
