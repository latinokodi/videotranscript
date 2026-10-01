"""Interpreter/GPU environment setup: UTF-8 streams and the CUDA runtime path."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

from .text import log


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so progress glyphs never crash a cp1252 console."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _bootstrap_cuda_dlls() -> list[str]:
    if sys.platform != "win32":
        return []
    import sysconfig

    purelib = Path(sysconfig.get_paths()["purelib"])
    added: list[str] = []
    for package in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        for bin_dir in (purelib / "nvidia" / package).glob("bin"):
            if bin_dir.is_dir():
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                with contextlib.suppress(AttributeError, OSError):
                    os.add_dll_directory(str(bin_dir))  # type: ignore[attr-defined]
                added.append(str(bin_dir))
        # some wheels nest one level deeper: nvidia/<pkg>/bin/<arch>/
        for nested in (purelib / "nvidia" / package).glob("*/*"):
            if nested.is_dir() and any(nested.glob("*.dll")):
                os.environ["PATH"] = str(nested) + os.pathsep + os.environ.get("PATH", "")
                with contextlib.suppress(AttributeError, OSError):
                    os.add_dll_directory(str(nested))  # type: ignore[attr-defined]
                added.append(str(nested))
    return added


_CUDA_DIRS = _bootstrap_cuda_dlls()


#: Subprocess flags for anything with a console window that must not be shown.
#: The app runs under ``pythonw`` with no console of its own, so Windows gives
#: every console child — ffmpeg, ffprobe, nvidia-smi — a brand-new console window
#: that flashes on screen. That is what a burst of command windows during a run
#: is: one per spawned tool, not a crash. ``CREATE_NO_WINDOW`` gives the child no
#: console at all, which also stops a stray Ctrl+C or window close reaching it.
#: There is no ``os.set_blocking``-style equivalent on other platforms, so this is
#: Windows-only and a no-op elsewhere.
_NO_WINDOW_FLAG = 0x08000000 if sys.platform == "win32" else 0


def quiet_subprocess() -> dict:
    """Extra ``subprocess`` keyword arguments that keep helper tools invisible."""
    if not _NO_WINDOW_FLAG:
        return {}
    return {"creationflags": _NO_WINDOW_FLAG}


def require_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def pick_device(requested: str) -> tuple[str, str]:
    """Return (device, compute_type) with a graceful CUDA -> CPU fallback."""
    if requested == "cpu":
        return "cpu", "int8"
    if requested == "cuda":
        return "cuda", "float16"
    # auto
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    log("  (no usable CUDA device found - falling back to CPU)")
    return "cpu", "int8"


_force_utf8_streams()
