"""Turning input paths into 16 kHz mono audio via ffmpeg."""

from __future__ import annotations

import contextlib
import gc
import glob as globmod
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .errors import FFmpegError
from .runtime import quiet_subprocess
from .text import log

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff"}


VIDEO_EXTS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".ts",
    ".3gp",
}


MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS


def expand_inputs(patterns: list[str]) -> list[Path]:
    """Expand files, globs and directories into a sorted list of media files."""
    found: list[Path] = []
    for raw in patterns:
        p = Path(raw)
        if p.is_dir():
            found.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in MEDIA_EXTS))
        elif any(ch in raw for ch in "*?["):
            found.extend(
                sorted(
                    # glob.glob (not Path.glob) so absolute patterns like D:\v\*.mkv work.
                    Path(m)
                    for m in globmod.glob(raw, recursive=True)  # noqa: PTH207
                    if Path(m).is_file()
                )
            )
        elif p.is_file():
            found.append(p)
        else:
            log(f"! skipping (not found): {raw}")
    # de-duplicate, keep order
    seen: set[Path] = set()
    out: list[Path] = []
    for f in found:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(f)
    return out


_PATH_TOKEN = re.compile(r'"([^"]+)"|\'([^\']+)\'|(\S+)')


def parse_path_list(text: str) -> list[str]:
    """Split pasted text into individual paths, honouring quotes.

    A pasted list may arrive as `"C:\\a b\\one.mp4" "C:\\a b\\two.mp4"` or one
    path per line, so quoting has to be respected rather than naively splitting
    on whitespace. Lives in the core (not the UI) because it is pure text handling.
    """
    paths: list[str] = []
    for match in _PATH_TOKEN.finditer(text.strip()):
        value = next((group for group in match.groups() if group), "")
        value = value.strip().strip('"').strip("'")
        if value:
            paths.append(value)
    return paths


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(src: Path, ffmpeg: str | None = None) -> float:
    """Best-effort media duration in seconds; 0.0 when it cannot be determined.

    Needed *before* decoding so the UI can show a real percentage while ffmpeg
    works through a long video.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            check=False,
            **quiet_subprocess(),
        )
        with contextlib.suppress(ValueError):
            value = float((proc.stdout or "").strip())
            if value > 0:
                return value

    probe = ffmpeg or shutil.which("ffmpeg")
    if probe:
        proc = subprocess.run(
            [probe, "-hide_banner", "-i", str(src)],
            capture_output=True,
            text=True,
            check=False,
            **quiet_subprocess(),
        )
        match = _DURATION_RE.search(proc.stderr or "")
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return 0.0


def extract_audio(
    src: Path,
    ffmpeg: str,
    duration: float = 0.0,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Decode any container to 16 kHz mono PCM WAV (what Whisper wants).

    When ``duration`` is known, ffmpeg's ``-progress`` stream is parsed to report
    real decode percentages; stderr goes to a temp file so the pipe can never
    deadlock on a full buffer.
    """
    # mkstemp opens the file; that descriptor must be released immediately or
    # Windows keeps the path locked and nothing can delete it later.
    handle, name = tempfile.mkstemp(suffix=".wav", prefix="vt_")
    os.close(handle)
    tmp = Path(name)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-stats_period",
        "0.3",
        "-nostats",
        "-i",
        str(src),
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(tmp),
    ]

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=err_file,
            text=True,
            errors="replace",
            **quiet_subprocess(),
        )
        if proc.stdout is not None:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"} and duration > 0 and on_progress:
                    with contextlib.suppress(ValueError):
                        on_progress(min(99.0, float(value) / 1_000_000 / duration * 100))
                elif key == "progress" and value == "end" and on_progress:
                    on_progress(100.0)
            proc.stdout.close()
        proc.wait()
        err_file.seek(0)
        stderr_text = err_file.read()

    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 44:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        detail = stderr_text.strip().splitlines()
        detail = detail[-1] if detail else "unknown ffmpeg error"
        raise FFmpegError(f"ffmpeg could not extract audio from {src.name}: {detail}")
    return tmp


def safe_unlink(path: Path, attempts: int = 6, delay: float = 0.25) -> None:
    """Delete a temp file, tolerating Windows' delayed handle release.

    PyAV/ctranslate2 keep the decoded WAV open until the segment generator is
    closed and garbage collected, so an immediate unlink can raise WinError 32.
    The OS temp folder cleans up whatever we cannot remove.
    """
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                return
            gc.collect()
            time.sleep(delay)
        except OSError:
            return
