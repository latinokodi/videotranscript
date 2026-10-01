"""Word/Cue data model and the cue-building rules that keep subtitles readable."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text import wrap_lines


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def flat(self) -> str:
        return " ".join(" ".join(self.lines).split())


BREAK_PUNCT = re.compile(r"[,;:.!?。！？…][\"'”’)\]]*$")

SENTENCE_END = re.compile(r"[.!?。！？…][\"'”’)\]]*$")


MIN_BREAK_CHARS = 12  # don't record a split point before the cue has any substance


def build_cues(
    words: list[Word],
    *,
    max_duration: float,
    max_cue_chars: int,
    min_cue_chars: int,
    gap_break: float,
    max_line_chars: int = 42,
) -> list[Cue]:
    """Group word timestamps into cues that read well as subtitles.

    Cues break preferentially at natural boundaries (sentence end, comma, or a
    pause). Hard limits (character count / duration) only force a break, and
    even then the split happens at the most recent natural boundary rather than
    mid-phrase — which is what stops "…do for / you, ask…" style orphans.

    Timestamps come straight from the words: nothing is stretched or padded.
    """
    cues: list[Cue] = []
    cur: list[Word] = []
    cur_len = 0
    best_break: int | None = None  # index into `cur` = end of the preferred split

    def emit(tokens: list[Word]) -> None:
        if not tokens:
            return
        start = tokens[0].start
        end = max(w.end for w in tokens)
        cues.append(Cue(start=start, end=max(end, start + 0.05), words=list(tokens)))

    def cur_chars(tokens: list[Word]) -> int:
        return len(" ".join(t.text for t in tokens))

    def force_break() -> None:
        """Break now, preferring the last natural boundary inside the cue."""
        nonlocal cur, cur_len, best_break
        if best_break is not None and 0 < best_break < len(cur):
            head, tail = cur[:best_break], cur[best_break:]
            emit(head)
            cur = tail
            cur_len = cur_chars(cur)
        else:
            emit(cur)
            cur, cur_len = [], 0
        best_break = None

    for i, w in enumerate(words):
        token = w.text.strip()
        if not token:
            continue
        add_len = len(token) + (1 if cur_len else 0)
        nxt = words[i + 1] if i + 1 < len(words) else None

        if cur:
            over_chars = (cur_len + add_len) > max_cue_chars
            over_time = (w.end - cur[0].start) > max_duration
            long_pause = (w.start - cur[-1].end) > gap_break
            # A pause alone never strands a single word on its own line.
            if over_chars or over_time or (long_pause and len(cur) >= 2):
                force_break()
                add_len = len(token) + (1 if cur_len else 0)

        cur.append(w)
        cur_len += add_len

        # Remember this spot as a good place to split if the limits are hit later.
        # `cur_len` is maintained incrementally so this stays O(n) per cue.
        ends_clause = bool(BREAK_PUNCT.search(token))
        gap_after = nxt is not None and (nxt.start - w.end) > 0.25
        if (ends_clause or gap_after) and cur_len >= max(MIN_BREAK_CHARS, min_cue_chars // 2):
            best_break = len(cur)

        # Sentence end is the strongest boundary - close the cue if it is long enough.
        if (SENTENCE_END.search(token) and cur_len >= min_cue_chars) or (
            SENTENCE_END.search(token)
            and nxt is not None
            and (nxt.start - w.end) > 0.45
            and cur_len >= min_cue_chars // 2
        ):
            emit(cur)
            cur, cur_len, best_break = [], 0, None

    emit(cur)

    # Wrap each cue's real words into display lines (timestamps stay untouched).
    for cue in cues:
        cue.lines = wrap_lines([w.text for w in cue.words], max_line_chars=max_line_chars)
    return cues
