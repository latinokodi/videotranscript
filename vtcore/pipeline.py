"""One file and one batch through decode -> transcribe -> cues -> files."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from . import diarize
from .config import Config, EventFn
from .cues import Cue, Word, build_cues
from .media import extract_audio, probe_duration, safe_unlink
from .models import load_model
from .postprocess import collapse_loops, polish_cues
from .text import human_time, log
from .writers import existing_transcripts, write_outputs


def skipped_meta(src: Path, found: list[Path]) -> dict:
    """A ``file_done``-shaped meta block for a file we deliberately did not run.

    Shaped like a real result so every consumer — the GUI's result column, the
    CLI's summary, the queue's progress maths — needs no special case: the only
    difference is ``skipped``, which says the files listed describe what is
    already on disk rather than work just done.
    """
    return {
        "source": src.name,
        "source_path": str(src.resolve()),
        "skipped": True,
        "outputs": [str(p) for p in found],
        "elapsed_sec": 0.0,
        "cue_count": 0,
    }


def transcribe_file(src: Path, cfg: Config, model, on_event: EventFn | None = None) -> tuple[list[Cue], dict]:
    def emit(event: str, **payload) -> None:
        if on_event:
            on_event(event, payload)

    ffmpeg = cfg.ffmpeg
    tmp_wav: Path | None = None
    started = time.time()

    clip_seconds = probe_duration(src, ffmpeg) if ffmpeg else 0.0

    if ffmpeg:
        emit("stage", name="decode", label=f"Reading audio from {src.name}", total=clip_seconds)
        tmp_wav = extract_audio(
            src,
            ffmpeg,
            duration=clip_seconds,
            on_progress=lambda pct: emit("decode", pct=pct, total=clip_seconds),
        )
        audio_input: str = str(tmp_wav)
    else:
        audio_input = str(src)  # faster-whisper's PyAV fallback

    emit("stage", name="transcribe", label=f"Transcribing {src.name}", total=clip_seconds)

    # Speaker labelling works on the decoded audio, which is a temp file that is
    # deleted the moment transcription ends. Take the samples first so the cleanup
    # can happen exactly once, on every path including failures.
    diar_samples = None

    try:
        segments, info = model.transcribe(
            audio_input,
            language=None if cfg.language in (None, "auto") else cfg.language,
            task="transcribe",
            beam_size=cfg.beam_size,
            temperature=cfg.temperature,
            vad_filter=not cfg.no_vad,
            vad_parameters={"min_silence_duration_ms": cfg.vad_min_silence} if not cfg.no_vad else None,
            word_timestamps=True,
            condition_on_previous_text=not cfg.no_context,
            initial_prompt=cfg.initial_prompt,
            hotwords=(cfg.hotwords or "").strip() or None,
            repetition_penalty=cfg.repetition_penalty,
            no_repeat_ngram_size=cfg.no_repeat_ngram_size,
            hallucination_silence_threshold=cfg.hallucination_silence_threshold,
        )

        words: list[Word] = []
        seg_count = 0
        clip_duration = float(getattr(info, "duration", 0.0) or 0.0)
        try:
            for seg in segments:
                seg_count += 1
                text = seg.text.strip()
                if not cfg.quiet:
                    log(f"  [{human_time(seg.end)}/{human_time(clip_duration)}] {text[:80]}")
                emit("segment", end=float(seg.end or 0.0), duration=clip_duration, text=text)
                for w in seg.words or []:
                    token = (w.word or "").strip()
                    if not token:
                        continue
                    words.append(
                        Word(
                            start=float(w.start or seg.start),
                            end=float(w.end or seg.end),
                            text=token,
                            probability=float(getattr(w, "probability", 1.0) or 1.0),
                        )
                    )
        finally:
            # Closing the generator releases PyAV's handle on the decoded WAV;
            # without this, Windows refuses to delete the temp file.
            closer = getattr(segments, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()
    finally:
        if tmp_wav is not None:
            if cfg.diarize:
                with contextlib.suppress(Exception):
                    diar_samples = diarize.read_wav_mono(tmp_wav)
            safe_unlink(tmp_wav)

    if not words:
        emit(
            "log",
            text="no word-level timestamps returned - transcript will be empty",
            style="warning",
        )

    cues = build_cues(
        words,
        max_duration=cfg.max_duration,
        max_cue_chars=cfg.max_cue_chars,
        min_cue_chars=cfg.min_cue_chars,
        gap_break=cfg.gap_break,
        max_line_chars=cfg.max_line_chars,
    )

    loops_removed = 0
    loop_start: float | None = None
    if cfg.loop_guard:
        cues, loops_removed, loop_start = collapse_loops(cues)
        if loops_removed:
            where = human_time(loop_start) if loop_start is not None else "unknown"
            emit(
                "log",
                text=(
                    f"decoder loop detected around {where}: dropped {loops_removed} repeated "
                    f"cue(s). Re-run this file with 'Use previous text as context' off if the "
                    f"tail of the audio matters."
                ),
                style="warning",
            )
            emit("loop_detected", at=loop_start, removed=loops_removed)

    cues, corrections_applied = polish_cues(
        cues,
        cfg.corrections,
        normalise=cfg.normalise_text,
        max_line_chars=cfg.max_line_chars,
    )
    if corrections_applied:
        emit(
            "log",
            text=f"custom vocabulary: corrected {corrections_applied} misheard word(s)",
            style="info",
        )

    speaker_turns: list[diarize.SpeakerTurn] = []
    if cfg.diarize:
        if diar_samples is not None:
            emit("stage", name="diarize", label="Identifying speakers", total=clip_duration)
            started_diarize = time.time()
            speaker_turns = diarize.diarize(
                diar_samples,
                threshold=cfg.diarize_threshold,
                num_speakers=cfg.speakers,
                on_event=on_event,
            )
            labels = diarize.assign_to_spans(speaker_turns, [(cue.start, cue.end) for cue in cues])
            for cue, label in zip(cues, labels, strict=True):
                if label is not None:
                    cue.speaker = diarize.speaker_name(label)
            found = diarize.speaker_count(speaker_turns)
            elapsed = time.time() - started_diarize
            emit(
                "log",
                text=(
                    f"speakers: {found} found in {elapsed:.1f}s "
                    f"({len(speaker_turns)} turns, {diarize.total_speech(speaker_turns):.0f}s of speech)"
                ),
                style="info" if found else "warning",
            )
            if not found:
                emit(
                    "log",
                    text="  no speech could be labelled - the file may have one voice or no speech",
                    style="warning",
                )
        else:
            emit("log", text="! speaker labels need the decoded audio; skipped", style="warning")

    meta = {
        "source": src.name,
        "source_path": str(src.resolve()),
        "language": getattr(info, "language", None),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 4),
        "duration": round(clip_duration, 3),
        "duration_hms": human_time(clip_duration),
        "model": cfg.model,
        "device": cfg.resolved_device,
        "compute_type": cfg.resolved_compute,
        "vad": not cfg.no_vad,
        "temperature": cfg.temperature,
        "repetition_penalty": cfg.repetition_penalty,
        "no_repeat_ngram_size": cfg.no_repeat_ngram_size,
        "loop_guard": cfg.loop_guard,
        "loops_removed": loops_removed,
        "loop_started_at": round(loop_start, 3) if loop_start is not None else None,
        "hotwords": cfg.hotwords or None,
        "corrections": dict(cfg.corrections) if cfg.corrections else None,
        "corrections_applied": corrections_applied,
        "text_normalised": bool(cfg.normalise_text),
        "word_count": len(words),
        "cue_count": len(cues),
        "segments": seg_count,
        "diarize": bool(cfg.diarize),
        "speakers": diarize.speaker_count(speaker_turns),
        "diarize_threshold": cfg.diarize_threshold if cfg.diarize else None,
        "speaker_turns": [
            {"start": round(turn.start, 3), "end": round(turn.end, 3), "speaker": turn.speaker}
            for turn in speaker_turns
        ]
        or None,
        "elapsed_sec": round(time.time() - started, 2),
    }
    return cues, meta


def run_batch(
    files: list[Path],
    cfg: Config,
    on_event: EventFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
    next_files: Callable[[], list[Path]] | None = None,
) -> int:
    """Transcribe every file in the queue. Returns the number of failures.

    ``should_cancel`` is polled before loading the model and between files, so a
    long queue can be stopped without killing the process mid-transcription
    (a file already being decoded always finishes cleanly).

    ``next_files`` turns the batch into a *pull* queue: instead of transcribing a
    frozen list, the batch asks for more work after every file and only stops when
    nothing is left. The GUI passes the rows still waiting in its table, so a file
    added while the run is busy is picked up the moment the current one finishes
    rather than waiting for a second run. The model is loaded once and stays
    resident across every pulled file.

    ``cfg.parallel_jobs`` decides how many of those files run at once; see
    ``_run_serial`` and ``_run_parallel`` for what changes.
    """
    emit = _emitter(on_event)
    cancelled = _canceller(should_cancel)
    state = _BatchState(files=list(files))

    if cancelled():
        emit("batch_done", failures=0, total=state.total, completed=0, skipped=0, cancelled=True)
        return 0

    # The model is loaded on first use, not up front: a queue where every file
    # already has a transcript must cost nothing at all, and it is the single most
    # expensive thing this function does (a fresh large-v3 takes ~13 s and ~3.8 GB
    # of VRAM). `_Run.model` memoises it, so it is still loaded once per batch.
    run = _Run(cfg=cfg, state=state, on_event=on_event)
    runner = _run_parallel if cfg.parallel_jobs > 1 else _run_serial
    runner(run, cancelled, next_files)

    emit(
        "batch_done",
        failures=state.failures,
        total=state.total,
        completed=state.completed,
        skipped=state.skipped,
        cancelled=cancelled(),
    )
    return state.failures


def _emitter(on_event: EventFn | None) -> Callable[..., None]:
    def emit(event: str, **payload) -> None:
        if on_event:
            on_event(event, payload)

    return emit


def _canceller(should_cancel: Callable[[], bool] | None) -> Callable[[], bool]:
    return lambda: bool(should_cancel and should_cancel())


#: Called between files to ask the caller for anything queued since the batch began.
_NextFiles = Callable[[], list["Path"]]


@dataclass
class _BatchState:
    """What the batch is doing, shared by both run modes.

    A dataclass rather than a handful of locals because the parallel mode updates
    it from several worker threads at once; every mutation goes through the lock.
    """

    files: list[Path]
    failures: int = 0
    completed: int = 0
    skipped: int = 0
    total: int = 0
    lock: Lock = field(default_factory=Lock)
    seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.total = len(self.files)

    def claim(self, src: Path) -> bool:
        """True the first time this file is offered; False for any repeat."""
        key = str(src.resolve())
        with self.lock:
            if key in self.seen:
                return False
            self.seen.add(key)
        return True

    def grow(self, fresh: list[Path]) -> None:
        """Queue newly added files: `total` must stay a real denominator."""
        with self.lock:
            self.files.extend(fresh)
            self.total += len(fresh)

    def count(self, *, failures: int = 0, completed: int = 0, skipped: int = 0) -> None:
        with self.lock:
            self.failures += failures
            self.completed += completed
            self.skipped += skipped

    def done_count(self) -> int:
        with self.lock:
            return self.completed + self.failures + self.skipped


def _handle(
    src: Path,
    cfg: Config,
    load: Callable[[], object],
    state: _BatchState,
    on_event: EventFn | None,
) -> None:
    """One file: skip it if its transcript is already there, else run and write it.

    ``load`` is a callable rather than a model so the skip check can happen before
    anything is loaded — a batch of already-transcribed files then costs nothing.
    It is only invoked once the decision to transcribe has been made.

    Called from the batch thread, and from any number of pool threads when
    ``parallel_jobs`` is above 1. Emitting from those threads is safe for the Qt
    consumers: `event` is a Qt signal, so a connected slot is *queued* onto the GUI
    thread rather than run on the worker.
    """
    emit = _emitter(on_event)
    if not state.claim(src):
        return

    # A transcript already on disk means the work is done: running it again would
    # burn minutes of GPU to overwrite (or step aside from) an identical file.
    # "Overwrite existing transcripts" is the explicit opt-out.
    if not cfg.overwrite:
        found = existing_transcripts(src, cfg)
        if found:
            state.count(skipped=1)
            emit("log", text=f"{src.name}: transcript already exists - skipped", style="info")
            emit(
                "file_skipped",
                name=src.name,
                path=str(src.resolve()),
                files=[str(p) for p in found],
                meta=skipped_meta(src, found),
            )
            return

    model = load()
    emit(
        "file_start",
        name=src.name,
        path=str(src),
        index=state.done_count() + 1,
        total=state.total,
    )
    try:
        cues, meta = transcribe_file(src, cfg, model, on_event)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        state.count(failures=1)
        emit(
            "file_failed",
            name=src.name,
            path=str(src.resolve()),
            error=f"{exc.__class__.__name__}: {exc}",
        )
        return

    emit("stage", name="write", label=f"Writing {src.name} transcripts")
    emit("decode", pct=100.0, total=0.0)
    written = write_outputs(cues, meta, src, cfg)
    speed = meta["duration"] / meta["elapsed_sec"] if meta["elapsed_sec"] else 0.0
    state.count(completed=1)
    emit("file_done", name=src.name, meta=meta, files=[str(w) for w in written], speed=speed)


def _pull(state: _BatchState, next_files: _NextFiles, cancelled: Callable[[], bool]) -> list[Path]:
    """Ask the caller for anything queued while the batch has been busy."""
    if next_files is None or cancelled():
        return []
    with state.lock:
        known = set(state.seen)
    fresh = [p for p in next_files() if str(p.resolve()) not in known]
    if fresh:
        state.grow(fresh)
    return fresh


@dataclass
class _Run:
    """Everything a runner needs, so the two runners can share one signature."""

    cfg: Config
    state: _BatchState
    on_event: EventFn | None
    _model: object | None = None
    _model_lock: Lock = field(default_factory=Lock)

    def loader(self) -> Callable[[], object]:
        """A getter for the model, which is loaded on first call and only once.

        Lazy because a batch that skips every file must not pay for a multi-GB
        load; lock-guarded because in parallel mode several workers reach this at
        the same moment and a doubled load would put two copies in VRAM.

        It hands back a *callable* rather than the model so `_handle` can defer
        touching it until after the "already transcribed?" check.
        """

        def load() -> object:
            if self._model is None:
                with self._model_lock:
                    if self._model is None:
                        self._model = load_model(self.cfg, self.on_event)
            return self._model

        return load

    def handle(self, src: Path) -> None:
        _handle(src, self.cfg, self.loader(), self.state, self.on_event)


def _run_serial(run: _Run, cancelled: Callable[[], bool], next_files: _NextFiles) -> None:
    """One file at a time — the default, and the right choice on a small GPU.

    One file at a time is also what makes the queue feel live: a pull happens
    after *every* file, so anything added while this one ran starts next.
    """
    state = run.state
    pending = list(state.files)
    state.files.clear()
    while pending and not cancelled():
        run.handle(pending.pop(0))
        pending.extend(_pull(state, next_files, cancelled))


def _run_parallel(run: _Run, cancelled: Callable[[], bool], next_files: _NextFiles) -> None:
    """``cfg.parallel_jobs`` files at once, over one shared, already-loaded model.

    Threads rather than processes: the work is dominated by native decode and disk
    I/O, CTranslate2 releases the GIL inside it, and each worker reads a different
    file — so there is no Python-level contention to serialise. The model is shared
    rather than copied per worker, which is what keeps the VRAM cost of going
    parallel to the per-job CUDA workspace instead of a second set of weights.

    `pool.map` waits for the whole group, so the pool — not this loop — is what
    overlaps the files, and at most ``parallel_jobs`` decodes are in flight.
    """
    from concurrent.futures import ThreadPoolExecutor

    state = run.state
    pending = list(state.files)
    state.files.clear()
    workers = max(1, int(run.cfg.parallel_jobs))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt") as pool:
        while pending and not cancelled():
            list(pool.map(run.handle, pending))
            pending = _pull(state, next_files, cancelled)
