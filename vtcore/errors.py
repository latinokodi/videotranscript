"""Exception types shared across the pipeline."""

from __future__ import annotations


class TranscribeError(Exception):
    """Base class for failures that are meaningful to the person running this."""


class FFmpegError(TranscribeError):
    """ffmpeg could not decode an audio track from the input."""


class ModelLoadError(TranscribeError):
    """The Whisper model could not be loaded on any available device."""
