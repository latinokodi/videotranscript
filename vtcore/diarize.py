"""Offline speaker diarization — who spoke when — with no PyTorch and no HF token.

Two small ONNX models from a public, ungated re-export of pyannote's
`speaker-diarization-community-1` pipeline:

1. **segmentation** (5.9 MB) consumes raw 16 kHz audio and emits, per frame, a
   *powerset* of up to three simultaneously-active speakers.
2. **embedding** (26.5 MB, WeSpeaker ResNet34) turns a speech segment into a
   256-d voiceprint. It wants Kaldi-style 80-dim log-mel filterbanks, which this
   module computes in numpy — verified against Windows TTS ground truth, where a
   per-utterance mean removal (CMN) separated three voices with a cosine margin
   of +0.368 and 12/12 nearest-neighbour accuracy.

Deliberate differences from pyannote's own pipeline, both verified as safe here:

* pyannote 4 uses **VBx + PLDA** clustering; this uses complete-linkage
  agglomerative clustering on cosine distance, which is what the reference ONNX
  implementation (`pyannote-rs`) does too. It measured perfectly separable on the
  ground-truth set, so the extra machinery buys nothing measurable.
* Everything runs on **CPU**. The reference implementation reports under a minute
  per hour of audio on CPU, and these two models measured RTF ~0.011 here, so a
  GPU would save a rounding error while adding a second CUDA consumer next to
  CTranslate2.
"""

from __future__ import annotations

import itertools
import math
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SEGMENTATION_REPO = "altunenes/speaker-diarization-community-1-onnx"
SEGMENTATION_FILE = "segmentation-community-1.onnx"
EMBEDDING_FILE = "embedding_model.onnx"

SAMPLE_RATE = 16000
FRAME_LENGTH = 400  # 25 ms
FRAME_SHIFT = 160  # 10 ms
FFT_SIZE = 512
NUM_MEL = 80
LOW_FREQ = 20.0
HIGH_FREQ = 0.0  # 0 means Nyquist, as in Kaldi
PREEMPHASIS = 0.97
_EPSILON = 1.1920929e-07

# pyannote's segmentation is trained on 10 s excerpts; longer audio is processed
# as overlapping windows. Each window contributes only its *central* step, so the
# step is also the time resolution: at 1 s a brisk interview exchanges shorter
# lines than that and they never separate, at 0.5 s they do, for twice a cost that
# is still a rounding error next to transcription.
WINDOW_SECONDS = 10.0
WINDOW_STEP_SECONDS = 0.5

MAX_FRAME_SPEAKERS = 3  # 2**3 - 1 == the 7 powerset classes the model emits
MIN_SPEECH_SECONDS = 0.25
MIN_TURN_SECONDS = 0.4
# Two thresholds, because the two jobs are different. A speaker's identity is
# established from spans this long or more, where a voiceprint is unambiguous.
MIN_CLUSTER_SECONDS = 1.0
# Below that a span is still embedded - the model works from ~0.1 s - but it is
# never allowed to invent a speaker of its own: it is matched against the
# established voices. A brisk interview exchanges 1 s lines, and both clustering
# short spans independently and assigning them by time mislabel that exchange.
MIN_EMBED_SECONDS = 0.45
SMOOTHING_SECONDS = 0.2
# Clusters holding less than this are boundary debris rather than a real speaker.
MIN_SPEAKER_SHARE = 0.04

# Cosine similarity above which two segments are called the same speaker.
DEFAULT_THRESHOLD = 0.7

ProgressFn = Callable[[float], None]


# --------------------------------------------------------------------------- #
# Kaldi-style filterbanks (what the embedding model expects)
# --------------------------------------------------------------------------- #


def _mel_scale(freq: np.ndarray | float) -> np.ndarray:
    return 1127.0 * np.log(1.0 + np.asarray(freq) / 700.0)


def _mel_banks() -> np.ndarray:
    """Kaldi's triangular mel filterbank, built exactly as MelBanks::MelBanks does."""
    high = 0.5 * SAMPLE_RATE + HIGH_FREQ if HIGH_FREQ <= 0 else HIGH_FREQ
    num_fft_bins = FFT_SIZE // 2
    fft_bin_width = SAMPLE_RATE / FFT_SIZE
    mel_low, mel_high = float(_mel_scale(LOW_FREQ)), float(_mel_scale(high))
    delta = (mel_high - mel_low) / (NUM_MEL + 1)
    banks = np.zeros((NUM_MEL, num_fft_bins + 1), dtype=np.float64)
    for bank in range(NUM_MEL):
        left = mel_low + bank * delta
        center = mel_low + (bank + 1) * delta
        right = mel_low + (bank + 2) * delta
        for index in range(num_fft_bins + 1):
            mel = float(_mel_scale(fft_bin_width * index))
            if left < mel < right:
                if mel <= center:
                    banks[bank, index] = (mel - left) / (center - left)
                else:
                    banks[bank, index] = (right - mel) / (right - center)
    return banks


_MEL_BANKS = _mel_banks()
_POVEY_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FRAME_LENGTH) / (FRAME_LENGTH - 1))) ** 0.85


def kaldi_fbank(samples: np.ndarray) -> np.ndarray:
    """80-dim log-mel features, computed the way Kaldi computes them.

    Order matters and matches Kaldi: remove the DC offset, pre-emphasise, apply
    the Povey window, take the power spectrum, then the mel filterbank and a log.
    """
    num_frames = 1 + (len(samples) - FRAME_LENGTH) // FRAME_SHIFT
    if num_frames <= 0:
        return np.zeros((0, NUM_MEL), dtype=np.float32)
    offsets = np.arange(FRAME_LENGTH)[None, :] + FRAME_SHIFT * np.arange(num_frames)[:, None]
    frames = samples[offsets].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= PREEMPHASIS * frames[:, :-1]
    frames *= _POVEY_WINDOW
    spectrum = np.fft.rfft(frames, n=FFT_SIZE)
    mel = (spectrum.real**2 + spectrum.imag**2) @ _MEL_BANKS.T
    np.maximum(mel, _EPSILON, out=mel)
    return np.log(mel).astype(np.float32)


# --------------------------------------------------------------------------- #
# Model access
# --------------------------------------------------------------------------- #


@dataclass
class DiarizationModels:
    """The two ONNX sessions, loaded once and reused across files."""

    segmentation: object
    embedding: object
    frames_per_second: float


_MODEL_CACHE: dict[str, DiarizationModels] = {}


def clear_model_cache() -> None:
    _MODEL_CACHE.clear()


def load_models(on_event: Callable[[str, dict], None] | None = None) -> DiarizationModels:
    """Fetch (once, cached by huggingface_hub) and open both ONNX models."""
    key = "diarization"
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download

    def emit(event: str, **payload: object) -> None:
        if on_event:
            on_event(event, payload)

    emit("log", text="loading diarization models (segmentation + speaker embedding)")
    seg_path = hf_hub_download(SEGMENTATION_REPO, SEGMENTATION_FILE)
    emb_path = hf_hub_download(SEGMENTATION_REPO, EMBEDDING_FILE)
    providers = ["CPUExecutionProvider"]
    segmentation = ort.InferenceSession(seg_path, providers=providers)
    embedding = ort.InferenceSession(emb_path, providers=providers)

    # Derive the frame rate instead of hardcoding it: the model decides how many
    # frames a 10 s window becomes, and mapping frames back to seconds depends on it.
    probe = np.zeros((1, 1, SAMPLE_RATE), dtype=np.float32)
    frames = segmentation.run(None, {"input_values": probe})[0].shape[1]
    models = DiarizationModels(segmentation, embedding, frames / 1.0)
    _MODEL_CACHE[key] = models
    emit("log", text=f"diarization models ready ({frames} frames/s)")
    return models


# --------------------------------------------------------------------------- #
# Segmentation: powerset frames -> speech regions
# --------------------------------------------------------------------------- #


def _powerset_to_multilabel(logits: np.ndarray) -> np.ndarray:
    """Decode the 7-class powerset into one activity column per speaker.

    Class index ``c`` is the binary mask ``c + 1``, so class 0 is speaker 0 alone
    and class 6 is all three together. Verified on ground truth: the three
    singleton classes each dominate for a different voice.
    """
    num_frames = logits.shape[0]
    active = np.zeros((num_frames, MAX_FRAME_SPEAKERS), dtype=bool)
    winner = logits.argmax(axis=1)
    for speaker in range(MAX_FRAME_SPEAKERS):
        active[:, speaker] = ((winner + 1) >> speaker) & 1 == 1
    return active


def _activity_frames(
    samples: np.ndarray, models: DiarizationModels, progress: ProgressFn | None
) -> np.ndarray:
    """Per-frame speaker activity, taken from the *centre* of each sliding window.

    A 10 s window stepped by 1 s predicts the same instant ten times, and near a
    speaker change only half of those agree: averaged, the boundary becomes a
    run of ~0.5 votes that flips either way, smearing every turn boundary into
    fragments. So each window contributes only its central step, where its own
    receptive field is fully inside the window and the prediction is trustworthy;
    every timeline frame therefore has exactly one owner and no averaging.
    """
    window = int(WINDOW_SECONDS * SAMPLE_RATE)
    step = int(WINDOW_STEP_SECONDS * SAMPLE_RATE)
    halo = max(0, (window - step) // 2)
    frames_per_second = models.frames_per_second
    total_frames = math.ceil(len(samples) / SAMPLE_RATE * frames_per_second) + 1
    activity = np.zeros((total_frames, MAX_FRAME_SPEAKERS), dtype=bool)
    owned = np.zeros(total_frames, dtype=bool)

    starts = list(range(0, max(1, len(samples) - window + step), step)) or [0]
    for index, start in enumerate(starts):
        chunk = samples[start : start + window]
        if len(chunk) < FRAME_LENGTH:
            break
        logits = models.segmentation.run(None, {"input_values": chunk[None, None, :].astype(np.float32)})[0][
            0
        ]
        active = _powerset_to_multilabel(logits)

        # Each predicted frame covers window_seconds / num_frames of audio; keep the
        # ones whose centre time falls inside this window's central step.
        num_frames = active.shape[0]
        frame_seconds = (len(chunk) / SAMPLE_RATE) / max(1, num_frames)
        centres = (np.arange(num_frames) + 0.5) * frame_seconds
        low, high = halo / SAMPLE_RATE, (halo + step) / SAMPLE_RATE
        selected = (centres >= low) & (centres < high)
        if not selected.any():
            continue
        targets = np.round((start / SAMPLE_RATE + centres[selected]) * frames_per_second).astype(int)
        keep = (targets >= 0) & (targets < total_frames)
        activity[targets[keep]] = active[selected][keep]
        owned[targets[keep]] = True
        if progress:
            progress((index + 1) / len(starts) * 100)

    # The first and last halo have no dedicated window; carry the nearest decision.
    if owned.any() and not owned.all():
        indices = np.arange(total_frames)
        nearest = np.searchsorted(indices[owned], indices)
        nearest = np.clip(nearest, 0, owned.sum() - 1)
        activity[~owned] = activity[indices[owned][nearest[~owned]]]
    return activity


def _smooth(activity: np.ndarray, frames_per_second: float) -> np.ndarray:
    """Majority-filter the per-frame activity to remove single-frame flicker.

    The segmentation occasionally flips its pattern for a frame or two mid-turn.
    Those flips are not speaker changes, and left alone they shatter a turn into
    fragments that each need an embedding.
    """
    window = max(1, int(SMOOTHING_SECONDS * frames_per_second) | 1)
    if window <= 1 or len(activity) <= window:
        return activity
    kernel = np.ones(window) / window
    smoothed = np.stack(
        [
            np.convolve(activity[:, speaker].astype(np.float32), kernel, mode="same")
            for speaker in range(activity.shape[1])
        ],
        axis=1,
    )
    return smoothed >= 0.5


def _segments_from_activity(activity: np.ndarray, frames_per_second: float) -> list[tuple[float, float]]:
    """Split the timeline at speaker-change points into candidate segments.

    Splitting on *silence* is not enough: an interview with no pauses is a single
    continuous speech region, and embedding it whole would describe the average of
    everyone talking. The segmentation's per-frame activity pattern changes when
    the speaker does, so each maximal run of one pattern becomes a candidate. The
    embeddings are then clustered globally, which is what turns those local
    patterns into consistent speaker identities.
    """
    segments: list[tuple[float, float]] = []
    start: int | None = None
    previous: tuple[bool, ...] | None = None

    for frame, pattern in enumerate(map(tuple, activity)):
        if pattern == previous:
            continue
        if previous is not None and any(previous) and start is not None:
            segments.append((start / frames_per_second, frame / frames_per_second))
        start = frame if any(pattern) else None
        previous = pattern

    if previous is not None and any(previous) and start is not None:
        segments.append((start / frames_per_second, len(activity) / frames_per_second))
    return [span for span in segments if span[1] - span[0] >= MIN_SPEECH_SECONDS]


# --------------------------------------------------------------------------- #
# Embeddings and clustering
# --------------------------------------------------------------------------- #


def _embed(samples: np.ndarray, models: DiarizationModels) -> np.ndarray:
    """One L2-normalised voiceprint for a speech span."""
    features = kaldi_fbank(samples)
    if features.shape[0] < 10:
        return np.zeros(256, dtype=np.float64)
    # Per-utterance mean removal: measured to lift the same/different margin from
    # +0.206 to +0.368 on ground truth, so it is worth the extra subtraction.
    features = features - features.mean(axis=0, keepdims=True)
    vector = models.embedding.run(None, {"fbank_features": features[None, :, :]})[0].reshape(-1)
    vector = np.asarray(vector, dtype=np.float64)
    return vector / (np.linalg.norm(vector) + 1e-9)


def merge_similar_clusters(
    vectors: np.ndarray, labels: list[int], threshold: float = DEFAULT_THRESHOLD
) -> list[int]:
    """Reunite clusters whose own representatives match.

    Complete linkage can split a single voice across two clusters when one short
    segment drifts - a quiet tail, say - because the rule must hold for *every*
    cross pair. Comparing the clusters' centroids by the same cosine criterion
    puts them back together, and it cannot merge genuinely different speakers
    whose centroids sit further apart than the segments ever did.
    """
    labels = list(labels)
    while True:
        groups: dict[int, list[np.ndarray]] = {}
        for label, vector in zip(labels, vectors, strict=True):
            groups.setdefault(label, []).append(vector)
        if len(groups) < 2:
            break
        centroids = {label: (np.mean(members, axis=0), label) for label, members in groups.items()}
        normalised = {
            label: value[0] / (np.linalg.norm(value[0]) + 1e-9) for label, value in centroids.items()
        }
        best, pair = threshold, None
        for first, second in itertools.combinations(sorted(groups), 2):
            similarity = float(normalised[first] @ normalised[second])
            if similarity > best:
                best, pair = similarity, (first, second)
        if pair is None:
            break
        keep, drop = pair
        labels = [keep if label == drop else label for label in labels]

    order = {label: index for index, label in enumerate(sorted(set(labels)))}
    return [order[label] for label in labels]


def _silhouette(vectors: np.ndarray, labels: list[int]) -> float:
    """Mean silhouette on cosine distance: how well each span sits in its cluster.

    Close to 1 means every span is much more like its own cluster than any other;
    near 0 means the clustering is arbitrary. This is what decides the *number* of
    speakers without a magic similarity threshold.
    """
    count = len(vectors)
    groups = set(labels)
    if count < 2 or len(groups) < 2:
        return -1.0
    distance = 1.0 - (vectors @ vectors.T)
    np.fill_diagonal(distance, 0.0)
    scores: list[float] = []
    for index in range(count):
        own = [other for other in range(count) if labels[other] == labels[index] and other != index]
        if not own:
            scores.append(0.0)
            continue
        inside = float(distance[index, own].mean())
        outside = min(
            float(distance[index, [o for o in range(count) if labels[o] == group]].mean())
            for group in groups
            if group != labels[index]
        )
        scale = max(inside, outside)
        scores.append((outside - inside) / scale if scale > 0 else 0.0)
    return float(np.mean(scores))


def choose_speaker_count(vectors: np.ndarray, max_speakers: int = 8, floor: float = 0.15) -> int:
    """How many people are in this recording, decided by the data.

    A similarity threshold cannot answer this: a hand-over span sits between two
    voices and gets its own cluster, which reports a phantom third participant on
    a two-person interview. Scoring each candidate count by silhouette does answer
    it, because the splits that are real score higher than the ones that are not.
    A recording that is genuinely one voice scores below `floor` for every split
    and is reported as a single speaker.
    """
    count = len(vectors)
    if count < 3:
        return 1
    best_k, best_score = 1, floor
    for candidate in range(2, min(max_speakers, count - 1) + 1):
        labels = cluster_embeddings(vectors, num_speakers=candidate)
        score = _silhouette(vectors, labels)
        if score > best_score:
            best_k, best_score = candidate, score
    return best_k


def cluster_embeddings(
    vectors: np.ndarray,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_speakers: int | None = None,
) -> list[int]:
    """Complete-linkage agglomerative clustering on cosine similarity.

    Complete linkage (not average or single) is what keeps two alternating voices
    apart: a merge is only allowed when *every* pair across the two clusters is
    similar enough, so a single coincidental bridge cannot chain them together.
    """
    count = len(vectors)
    if count == 0:
        return []
    if count == 1 or (num_speakers is not None and num_speakers <= 1):
        return [0] * count

    similarity = vectors @ vectors.T
    clusters: dict[int, list[int]] = {index: [index] for index in range(count)}
    active = list(range(count))
    link = {i: {j: float(similarity[i, j]) for j in range(count) if j != i} for i in range(count)}

    def stop() -> bool:
        if num_speakers is not None:
            return len(active) <= max(1, num_speakers)
        return len(active) <= 1 or max(link[a][b] for a in active for b in active if a < b) < threshold

    while not stop():
        best, pair = -2.0, None
        for position, first in enumerate(active):
            for second in active[position + 1 :]:
                value = link[first][second]
                if value > best:
                    best, pair = value, (first, second)
        first, second = pair  # type: ignore[misc]
        members = clusters[first] + clusters[second]
        clusters[first] = members
        for other in active:
            if other in (first, second):
                continue
            link[first][other] = link[other][first] = min(
                float(similarity[x, y]) for x in members for y in clusters[other]
            )
        active.remove(second)
        for other in active:
            link[other].pop(second, None)
        link.pop(second, None)
        clusters.pop(second, None)

    order = {root: index for index, root in enumerate(sorted(clusters))}
    labels = [0] * count
    for root, members in clusters.items():
        for member in members:
            labels[member] = order[root]
    return labels


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: int

    @property
    def duration(self) -> float:
        return self.end - self.start


def _nearest_speaker(turns: Sequence[SpeakerTurn], start: float, end: float) -> int | None:
    """Label of the turn closest in time to the span, for absorbing short fragments."""
    best, best_distance = None, float("inf")
    for turn in turns:
        distance = min(abs(start - turn.end), abs(turn.start - end))
        if distance < best_distance:
            best, best_distance = turn.speaker, distance
    return best


def _fold_rare_speakers(turns: list[SpeakerTurn], share: float = MIN_SPEAKER_SHARE) -> list[SpeakerTurn]:
    """Merge away clusters that are not a real participant.

    Two independent tests, because a speaker can be wrong in two different ways:

    * it was **never established** by a long span, so it exists only out of short
      fragments matched to its centroid - that is boundary debris, however much
      time it adds up to; or
    * it holds a negligible share of the conversation.

    Either way its turns are relabelled to the adjacent established speaker, so no
    line is ever left without a label.
    """
    if not turns:
        return turns
    totals: dict[int, float] = {}
    longest: dict[int, float] = {}
    for turn in turns:
        totals[turn.speaker] = totals.get(turn.speaker, 0.0) + turn.duration
        longest[turn.speaker] = max(longest.get(turn.speaker, 0.0), turn.duration)

    cutoff = max(MIN_TURN_SECONDS, share * sum(totals.values()))
    keep = {
        speaker
        for speaker, total in totals.items()
        if total >= cutoff and longest[speaker] >= MIN_CLUSTER_SECONDS
    }
    if not keep or len(keep) == len(totals):
        return turns
    kept_turns = [turn for turn in turns if turn.speaker in keep]
    folded = [
        turn
        if turn.speaker in keep
        else SpeakerTurn(
            turn.start, turn.end, _nearest_speaker(kept_turns, turn.start, turn.end) or turn.speaker
        )
        for turn in turns
    ]
    # Re-number so the surviving speakers stay contiguous: Speaker 1, 2, 3...
    renumber = {speaker: index for index, speaker in enumerate(sorted(keep))}
    return [SpeakerTurn(t.start, t.end, renumber.get(t.speaker, t.speaker)) for t in folded]


def diarize(
    samples: np.ndarray,
    *,
    models: DiarizationModels | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    num_speakers: int | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> list[SpeakerTurn]:
    """Return speaker turns for 16 kHz mono audio, ordered by start time."""
    models = models or load_models(on_event)

    def progress(pct: float) -> None:
        if on_event:
            on_event("diarize", {"pct": pct})

    activity = _smooth(_activity_frames(samples, models, progress), models.frames_per_second)
    segments = _segments_from_activity(activity, models.frames_per_second)
    if not segments:
        return []

    confident = [span for span in segments if span[1] - span[0] >= MIN_CLUSTER_SECONDS]
    if not confident:
        return []
    vectors = np.asarray(
        [
            _embed(samples[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)], models)
            for start, end in confident
        ]
    )
    if num_speakers is not None:
        labels = cluster_embeddings(vectors, num_speakers=num_speakers)
    else:
        # The count is decided by the data (silhouette), never by a similarity
        # threshold: a threshold cannot tell "two people talking over each other"
        # from "a third person", and it was measurably worse at both ends.
        labels = cluster_embeddings(vectors, num_speakers=choose_speaker_count(vectors))
        labels = merge_similar_clusters(vectors, labels, threshold)

    turns = merge_adjacent(
        [SpeakerTurn(start, end, label) for (start, end), label in zip(confident, labels, strict=True)]
    )

    # A span too short to define a speaker still has a voiceprint, so let it vote:
    # match it against the voices already established rather than assuming it
    # belongs to whichever turn happens to be nearest in time.
    centroids = _centroids(vectors, labels)
    for span in segments:
        if span[1] - span[0] >= MIN_CLUSTER_SECONDS:
            continue
        speaker = None
        if span[1] - span[0] >= MIN_EMBED_SECONDS and centroids:
            vector = _embed(samples[int(span[0] * SAMPLE_RATE) : int(span[1] * SAMPLE_RATE)], models)
            speaker = max(centroids, key=lambda label: float(vector @ centroids[label]))
        if speaker is None:
            speaker = _nearest_speaker(turns, span[0], span[1])
        if speaker is not None:
            turns.append(SpeakerTurn(span[0], span[1], speaker))

    turns.sort(key=lambda turn: turn.start)
    turns = merge_adjacent(turns)
    return _fold_rare_speakers([t for t in turns if t.duration >= MIN_TURN_SECONDS])


def _centroids(vectors: np.ndarray, labels: list[int]) -> dict[int, np.ndarray]:
    """One normalised average voiceprint per speaker, for matching short spans."""
    groups: dict[int, list[np.ndarray]] = {}
    for label, vector in zip(labels, vectors, strict=True):
        groups.setdefault(label, []).append(vector)
    centroids: dict[int, np.ndarray] = {}
    for label, members in groups.items():
        mean = np.mean(members, axis=0)
        centroids[label] = mean / (np.linalg.norm(mean) + 1e-9)
    return centroids


def merge_adjacent(turns: Sequence[SpeakerTurn], gap: float = 0.35) -> list[SpeakerTurn]:
    """Join consecutive turns by the same speaker across short pauses."""
    merged: list[SpeakerTurn] = []
    for turn in turns:
        if merged and merged[-1].speaker == turn.speaker and turn.start - merged[-1].end <= gap:
            merged[-1] = SpeakerTurn(merged[-1].start, turn.end, turn.speaker)
        else:
            merged.append(turn)
    return merged


def read_wav_mono(path: Path) -> np.ndarray:
    """Read a 16 kHz mono 16-bit WAV (what vtcore.media.extract_audio produces)."""
    with wave.open(str(path)) as handle:
        if handle.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{path.name}: expected {SAMPLE_RATE} Hz, got {handle.getframerate()}")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def speaker_name(index: int) -> str:
    """Display name for a cluster index: Speaker 1 is whoever talks first."""
    return f"Speaker {index + 1}"


def assign_to_spans(turns: Sequence[SpeakerTurn], spans: Sequence[tuple[float, float]]) -> list[int | None]:
    """Label arbitrary ``(start, end)`` spans by the speaker covering most of each.

    This is how Whisper cues get their speaker: cues and diarization turns are
    independent segmentations of the same timeline, so each cue takes whichever
    speaker overlaps it for the longest.
    """
    labels: list[int | None] = []
    for start, end in spans:
        weight: dict[int, float] = {}
        for turn in turns:
            overlap = min(end, turn.end) - max(start, turn.start)
            if overlap > 0:
                weight[turn.speaker] = weight.get(turn.speaker, 0.0) + overlap
        labels.append(max(weight, key=lambda key: weight[key]) if weight else None)
    return labels


def speaker_count(turns: Sequence[SpeakerTurn]) -> int:
    return len({turn.speaker for turn in turns}) or 0


def total_speech(turns: Sequence[SpeakerTurn]) -> float:
    return math.fsum(turn.duration for turn in turns)
