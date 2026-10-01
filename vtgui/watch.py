"""Watching a folder for new media and handing each file over once it is complete.

The hard part is not noticing a new file, it is knowing when that file has
*finished arriving*. A copy or a download shows up in the target folder at zero
bytes and grows; a resumable download leaves a differently-named temporary behind;
and a file that has stopped growing may still be held open by the process writing
it. Transcribing any of those decodes half a file into a garbage transcript that
then looks like real work in the queue.

So a candidate is promoted only when it clears **three independent checks**:

1. **Not a temporary name** — ``.part``, ``.crdownload``, ``.tmp`` and friends are
   ignored outright, whatever their size.
2. **Quiet** — its size *and* modification time are unchanged across a settling
   window (default 8 s), sampled by the poll timer rather than trusting a single
   sleep.
3. **Unlocked** — an atomic same-directory rename proves no other process holds
   the file open. This is what makes a cancelled download, a file still being
   written with no size change, and a half-written copy safe.

Checks 2 and 3 are deliberately both required. A quiet window alone is fooled by a
download that stalls; a lock test alone is fooled by writers that release the
handle between chunks. Together they only ever cost a few seconds of latency.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path, PurePath

from PySide6.QtCore import QObject, QTimer, Signal

from vtcore.media import MEDIA_EXTS

#: Extensions that are *never* finished even at a plausible size. Lower-cased and
#: matched on the full suffix chain, so ``movie.mp4.part`` is caught as well as
#: ``movie.part``.
TEMP_SUFFIXES = frozenset(
    {
        ".part",
        ".partial",
        ".crdownload",
        ".download",
        ".opdownload",
        ".filepart",
        ".aria2",
        ".tmp",
        ".temp",
        ".!qb",
        ".bc!",
        ".unconfirmed",
        ".incomplete",
        ".ytdl",
        ".tmp~",
        ".bak",
        ".swp",
    }
)

#: Formats a finished transcript is written in. A media file sitting beside one of
#: these has already been transcribed, so the watcher does not queue it again.
_TRANSCRIPT_SUFFIXES = (".srt", ".vtt", ".txt", ".tsv", ".json")

#: Media extensions, lower-cased once. Anything else in the folder is not a source.
_MEDIA_SUFFIXES = frozenset(ext.lower() for ext in MEDIA_EXTS)

#: Sensible range for the settling window, in seconds.
MIN_SETTLE_SECONDS = 2
MAX_SETTLE_SECONDS = 120
DEFAULT_SETTLE_SECONDS = 8

#: How often the folder is rescanned. Cheap (a stat per candidate) but there is no
#: reason to run it faster than a settling window can elapse.
DEFAULT_POLL_MS = 2000


def is_temporary(path: PurePath) -> bool:
    """True when the name itself says the file is still being written.

    Checked against every trailing suffix, not just the last one, because
    downloaders nest them (``video.mp4.part``, ``video.mp4.crdownload``).
    """
    return any(suffix.lower() in TEMP_SUFFIXES for suffix in path.suffixes)


def is_locked(path: Path) -> bool:
    """True when another process holds ``path`` open.

    On Windows a rename of a file that someone else has open raises
    ``PermissionError`` — the same signal Explorer shows as "the file is in use" —
    so an atomic same-directory rename *to the same name* is a reliable probe. It
    is also the only one that works without knowing which process to ask, and it
    mutates nothing: Windows will not rename a file it cannot take exclusively, and
    a rename that succeeds leaves the file exactly where it was.

    Elsewhere ``os.rename`` succeeds regardless of open handles, so this raises the
    bar instead: if the file cannot even be opened for writing, treat it as busy.
    """
    if not path.exists():
        return True  # vanished mid-check: caller re-scans later
    try:
        path.rename(path)
        return False
    except PermissionError:
        return True
    except OSError:
        # Anything else (a network share refusing a self-rename, an odd filesystem)
        # is not evidence of a writer, so fall back to the open-for-write test
        # rather than permanently blocking a file we could have transcribed.
        try:
            with path.open("ab"):
                return False
        except OSError:
            return True


class FolderWatcher(QObject):
    """Polls one folder and emits each media file once it has finished arriving.

    Deliberately dumb about what happens next: it reports ready files through
    ``file_ready`` and knows nothing about queues, configs or the model. That keeps
    the completion rules testable on their own, with no Qt event loop and no GPU.
    """

    #: A file that passed every completeness check. Emitted once per file, ever.
    file_ready = Signal(Path)
    #: Human-readable progress for the Activity log (level, text).
    message = Signal(str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        now: Callable[[], float] = time.monotonic,
        settle_seconds: int = DEFAULT_SETTLE_SECONDS,
        poll_ms: int = DEFAULT_POLL_MS,
    ) -> None:
        super().__init__(parent)
        self._now = now
        self.settle_seconds = settle_seconds
        self.interval_ms = poll_ms
        self.folder: Path | None = None
        self._known: set[str] = set()
        self._settling: dict[str, tuple[tuple[int, float], float]] = {}
        self._noted_backlog = False

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.scan)

    # -- lifecycle -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self, folder: Path, settle_seconds: int | None = None) -> bool:
        """Begin watching ``folder``. False when it is missing or not a folder."""
        folder = Path(folder)
        if not folder.is_dir():
            self.message.emit("warning", f"cannot watch {folder} - it is not a folder")
            return False
        if settle_seconds is not None:
            self.settle_seconds = settle_seconds

        changed = self.folder is None or self.folder.resolve() != folder.resolve()
        self.folder = folder
        if changed:
            self._known.clear()
            self._settling.clear()
            self._noted_backlog = False
        self._timer.setInterval(self.interval_ms)
        self._timer.start()
        self.message.emit(
            "info",
            f"watching {folder} - new files are transcribed once they stop changing ({self.settle_seconds}s)",
        )
        # Scan at once so a file that landed before Start is picked up without
        # waiting a whole poll interval.
        self.scan()
        return True

    def stop(self) -> None:
        # Clear the settling windows whether or not the timer was running: a
        # watcher that was stopped (or never started) must not resume with
        # half-finished windows left over from a previous folder.
        self._settling.clear()
        if not self._timer.isActive():
            return
        self._timer.stop()
        self.message.emit("info", "stopped watching for new files")

    def set_interval(self, poll_ms: int) -> None:
        self.interval_ms = poll_ms
        self._timer.setInterval(poll_ms)

    # -- discovery -----------------------------------------------------------

    def scan(self) -> list[Path]:
        """One pass: return the files that became ready, and emit for each.

        Public and side-effecting so the tests can drive it directly, without a Qt
        event loop and without waiting for real time to pass.
        """
        if self.folder is None:
            return []
        ready: list[Path] = []
        candidates = self._candidates()
        if candidates and not self._noted_backlog:
            # Say so plainly: pointing the watcher at a folder that already holds
            # media queues that media. Surprising the user with 40 transcriptions
            # would be worse than a line in the log.
            self._noted_backlog = True
            self.message.emit(
                "info",
                f"{len(candidates)} file(s) already in the folder will be transcribed too",
            )
        for path in candidates:
            if self._is_settled(path):
                self._known.add(str(path))
                self._settling.pop(str(path), None)
                ready.append(path)
        for path in ready:
            self.message.emit("success", f"new file ready: {path.name}")
            self.file_ready.emit(path)
        return ready

    def _candidates(self) -> list[Path]:
        """Media files in the folder that are neither known nor obviously partial.

        A file written by this app can never appear here: transcripts are written
        as ``.srt``/``.txt``/``.json``, which are not media extensions, so the
        watcher cannot feed itself. A *media* file that already has a transcript
        beside it is skipped for the same reason the queue skips it.
        """
        folder = self.folder
        if folder is None:
            return []
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file())
        except OSError as exc:
            self.message.emit("warning", f"cannot read {folder}: {exc}")
            return []

        found: list[Path] = []
        for path in entries:
            key = str(path)
            if key in self._known:
                continue
            if path.suffix.lower() not in _MEDIA_SUFFIXES:
                continue
            if is_temporary(path):
                continue
            if self._has_transcript(path):
                # Record it so the log is not spammed with the same note every poll.
                self._known.add(key)
                self.message.emit("info", f"{path.name}: already transcribed - not queued")
                continue
            found.append(path)
        return found

    @staticmethod
    def _has_transcript(path: Path) -> bool:
        """A transcript beside the media file means there is nothing to do."""
        return any((path.parent / f"{path.stem}{suffix}").is_file() for suffix in _TRANSCRIPT_SUFFIXES)

    def _is_settled(self, path: Path) -> bool:
        """True when the file has been quiet for the settling window and is unlocked."""
        try:
            stat = path.stat()
        except OSError:
            self._settling.pop(str(path), None)
            return False

        key = str(path)
        now = self._now()
        fingerprint = (stat.st_size, stat.st_mtime)

        previous = self._settling.get(key)
        if previous is None or previous[0] != fingerprint:
            # First sighting, or it grew since the last poll: restart the window.
            self._settling[key] = (fingerprint, now)
            return False
        if now - previous[1] < self.settle_seconds:
            return False
        if stat.st_size == 0:
            return False  # still just a placeholder
        return not is_locked(path)

    # -- introspection -------------------------------------------------------

    def settling_names(self) -> list[str]:
        """Files seen but not yet promoted, for a status line."""
        return sorted(PurePath(key).name for key in self._settling)
