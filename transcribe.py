#!/usr/bin/env python3
"""
transcribe.py — turn the audio of a video (or audio) file into a transcript with
accurate timestamps, 100% locally, GPU-accelerated.

Pipeline:  ffmpeg -> 16 kHz mono WAV -> faster-whisper (CTranslate2, CUDA)
           -> word-level timestamps -> subtitle-grade cues -> .srt/.vtt/.txt/.json

Examples
--------
  python transcribe.py video.mp4
  python transcribe.py "D:\\videos\\*.mkv" --model large-v3
  python transcribe.py talk.mp4 --language en --formats srt,txt,json
  python transcribe.py interview.mp4 --language auto --max-line-chars 38

Run `python transcribe.py --help` for all options.

The implementation lives in the `vtcore` package; this module is the stable
facade that the GUI, the tests and the command line import, so the split into
modules stays invisible to callers.
"""

from __future__ import annotations

from vtcore import *  # noqa: F403 - re-export the documented core surface

# The CLI lives in its own module so library users never import argparse; it is
# re-exported here because the tests and the launcher drive the facade.
from vtcore.cli import (  # noqa: F401 - re-exported facade API
    build_parser,
    config_from_args,
    main,
    parse_temperatures,
)

if __name__ == "__main__":
    raise SystemExit(main())
