"""Serialising finished cues to srt/vtt/txt/tsv/json."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .cues import Cue
from .text import fmt_timestamp


def write_srt(cues: list[Cue], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for i, cue in enumerate(cues, 1):
            fh.write(f"{i}\n{fmt_timestamp(cue.start)} --> {fmt_timestamp(cue.end)}\n{cue.text}\n\n")


def write_vtt(cues: list[Cue], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("WEBVTT\n\n")
        for i, cue in enumerate(cues, 1):
            fh.write(
                f"{i}\n{fmt_timestamp(cue.start, '.')} --> {fmt_timestamp(cue.end, '.')}\n{cue.text}\n\n"
            )


def write_txt(cues: list[Cue], path: Path) -> None:
    """Plain reading transcript: one paragraph per cue."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for cue in cues:
            fh.write(cue.flat + "\n")


def write_ts_txt(cues: list[Cue], path: Path) -> None:
    """Timestamped plain text — easy to grep/skim, no subtitle syntax."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for cue in cues:
            fh.write(f"{fmt_timestamp(cue.start, '.')}  {cue.flat}\n")


def write_json(cues: list[Cue], path: Path, meta: dict) -> None:
    payload = {
        "meta": meta,
        "cues": [
            {
                "id": i,
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "text": c.flat,
                "words": [
                    {
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "text": w.text,
                        "probability": round(w.probability, 4),
                    }
                    for w in c.words
                ],
            }
            for i, c in enumerate(cues, 1)
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def output_dir_for(src: Path, cfg: Config) -> Path:
    """Where this file's transcripts belong: ``cfg.output_dir`` or beside the input."""
    return Path(cfg.output_dir) if cfg.output_dir else src.parent


def output_target(src: Path, fmt: str, cfg: Config) -> Path:
    """The exact path one format will be written to.

    With overwrite off an occupied name is stepped aside to
    ``<stem>.transcript.<fmt>``, which is the name the app itself produced on an
    earlier run. Detection and writing both go through here so they can never
    disagree about whether a transcript is already on disk.
    """
    outdir = output_dir_for(src, cfg)
    target = outdir / f"{src.stem}.{fmt}"
    if target.exists() and not cfg.overwrite:
        target = outdir / f"{src.stem}.transcript.{fmt}"
    return target


def existing_transcripts(src: Path, cfg: Config) -> list[Path]:
    """Transcripts already on disk for ``src``, in the configured formats.

    Cheap (one ``stat`` per format) and deliberately conservative: only a file
    with an output extension counts, so an unrelated ``talk.txt`` that the user
    wrote by hand is indistinguishable from a transcript and is treated as one.
    That is the safe direction — re-transcribing minutes of audio by accident
    costs far more than skipping a file the user can re-run with Remove + Add.
    """
    outdir = output_dir_for(src, cfg)
    found: list[Path] = []
    for fmt in cfg.formats:
        for candidate in (
            outdir / f"{src.stem}.{fmt}",
            outdir / f"{src.stem}.transcript.{fmt}",
        ):
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def write_outputs(cues: list[Cue], meta: dict, src: Path, cfg: Config) -> list[Path]:
    """Write every requested format next to the input (or into cfg.output_dir)."""
    outdir = output_dir_for(src, cfg)
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in cfg.formats:
        target = output_target(src, fmt, cfg)
        if fmt == "srt":
            write_srt(cues, target)
        elif fmt == "vtt":
            write_vtt(cues, target)
        elif fmt == "txt":
            write_txt(cues, target)
        elif fmt == "json":
            write_json(cues, target, meta)
        elif fmt == "tsv":
            write_ts_txt(cues, target)
        written.append(target)
    return written
