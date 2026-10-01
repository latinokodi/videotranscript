"""Argument parsing and the command-line entry point."""

from __future__ import annotations

import argparse

from .config import DEFAULT_TEMPERATURES, FORMATS, Config
from .media import expand_inputs
from .pipeline import run_batch
from .runtime import _CUDA_DIRS, require_ffmpeg
from .text import log, parse_corrections


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcribe.py",
        description="Local GPU video/audio -> transcript with accurate timestamps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples")[-1],
    )
    p.add_argument("inputs", nargs="+", help="video/audio files, globs, or folders")

    g = p.add_argument_group("model / device")
    g.add_argument(
        "-m",
        "--model",
        default="large-v3",
        help="Whisper model: tiny/base/small/medium/large-v2/large-v3/large-v3-turbo/"
        "distil-large-v3 or a HF repo id (default: large-v3 = best accuracy, fits 12 GB)",
    )
    g.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    g.add_argument(
        "--compute-type",
        default=None,
        help="float16 | int8_float16 | int8 | float32 (default: float16 on GPU, int8 on CPU)",
    )
    g.add_argument(
        "-l",
        "--language",
        default="auto",
        help="ISO code (en, es, de...) or 'auto' to detect (default: auto)",
    )

    g = p.add_argument_group("accuracy")
    g.add_argument("--beam-size", type=int, default=5)
    g.add_argument(
        "--temperature",
        default=None,
        help="float, or a comma list used as a fallback ladder "
        "(default: 0.0,0.2,0.4,0.6,0.8,1.0 - the ladder is what breaks decoder loops)",
    )
    g.add_argument("--initial-prompt", default=None, help="hint with names/jargon to improve spelling")
    g.add_argument(
        "--hotwords",
        default=None,
        help="custom vocabulary: names/jargon to spell correctly, e.g. "
        '"Bessent, GPIF, Ethena". Biases the decoder and is the biggest accuracy '
        "win on interviews and technical talks (costs ~10%% more decode time)",
    )
    g.add_argument(
        "--fix",
        action="append",
        default=None,
        metavar="WRONG=RIGHT",
        help='replace a misheard form in the output, e.g. --fix "Balanchine=balance sheet". '
        "Repeatable; applied word-boundary and case-insensitively",
    )
    g.add_argument(
        "--no-normalise",
        action="store_true",
        help='keep the decoder\'s raw spacing instead of tidying "75 %%" and "5 ,000"',
    )
    g.add_argument(
        "--no-vad",
        action="store_true",
        help="disable voice-activity filter (VAD removes silence hallucination)",
    )
    g.add_argument("--vad-min-silence", type=int, default=500, help="VAD min silence in ms (default 500)")
    g.add_argument(
        "--no-context",
        action="store_true",
        help="don't condition on previous text (less hallucination on noisy audio)",
    )
    g.add_argument(
        "--no-loop-guard",
        action="store_true",
        help="keep every cue even if the decoder repeats itself (disables loop collapsing)",
    )
    g.add_argument(
        "--repetition-penalty",
        type=float,
        default=1.1,
        help="decode-time penalty against reusing tokens (1.0 = off, default 1.1)",
    )
    g.add_argument(
        "--no-repeat-ngram-size",
        type=int,
        default=4,
        help="forbid repeating an n-word sequence (0 = off, default 4)",
    )
    g.add_argument(
        "--hallucination-silence",
        type=float,
        default=None,
        help="also skip silence longer than this around a suspected hallucination",
    )

    g = p.add_argument_group("subtitle shaping")
    g.add_argument("--max-cue-chars", type=int, default=84, help="max characters per cue (default 84)")
    g.add_argument(
        "--max-line-chars",
        type=int,
        default=42,
        help="max characters per subtitle line (default 42)",
    )
    g.add_argument(
        "--min-cue-chars",
        type=int,
        default=20,
        help="min chars before a sentence break (default 20)",
    )
    g.add_argument("--max-duration", type=float, default=7.0, help="max seconds per cue (default 7)")
    g.add_argument(
        "--gap-break",
        type=float,
        default=0.8,
        help="break cue on pauses longer than this (default 0.8)",
    )

    g = p.add_argument_group("output")
    g.add_argument("-o", "--output-dir", default=None, help="where to write files (default: next to input)")
    g.add_argument(
        "-f",
        "--formats",
        default="srt,txt,json",
        help="comma list of srt,vtt,txt,tsv,json (default: srt,txt,json)",
    )
    g.add_argument("--overwrite", action="store_true", help="overwrite existing output files")
    g.add_argument(
        "-j",
        "--parallel-jobs",
        type=int,
        default=1,
        metavar="N",
        help="transcribe N files at once (default 1). Each extra job needs its own "
        "CUDA workspace and temp WAV, so raise it only while VRAM allows",
    )
    g.add_argument("--quiet", action="store_true", help="no per-segment progress")
    return p


def parse_temperatures(value: str | float | list[float] | None) -> float | list[float]:
    """Accept a single temperature or a comma list; default to the fallback ladder."""
    if value is None:
        return list(DEFAULT_TEMPERATURES)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        return value
    parts = [piece.strip() for piece in str(value).split(",") if piece.strip()]
    if not parts:
        return list(DEFAULT_TEMPERATURES)
    numbers = [float(piece) for piece in parts]
    return numbers[0] if len(numbers) == 1 else numbers


def config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
        temperature=parse_temperatures(args.temperature),
        initial_prompt=args.initial_prompt,
        hotwords=args.hotwords,
        corrections=parse_corrections(args.fix),
        normalise_text=not args.no_normalise,
        no_vad=args.no_vad,
        vad_min_silence=args.vad_min_silence,
        no_context=args.no_context,
        loop_guard=not args.no_loop_guard,
        repetition_penalty=args.repetition_penalty,
        no_repeat_ngram_size=args.no_repeat_ngram_size,
        hallucination_silence_threshold=args.hallucination_silence,
        max_cue_chars=args.max_cue_chars,
        max_line_chars=args.max_line_chars,
        min_cue_chars=args.min_cue_chars,
        max_duration=args.max_duration,
        gap_break=args.gap_break,
        formats=args.formats,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        parallel_jobs=args.parallel_jobs,
        quiet=args.quiet,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    unknown = [f for f in args.formats if f not in FORMATS]
    if unknown:
        log(f"! unknown format(s): {', '.join(unknown)}")
        return 2

    files = expand_inputs(args.inputs)
    if not files:
        log("No input media found.")
        return 2

    cfg = config_from_args(args)
    if not require_ffmpeg():
        log("! ffmpeg not found on PATH - falling back to in-process decoding.")

    if _CUDA_DIRS and not cfg.quiet:
        log(f"CUDA runtime dirs added to PATH: {len(_CUDA_DIRS)}")

    def on_event(event: str, payload: dict) -> None:
        if event == "file_start":
            log(f"[{payload['index']}/{payload['total']}] {payload['name']}")
        elif event == "file_failed":
            log(f"  ! FAILED: {payload['error']}\n")
        elif event == "file_done":
            m = payload["meta"]
            log(
                f"  done in {m['elapsed_sec']:.1f}s ({payload['speed']:.1f}x realtime) - "
                f"{m['language']} {m['duration_hms']}, {m['cue_count']} cues"
            )
            for w in payload["files"]:
                log(f"  -> {w}")
            log()

    try:
        failures = run_batch(files, cfg, on_event)
    except KeyboardInterrupt:
        log("\nInterrupted.")
        return 130
    except Exception as exc:
        log(f"! {exc.__class__.__name__}: {exc}")
        return 4

    if failures:
        log(f"{failures} file(s) failed.")
        return 1
    return 0
