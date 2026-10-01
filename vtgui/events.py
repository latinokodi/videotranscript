"""Turning core pipeline events into UI state: progress, stage and log.

Kept as a mixin so the main window stays a view: these methods only read the
payloads emitted by `vtcore` and update the widgets the window already owns.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QModelIndex

import transcribe as core


class PipelineEventsMixin:
    """Event dispatch plus the progress/stage setters it drives."""

    def on_pipeline_event(self, event: str, payload: dict) -> None:
        handler = getattr(self, f"_on_{event}", None)
        if handler is not None:
            handler(payload)

    def _on_status(self, payload: dict) -> None:
        self.stage_label.set_full_text(payload["text"])

    def _on_log(self, payload: dict) -> None:
        self.append_log(payload["text"], level=payload.get("style") or "info")

    def set_stage(self, text: str) -> None:
        self.stage_label.set_full_text(text)

    def set_file_progress(self, pct: float | None) -> None:
        """None means 'working, duration unknown' -> indeterminate bar."""
        if pct is None:
            self.file_progress.setRange(0, 0)
            self.file_pct.set_full_text("working…")
            return
        if self.file_progress.maximum() == 0:
            self.file_progress.setRange(0, 100)
        self._current_progress = max(0.0, min(100.0, pct))
        self.file_progress.setValue(round(self._current_progress))
        self.file_pct.set_full_text(f"{self._current_progress:.0f}%")

    def set_queue_progress(self, pct: float) -> None:
        value = max(0, min(100, round(pct)))
        self.overall_progress.setValue(value)
        self.queue_pct.set_full_text(f"{value}% of queue")

    def set_folder_progress(self) -> None:
        self.file_progress.setRange(0, 0)

    def _on_stage(self, payload: dict) -> None:
        name = payload.get("name", "")
        label = payload.get("label") or name.title()
        self.set_stage(label)
        self._stage_name = name
        self._stage_started_at = time.time()
        if name == "load":
            self.set_folder_progress()  # loading into VRAM has no measurable percentage

    def _on_download(self, payload: dict) -> None:
        pct = payload.get("pct")
        total = payload.get("total_bytes") or 0
        self.set_stage(f"Downloading {payload.get('model', 'model')}")
        self._stage_name = "download"
        self.set_file_progress(pct)
        if pct is not None and total:
            done_gb = (payload.get("done_bytes") or 0) / 1024**3
            total_gb = total / 1024**3
            self._download_detail = f"{done_gb:.2f}/{total_gb:.2f} GB"
        else:
            self._download_detail = ""

    def _on_decode(self, payload: dict) -> None:
        if self._stage_name == "download":
            return  # download still finishing; keep its percentage on screen
        self.set_stage("Reading audio")
        self.set_file_progress(payload.get("pct"))

    def _on_model_ready(self, payload: dict) -> None:
        if payload.get("cached"):
            self.append_log("model reused from cache", level="info")
        else:
            self.append_log(
                f"{payload['device']} / {payload['compute']} ready in {payload['seconds']}s",
                level="success",
            )
        self.set_file_progress(100.0)
        self._stage_name = "load"
        self._download_detail = ""
        self._refresh_chips()

    def _on_file_start(self, payload: dict) -> None:
        self._file_started_at = time.time()
        self._file_duration = 0.0
        self._current_progress = 0.0
        self._stage_name = "idle"
        self._download_detail = ""
        self.file_progress.setRange(0, 100)
        self.set_file_progress(0)
        self.set_stage(f"{payload['name']}  ({payload['index']}/{payload['total']})")
        row = self.model.row_of(payload["path"])
        if row >= 0:
            # The row was already claimed as Running when it was pulled off the
            # queue; this only re-asserts it for a caller that skipped that step.
            self.model.update_item(row, status="Running", result="", note="")
        self.table.scrollTo(self.model.index(row, 0) if row >= 0 else QModelIndex())

    def _on_segment(self, payload: dict) -> None:
        self._file_duration = payload.get("duration") or 0.0
        if self._file_duration:
            self.set_stage("Transcribing")
            self.set_file_progress(payload["end"] / self._file_duration * 100)

    def _on_file_done(self, payload: dict) -> None:
        meta = payload["meta"]
        row = self.model.row_of(meta["source_path"])
        if row >= 0:
            self.model.update_item(
                row,
                status="Done",
                result=f"{meta['cue_count']} cues · {payload['speed']:.1f}×",
                outputs=list(payload["files"]),
                note="",
            )
        if payload["files"]:
            self._last_outdir = Path(payload["files"][0]).parent
        self._done_files += 1
        self.set_stage("Writing transcripts")
        self.set_file_progress(100.0)
        self.set_queue_progress(self._done_files / max(1, self._total_files) * 100)
        self.append_log(
            f"{payload['name']}: {meta['language']} · {meta['duration_hms']} · "
            f"{meta['cue_count']} cues · {payload['speed']:.1f}x realtime",
            level="success",
        )
        for written in payload["files"]:
            self.append_log(f"  wrote {written}", level="muted")
        self.statusBar().showMessage(f"{payload['name']} done", 5000)

    def _on_file_skipped(self, payload: dict) -> None:
        """A transcript was already on disk, so the file was never decoded."""
        files = list(payload.get("files") or [])
        row = self.model.row_of(payload.get("path", ""))
        if row >= 0:
            self.model.update_item(
                row,
                status="Skipped",
                result=f"already transcribed · {len(files)} file(s)",
                outputs=files,
                note="Transcript already exists - re-run by removing the row and adding it again.",
            )
        if files:
            self._last_outdir = Path(files[0]).parent
        self._done_files += 1
        self.set_stage("Skipped - transcript already exists")
        self.set_queue_progress(self._done_files / max(1, self._total_files) * 100)
        self.append_log(f"{payload['name']}: transcript already exists - skipped", level="warning")
        for found in files:
            self.append_log(f"  found {found}", level="muted")
        self.statusBar().showMessage(f"{payload['name']} already transcribed - skipped", 5000)

    def _on_file_failed(self, payload: dict) -> None:
        row = self.model.row_of(payload.get("path", ""))
        if row >= 0:
            self.model.update_item(row, status="Failed", result=payload["error"][:60])
        self.append_log(f"{payload['name']} failed - {payload['error']}", level="error")
        self.statusBar().showMessage(f"{payload['name']} failed", 8000)

    def _on_batch_done(self, payload: dict) -> None:
        cancelled = payload.get("cancelled")
        skipped = payload.get("skipped", 0)
        self.file_progress.setRange(0, 100)
        if cancelled:
            self.set_stage("Stopped")
            self.append_log("stopped", level="warning")
            self.statusBar().showMessage("Stopped", 5000)
        elif payload.get("failures"):
            self.set_stage("Finished with errors")
            self.append_log(f"finished with {payload['failures']} failure(s)", level="error")
        else:
            self.set_stage("Finished")
            self.set_queue_progress(100)
            summary = f"finished - {payload['completed']} transcript(s) written"
            if skipped:
                summary += f", {skipped} already existed and were skipped"
            self.append_log(summary, level="success")
            self.statusBar().showMessage(
                f"{skipped} file(s) skipped - transcripts already existed"
                if skipped and not payload["completed"]
                else "All files transcribed",
                6000,
            )

    def _on_loop_detected(self, payload: dict) -> None:
        """Whisper repeated itself; the guard already dropped the repeats."""
        where = core.human_time(payload["at"]) if payload.get("at") is not None else "somewhere"
        removed = payload.get("removed", 0)
        self.set_stage("Repeated text removed")
        self.statusBar().showMessage(
            f"Decoder loop around {where} - {removed} repeated cue(s) dropped", 15000
        )
        self.warn(
            "Repetition detected",
            "The model started repeating itself and the repeated cues were dropped.\n\n"
            f"Loop began around {where}; {removed} cue(s) removed.\n\n"
            "If the audio after that point matters, re-run this file with "
            "\u201cUse previous text as context\u201d turned off in Advanced settings.",
        )

    def _on_fatal(self, payload: dict) -> None:
        self.append_log(payload["error"], level="error")
        self.warn("Transcription failed", payload["error"])

    def _on_worker_exit(self, _payload: dict) -> None:
        self.set_stage("Idle")
        self.file_progress.setRange(0, 100)
        self._stage_name = "idle"
        self._download_detail = ""
        self._refresh_actions()
        self.path_input.setFocus()

    def _tick(self) -> None:
        """Cheap heartbeat: stage timing, throughput and ETA while work runs."""
        if not self.is_running:
            return
        downloading = self._stage_name == "download"
        elapsed = time.time() - (self._stage_started_at if downloading else self._file_started_at)
        parts = [f"{self._done_files}/{self._total_files} files"]
        if downloading and self._download_detail:
            parts.append(self._download_detail)
        parts.append(f"{elapsed:0.0f}s elapsed")

        if self.file_progress.maximum() > 0 and 2 < self._current_progress < 100:
            speed = (self._file_duration * self._current_progress / 100) / elapsed if elapsed else 0
            if speed > 0:
                parts.append(f"{speed:.1f}× realtime")
            if self._current_progress > 5:
                remaining = elapsed * (100 - self._current_progress) / self._current_progress
                parts.append(f"~{core.human_time(remaining)} left")
        self.meta_label.set_full_text(" · ".join(parts))
