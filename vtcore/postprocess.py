"""Cue-level repairs: decoder-loop collapsing and vocabulary corrections."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .cues import Cue
from .text import apply_corrections, normalise_text, wrap_lines


def _normalise_for_repeat(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.casefold()).strip()


def collapse_loops(
    cues: list[Cue],
    *,
    max_period: int = 4,
    min_repeats: int = 3,
    text_limit: int = 4,
    min_words: int = 4,
    ngram_size: int = 6,
    similarity: float = 0.8,
    fragment_words: int = 4,
) -> tuple[list[Cue], int, float | None]:
    """Collapse Whisper's decoder repetition loops.

    Every so often the decoder locks up and repeats one sentence (or a short
    cycle of sentences) for the rest of the file, with cue durations shrinking
    to a few dozen milliseconds. Three passes catch that without harming real
    speech:

    1. consecutive repeated cycles of up to ``max_period`` cues, where the cycle
       repeats at least ``min_repeats`` times -> keep the first cycle only;
    2. a frequency cap on identical *long* cue texts (``min_words`` or more), so
       short backchannel repetitions like "Yeah." survive;
    3. a cap on reused word ``ngram_size``-grams, because a looping decoder drifts
       slightly and leaves near-duplicate lines that pass 1 and 2 would miss.

    Returns the surviving cues, how many cues were dropped, and the timestamp
    where the first loop was seen (or None when nothing was removed).
    """
    if not cues:
        return cues, 0, None

    keys = [_normalise_for_repeat(cue.flat) for cue in cues]
    total = len(cues)
    keep = [True] * total
    removed = 0
    first_loop: float | None = None

    index = 0
    while index < total:
        matched = False
        for period in range(1, max_period + 1):
            if index + period * min_repeats > total:
                continue
            cycle = keys[index : index + period]
            if not any(cycle):
                continue
            end = index + period
            repeats = 1
            while end + period <= total and keys[end : end + period] == cycle:
                repeats += 1
                end += period
            if repeats >= min_repeats:
                for drop in range(index + period, end):
                    if keep[drop]:
                        keep[drop] = False
                        removed += 1
                if first_loop is None:
                    first_loop = cues[index].start
                index = end
                matched = True
                break
        if not matched:
            index += 1

    seen: dict[str, int] = {}
    for position, cue in enumerate(cues):
        if not keep[position]:
            continue
        key = keys[position]
        if len(key.split()) < min_words:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > text_limit:
            keep[position] = False
            removed += 1
            if first_loop is None:
                first_loop = cue.start

    # Pass 3: a looping decoder drifts, so whole-cue equality leaves
    # near-duplicate stragglers behind. Cues are bucketed by their first few
    # words (a loop always reuses its opening) and compared by token overlap, so
    # that "…about topic 7" / "…about topic 8" stay - they differ - while
    # "…towards the end of" / "…towards the end of where I" do not.
    if similarity > 0:
        buckets: dict[tuple[str, ...], list[set[str]]] = {}
        for position, cue in enumerate(cues):
            if not keep[position]:
                continue
            tokens = keys[position].split()
            if len(tokens) < max(min_words, ngram_size):
                continue
            bucket = buckets.setdefault(tuple(tokens[:ngram_size]), [])
            current = set(tokens)
            current_size = len(current)
            # Only the comparison with `text_limit` matters, so counting stops as
            # soon as the cap is reached, and Jaccard is computed arithmetically
            # (|union| = |A| + |B| - |A n B|) to avoid materialising the union set.
            # Together these keep the common looping case near O(text_limit) per
            # cue; the exact-count worst case remains O(N * bucket).
            alike = 0
            for previous in bucket:
                shared = len(current & previous)
                if shared and shared / (current_size + len(previous) - shared) >= similarity:
                    alike += 1
                    if alike >= text_limit:
                        break
            if alike >= text_limit:
                keep[position] = False
                removed += 1
                if first_loop is None:
                    first_loop = cue.start
                continue
            bucket.append(current)

    # Pass 4: short debris stranded inside a collapsed region ("And", "about").
    # Only fires between two already-removed cues, so ordinary dialogue - even a
    # long pause followed by a two-word reply - is untouched.
    for _sweep in range(4):
        changed = False
        for position, cue in enumerate(cues):
            if not keep[position]:
                continue
            if len(keys[position].split()) > fragment_words:
                continue
            before, after = position - 1, position + 1
            inside_removed = 0 <= before < total and after < total and not keep[before] and not keep[after]
            if inside_removed:
                keep[position] = False
                removed += 1
                changed = True
                if first_loop is None:
                    first_loop = cue.start
        if not changed:
            break

    if not removed:
        return cues, 0, None
    return [cue for position, cue in enumerate(cues) if keep[position]], removed, first_loop


def polish_cues(
    cues: list[Cue],
    corrections: Mapping[str, str],
    normalise: bool = True,
    max_line_chars: int = 42,
) -> tuple[list[Cue], int]:
    """Apply corrections and spacing tidy-up to finished cues.

    Cues whose text does not change are returned untouched, so already-correct
    line breaks are preserved. When a cue does change it is re-wrapped with the
    same rules used during cue building.
    """
    if not corrections and not normalise:
        return cues, 0
    applied_total = 0
    polished: list[Cue] = []
    for cue in cues:
        original = cue.flat
        text, applied = apply_corrections(original, corrections)
        applied_total += applied
        if normalise:
            text = normalise_text(text)
        if text == original:
            polished.append(cue)
            continue
        polished.append(
            Cue(
                start=cue.start,
                end=cue.end,
                lines=wrap_lines(text.split(), max_line_chars),
                words=cue.words,
            )
        )
    return polished, applied_total
