"""Text formatting, line wrapping and the custom-vocabulary rules."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence


def log(msg: str = "") -> None:
    print(msg, file=sys.stderr, flush=True)


def fmt_timestamp(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, seconds)
    ms_total = round(seconds * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def human_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def wrap_lines(tokens: list[str], max_line_chars: int, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        candidate = f"{cur} {tok}".strip()
        if cur and len(candidate) > max_line_chars and len(lines) < max_lines - 1:
            lines.append(cur)
            cur = tok
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = [*lines[: max_lines - 1], " ".join(lines[max_lines - 1 :])]
    return lines


_SPACING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s+([%‰])"), r"\1"),
    (re.compile(r"(?<=\d)\s+,(?=\d)"), ","),
    (re.compile(r"(?<=\d)\s+\.(?=\d)"), "."),
    (re.compile(r"(?<=[^\W\d_])\s+-(?=[^\W\d_])"), "-"),
    (re.compile(r"[ \t]{2,}"), " "),
)


def parse_corrections(values: Sequence[str] | str | None) -> dict[str, str]:
    """Parse ``wrong=right`` entries into an ordered mapping.

    Accepts a list of entries (one per CLI flag) or a single newline/comma
    separated blob (what the GUI settings box produces). Entries without a ``=``
    or with an empty side are ignored so a half-typed line cannot corrupt a run.
    """
    if not values:
        return {}
    if isinstance(values, str):
        raw = re.split(r"[\n,]+", values)
    else:
        raw: list[str] = []
        for value in values:
            raw.extend(re.split(r"[\n,]+", str(value)))
    corrections: dict[str, str] = {}
    for entry in raw:
        piece = entry.strip()
        if not piece or "=" not in piece:
            continue
        wrong, _, right = piece.partition("=")
        wrong, right = wrong.strip(), right.strip()
        if wrong and right:
            corrections[wrong] = right
    return corrections


def apply_corrections(text: str, corrections: Mapping[str, str]) -> tuple[str, int]:
    """Replace misheard forms with the intended ones.

    Matching is case-insensitive and word-boundary anchored, and the canonical
    spelling from the setting is always used in the output.

    Every key is matched in a *single pass*, longest alternatives first. Applying
    one key at a time would let a shorter key rewrite text that a longer key had
    just produced (``balance sheet`` -> ``balance-sheet`` -> ``BAL-sheet``); one
    pass makes that impossible.
    """
    if not text or not corrections:
        return text, 0
    alternation = "|".join(re.escape(key) for key in sorted(corrections, key=len, reverse=True))
    pattern = re.compile(rf"(?<![^\W\d_])(?:{alternation})(?![^\W\d_])", re.IGNORECASE)
    canonical = {key.casefold(): value for key, value in corrections.items()}

    def replace(match: re.Match[str]) -> str:
        return canonical[match.group(0).casefold()]

    return pattern.subn(replace, text)


def normalise_text(text: str) -> str:
    """Tidy Whisper's spacing artifacts without touching the wording."""
    if not text:
        return text
    for pattern, replacement in _SPACING_RULES:
        text = pattern.sub(replacement, text)
    return text.strip()
