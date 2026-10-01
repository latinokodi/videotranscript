# videotranscript

A desktop app that turns the audio of a video into a **transcript with accurate timestamps** — 100% locally, on your GPU. Paste a path, press Start, get `.srt` + `.txt` + `.json`. No cloud, no API keys, no per-minute fees.

```
video ─▶ ffmpeg (16 kHz mono) ─▶ faster-whisper on CUDA ─▶ word timestamps ─▶ cues ─▶ .srt/.vtt/.txt/.json
```

## Quick start

```powershell
cd F:\PyApps\videotranscript
uv sync                 # once: creates .venv and installs Python, Qt and the CUDA wheels
uv run python gui.py    # launch the app
```

Or **double-click `start.bat`** — it checks for `uv`, builds the environment on first run, and opens the app. You can also drag a video file onto `start.bat` to queue it at launch.

## Using the app

The window **opens full screen** and the whole layout is arranged to fit a
maximised screen with **no scrolling** — there is no scroll container in the
settings column at all, so nothing can hide below a fold. Quality and Output sit
side by side because the column's problem is height, not width; the Activity log
takes whatever slack is left, so the options never absorb it. If you restore the
window to a small size, the log is the first thing to shrink.

1. **Add a video** — paste a path into the box and press `Enter`, or use **Browse…**. Several paths at once work (one per line or `"a.mp4" "b.mkv"`), and a folder is scanned for media files.
2. **Press Start** (`F5`). The queue shows each file's status, and the footer tracks every waiting step.
3. Results land next to each video (`talk.srt`, `talk.txt`, `talk.json`) unless you pick a custom folder.

### It already knows what is done

A file whose transcript is already on disk is **not transcribed again**. The check
runs the moment the path is added, so the answer is on screen before the GPU does
any work:

* the row goes straight to a **Skipped** pill with `already transcribed · 3 file(s)`,
* the Activity log names every transcript it found,
* the status bar says how many files were skipped.

Only the formats you have selected count, and they are looked for in the folder the
run would actually write to — so a `.srt` sitting beside the video is real evidence
when output goes next to the video, and is not evidence when you picked a custom
folder. Detection is done **twice on purpose**: once on add (the notification) and
once inside the pipeline (the guarantee, in case a transcript appeared while the
file waited in the queue).

To transcribe such a file anyway, select the row, press **Remove**, and add it
again — or tick **Overwrite existing transcripts**. A finished row is never
re-sent through the model just because another file was added, which matters
because adding a file auto-starts the queue.

### The queue runs itself

Adding a file while a run is in progress no longer needs a second **Start**. The
batch **pulls** from the queue after every file, so anything waiting — including
something you add mid-run — starts the moment one finishes. Because the same
worker keeps pulling, the model that was loaded for the first file stays resident
for the whole session instead of being reloaded per file.

`Start` only runs files that still need it, and `Esc` still stops cleanly after
the file being processed.

### Parallel files

**Transcribe at once** (1, 2 or 3) overlaps files instead of running them one
after another. It is off by default because it is a **VRAM budget, not a free
speed dial**: the workers share the one loaded model, but each needs its own CUDA
workspace and its own 16 kHz temp WAV. With `large-v3` resident that is roughly
1 GB or more per extra job on top of the model's ~3.8 GB, so on a 12 GB card
3 jobs is near the ceiling, and a long file pair can push it over. Watch the
**model in memory** chip and the free VRAM before raising it; if a job fails for
lack of memory, drop back to 1.

### Auto-transcribe a folder

Point the app at the folder your downloads land in and it transcribes files as
they arrive — **Start watching** in the *Auto-transcribe* card.

The hard part is not noticing a new file, it is knowing when that file has
*finished arriving*. A download appears at zero bytes and grows, a resumable one
leaves a differently-named temporary behind, and a file that has stopped growing
may still be held open by the program writing it. Transcribing any of those
decodes half a file into a garbage transcript that then looks like real work in
the queue.

So a file is queued only once it clears **three independent checks**:

| Check | What it rules out |
|---|---|
| **Not a temporary name** | `.part`, `.crdownload`, `.partial`, `.tmp`, `.opdownload`, `.!qb`, `.bc!`, `.aria2`, `.unconfirmed`, `.download`, `.incomplete` … ignored outright, whatever their size |
| **Quiet for the settling window** | Size *and* modification time unchanged across the window (default 8 s, adjustable 2–120 s) — sampled on every poll, not one long sleep |
| **Unlocked** | An atomic same-directory rename proves no other process holds the file open. This is what makes a stalled or cancelled download safe |

The quiet window alone is fooled by a download that stalls but keeps the file
locked; the lock test alone is fooled by a writer that lets go of the handle
between chunks. Requiring **both** only ever costs a few seconds of latency.

A watched file is then treated exactly like a pasted one: same duplicate check,
same destination, same auto-start, and the same *already transcribed* skip — so a
folder you have transcribed before is not done again. Media already sitting in
the folder when you start are transcribed too (the log says how many before it
begins), non-media is ignored, and so are the transcripts the app itself writes
there — the watcher cannot feed itself. New files keep the queue fed while a run
is in progress, because the batch pulls each one as the GPU frees up. The folder,
the settling window and whether monitoring was on are all remembered, so
reopening the app resumes watching.

### Progress you can see

Every waiting step reports a stage, a bar and a percentage:

| Step | What is shown |
|---|---|
| Fetching the model | `Downloading large-v3` + % and `1.95/3.09 GB` + ETA |
| Loading into the GPU | `Loading large-v3 into cuda` + animated bar (no measurable %) |
| Reading audio | `Reading audio` + real % parsed from ffmpeg's progress stream |
| Transcribing | `Transcribing` + real % from decoded segments, plus `×realtime` and ETA |
| Writing | `Writing transcripts` + 100% |
| Whole queue | `% of queue`, files done, elapsed and ETA |

Percentages are shown *beside* the bars rather than inside them — text painted over the accent fill would fall below the contrast floor.

**The footer never moves.** Every label that shows changing text is pinned to its own worst-case width, measured with the font actually in use at startup, and the two bars are pinned as well — the *stage* label is the single cell that flexes when the window is resized, so nothing in the row shifts just because a number changed or the window changed size. The window cannot be made narrower than the footer needs, because a row squeezed onto its minimums is a row that visibly moves.

### Keys

| Key | Action |
|---|---|
| `F5` | start the queue |
| `Esc` | stop after the current file |
| `Del` | remove selected rows |
| `Ctrl+O` / `Ctrl+Shift+O` | add files / add a folder |
| `Ctrl+E` | open the output folder |
| `Ctrl+L` | clear the log |
| `F1` | quick start |
| `Ctrl+Q` | quit |

### Options

**Quality presets** set the model and beam size together — *Best* (large-v3, beam 5), *Balanced* (turbo, beam 5), *Fast* (turbo, beam 1), *Draft* (tiny). Choosing a model by hand switches the preset to *Custom*. Also in-app: language (auto or 17 codes), device (auto/CUDA/CPU), **Transcribe at once** (1/2/3 files), output formats (`srt` `vtt` `txt` `json` `tsv` `rttm`), destination, overwrite, **Label speakers**, **Unload model from memory**, and an **Advanced settings…** dialog (beam size, line width, cue length, pause break, jargon hint, **custom vocabulary**, **misheard-word fixes**, spacing tidy-up, VAD, context, and the three **loop defences**).

### Speaker labels

Tick **Label speakers (who said what)** and every cue is prefixed `Speaker 1:`, `Speaker 2:`, … in the SRT, VTT, TXT and JSON, and a standard `.rttm` of the speaker turns is written for scoring tools.

It is **fully offline and needs no Hugging Face token or account.** Two small ONNX models from a public re-export of pyannote's `speaker-diarization-community-1` pipeline run on the **CPU** at about **4% of realtime** — roughly 35 seconds per 15-minute video, against about 1 minute 45 for the transcription itself. No PyTorch, no GPU contention with Whisper, and nothing to download beyond ~65 MB of models.

| Setting | Notes |
|---|---|
| **Detect automatically** | Works the cast out from the audio. Verified: exactly 3 speakers on a 3-voice ground-truth recording, 1 on a single-narrator clip |
| **Exactly N speakers** | **Use this whenever you know the answer.** On a real two-person interview, automatic detection proposed 3 while forcing 2 produced a perfectly balanced 85-turns-each split of host and guest. Naming the cast is not a fallback, it is the better setting |

Two details worth knowing, both measured on Windows-TTS ground truth (12 alternating turns, three voices):

* Labels go on the **first line** of a cue only, so subtitle line lengths and wrapping are exactly what they were before labels existed.
* A span too short to prove who is talking is matched against the **voices already established** rather than guessed from the nearest neighbour in time — which is what keeps brisk back-and-forth dialogue attributed correctly instead of collapsing to one speaker.
* Ground truth result: **12/12 turns correct, exactly 3 speakers found**, boundaries within a tenth of a second.


### GPU memory

`large-v3` holds about **3.8 GB** of VRAM while it is loaded, and the model is kept resident after a run so the next file starts instantly (13.4 s cold → 0.01 s warm). The header chip shows whether anything is resident.

* **Unload model from memory** gives that memory back on demand. It is greyed out when nothing is loaded and while a file is being transcribed — pulling the model out from under a running worker would crash it.
* **Closing the window unloads the model.** If you close while a file is still finishing, the unload is skipped deliberately: cancellation is only polled between files, so the worker may still be inside the model. The process is ending anyway and the driver reclaims the VRAM then.
* **A killed process needs no handling.** `taskkill /F` and Task Manager destroy the CUDA context and the driver returns the memory — measured: 5065 MiB → 1930 MiB (back to idle) the moment the process tree died. The Quit menu and Ctrl+C release it through `aboutToQuit`/`atexit` guards instead.

Verified on this machine with `nvidia-smi`: 6057 MiB with `large-v3` resident → **2311 MiB after clicking Unload** (and the same for closing the window), i.e. the full 3.8 GB returned.

> `start.bat` launches the app **detached** rather than through `uv run`, because Windows does not cascade kills: closing a launcher console used to leave `uv.exe` plus two `pythonw.exe` processes alive holding ~4 GB with no window to close. Detached, there is nothing left to strand.

> **No console windows flash while it works.** The app runs under `pythonw` with no console of its own, so Windows hands every console child — ffmpeg, ffprobe, nvidia-smi, the clipboard helper — a brand-new console window that appears and vanishes. All of them are now spawned through `vtcore.runtime.quiet_subprocess()`, which passes `CREATE_NO_WINDOW`, so the tools run invisibly. If you ever see a burst of command windows, that is this: one per spawned tool, not a crash.

**Everything is remembered**, and written back within a second of any change (not just on exit):

| Remembered | Notes |
|---|---|
| **Last folder browsed** | **Browse…** and the output-folder picker reopen where you last were |
| Model, preset, language, device | restored exactly, including *Custom* presets |
| How many files transcribe at once | |
| Output formats, destination folder, overwrite | |
| Advanced settings | all of them, including the loop defences |
| Window geometry, splitter position, theme | the app still *opens* maximised |
| Custom vocabulary and misheard-word fixes | so a series keeps its spellings |
| **Watched folder**, settling window, watching on/off | so reopening the app resumes monitoring |

The **source box always starts empty**. Only the folder is remembered — restoring the last *filename* invited re-queueing the previous file by accident.

## Getting names and jargon right

Whisper is near-perfect on ordinary sentences and *consistently wrong on proper nouns*. On an 8-minute crypto interview, a plain `large-v3` run at 11× realtime produced these errors, and every one of them is a real word that merely sounds right:

| Heard | Should be |
|---|---|
| `Balanchine` | balance sheet |
| `Besson` / `best` | Bessent |
| `GPF` | GPIF |
| `Athena` | Ethena |

Two settings fix this, and they are complementary:

**1. Custom vocabulary (`--hotwords`, "Spell these right" in the app).** A comma-separated list of names and jargon. This is fed to Whisper as decode-time *hotwords*, so the decoder prefers those spellings instead of the nearest common word. Measured on that clip:

| Configuration | Wrong terms | Correct terms | Wall time |
|---|---|---|---|
| defaults | 5 | 3 | 41.8 s |
| **+ `--hotwords`** | **1** | **9** | 46.3 s |
| `patience=2` | 5 | 3 | 43.2 s |
| `--hotwords` + `patience=2` | 1 | 9 | 48.2 s |

Hotwords fixed 4 of the 5 errors for about **10% more decode time**. `patience` was measured too and changed *nothing*, so it is deliberately not offered. Prefer the words the speaker actually says, and include the ones you know recur.

**2. Misheard-word fixes (`--fix wrong=right`, "Fix misheard words" in the app).** Whatever biasing does not catch is corrected after decoding, one rule per line. Matching is case-insensitive, anchored on word boundaries, and applied in a **single pass** so a short key can never rewrite text a longer key just produced (`balance sheet` → `balance-sheet` → `BAL-sheet` is impossible). It counts what it changed into the JSON metadata (`corrections_applied`), so a run is auditable.

**3. Spacing tidy-up (on by default).** Whisper writes numbers oddly — `75 %`, `5 ,000`, `Euro -Yen`. Those are corrected to `75%`, `5,000`, `Euro-Yen`. The rules only touch a space sitting against punctuation or a digit, so ordinary text is never re-flowed, and a spaced dash used as punctuation (`altcoins - like Ethereum`) is left alone. Turn it off with `--no-normalise` to keep the raw decode.

Combining all three on the interview took the four error terms to **zero** with no wording regressions. One honest caveat: at one site hotwords changed the mistake rather than fixing it (`Balanchine` became `balancing`, which is still wrong) — that is what the `--fix` list is for, and it is why the vocabulary is a setting rather than something baked in.

> These are the *only* accuracy levers measured to matter here. Model choice and `patience` did not help; the temperature ladder, VAD, decode penalties and the loop guard (below) are what keep the transcript from breaking.

## Model guide (measured on this machine)

| Model | VRAM | Speed | When |
|---|---|---|---|
| `large-v3` (default) | **~4.2 GB** | 11× realtime on an RTX 3060 | best accuracy |
| `large-v3-turbo` | ~2.5 GB | ~3.5–5× faster | long files |
| `distil-large-v3` | ~2.5 GB | fastest large-class | English only |
| `large-v2` | ~4 GB | 11× | occasionally steadier than v3 on messy audio |
| `medium.en` / `small.en` | 1–2 GB | fast | English-only, noticeably fewer hallucinations than the multilingual equivalents |
| `medium` / `small` / `base` / `tiny` | 0.5–2 GB | very fast | drafts |

The loaded model stays resident, so a second file starts immediately: cold load 13.4 s → **0.01 s** warm.

> **Model choice does not fix decoder loops.** A repetition loop is a decoding pathology, not a capability gap — a bigger or smaller model does not remove it (in fact large-v3 is the *most* prone on silent or noisy stretches). What fixes it is the decoding settings below. Trying `medium.en` is a reasonable experiment, but the deterministic fixes are the temperature ladder, the decode-time penalties and the loop guard.

## How the timestamps stay accurate

1. ffmpeg decodes to 16 kHz mono WAV — no resampling surprises.
2. Whisper runs with `word_timestamps=True`. The first pass is `temperature=0.0` (deterministic); if a segment comes out degenerate, the **temperature ladder** (`0.0 → 1.0`) retries it. That ladder is Whisper's built-in escape from decoder loops — pinning the temperature to `0.0` disables it.
3. Words are grouped into cues using their **real** timestamps; nothing is stretched or padded.
4. Breaks prefer natural boundaries — sentence end, comma, or a pause ≥ `gap-break` — so a length limit never produces `…can do for / you, ask…`.
5. VAD is on by default, the main defence against hallucination over silence and music.
6. The **loop guard** collapses and reports any repetition that still gets through (see below).

### Decoder loops

Whisper sometimes locks up and repeats one sentence for the rest of a file, emitting thousands of near-identical cues with sub-100 ms durations. Two layers defend against it:

| Layer | What it does |
|---|---|
| **Repetition penalty** (`1.1`) | the decoder is penalised for reusing tokens, so a loop is much less likely to form |
| **No-repeat n-gram** (`4`) | the decoder may not emit the same 4-word sequence twice — this blocks a loop outright |
| **Temperature ladder** | a degenerate segment (`compression_ratio` / `log_prob`) is retried at a higher temperature |
| **Loop guard** | after transcription, repeated cycles, over-used long cue texts, drifting near-duplicates and stranded fragments are dropped — and reported in the log, on screen, and in the JSON `meta` (`loops_removed`, `loop_started_at`) |

The first three work during decoding and prevent the loop; the last repairs anything that still slips through. The guard only ever removes *repetition*: a clean transcript passes through untouched, and short backchannel lines like “Yeah.” are deliberately exempt.

All three decode-time defences are toggles in **Advanced settings → Loop defences** (`--repetition-penalty`, `--no-repeat-ngram-size`, `--no-loop-guard` on the CLI). Turn off “Block repeated word sequences” if your material genuinely repeats phrases verbatim.

## Architecture

The two entry points are thin **facades**; everything else is a package of focused
modules, so no file is a dumping ground and the dependency direction is one-way.

```
transcribe.py   facade -> vtcore          gui.py   facade -> vtgui
```

| `vtcore/` | Role | Depends on |
|---|---|---|
| `errors.py` | the exception hierarchy | — |
| `text.py` | timestamps, line wrapping, custom-vocabulary rules | — |
| `runtime.py` | UTF-8 streams, Windows CUDA DLL path, device/ffmpeg lookup | text |
| `media.py` | paths → 16 kHz mono WAV (ffmpeg, progress, temp cleanup) | errors, runtime, text |
| `cues.py` | `Word`/`Cue` and the cue-building rules | text |
| `postprocess.py` | decoder-loop collapsing, vocabulary corrections | cues, text |
| `diarize.py` | who spoke when: Kaldi fbank, ONNX segmentation + voiceprints, clustering | — |
| `config.py` | the single options object shared by GUI and CLI | runtime |
| `models.py` | model resolution, download, resident cache | config, errors, text |
| `writers.py` | srt / vtt / txt / tsv / json, plus where they go and whether they are already there | config, cues, text |
| `pipeline.py` | one file and one batch through the whole flow, serially or in parallel | all of the above |
| `cli.py` | argparse and the command-line entry point | all of the above |

| `vtgui/` | Role | Depends on |
|---|---|---|
| `theme.py` | **every** colour/space/type token + the generated stylesheet | — |
| `catalog.py` | presets, models, languages, formats, defaults | — |
| `widgets.py` | shared bits: `Card`, `EmptyState`, `elbow_field`, `human_size`, clipboard | theme |
| `footer.py` | the progress bars and every number beside them — and the rule that none of them may change width | theme, widgets |
| `queue.py` | the queue table model and status pills | theme, widgets |
| `watch.py` | when is a new file actually *complete*? (no UI) | core |
| `watchcard.py` | the Auto-transcribe card: controls, status, its settings | theme, watch, widgets |
| `worker.py` | the background thread that runs the pipeline | core |
| `dialogs.py` | advanced settings | catalog, theme |
| `events.py` | turns core events into progress/stage/log state (a mixin) | core, widgets |
| `window.py` | the main window: wiring, queue actions, persistence | everything above |
| `app.py` | builds `QApplication`, installs the unload-on-exit guards, shows the window | theme, window |

| Other | Role |
|---|---|
| `start.bat` | double-click launcher: builds the environment on first run, then starts the app |
| `pyproject.toml` | dependencies, lint/format/test tool config |
| `uv.lock` | pinned dependency versions, so `start.bat` installs the same set everywhere |

Import order is a strict DAG: `errors → text → cues → postprocess → writers`, with
`runtime → config → models → pipeline → cli` alongside. Nothing imports the
facades, so there are no cycles, and `vtcore` never imports Qt.

The core emits structured events (`stage`, `download`, `decode`, `segment`, `file_done`, …) and never prints for the UI, so the same logic backs both the GUI and the CLI.

Design tokens (colour, spacing, radius, type) live in one table per theme in `vtgui/theme.py`, and the stylesheet is **generated** from them, so dark and light cannot drift apart. Both themes are verified for WCAG AA contrast.

### Adding a feature without untangling anything

Each concern owns one module, and dependencies only ever point one way:

| To change… | Touch | At most also |
|---|---|---|
| how a file is decoded or transcribed | `vtcore/pipeline.py`, `vtcore/media.py` | `vtcore/config.py` |
| a new output format | `vtcore/writers.py` | `vtcore/config.py`, the format checkbox |
| what counts as "already transcribed" | `vtcore/writers.py` (`existing_transcripts`) | — |
| when a watched file counts as complete | `vtgui/watch.py` | — |
| the Auto-transcribe controls or settings | `vtgui/watchcard.py` | — |
| what happens to a file the watcher reports | `vtgui/window.py` (`add_watched_file`) | — |
| the footer's bars, numbers or widths | `vtgui/footer.py` | — |
| a status pill or colour | `vtgui/theme.py` (`STATUS_STYLE`) | — |
| a new queue column | `vtgui/queue.py` | — |
| a new option | `vtcore/config.py` → `vtgui/catalog.py` → `vtgui/window.py` | persistence in `window.py` |

The rule that keeps it that way: `vtcore/` never imports Qt, and the widgets never
import the pipeline. Data flows in through function arguments and back out through
**Qt signals**, so a widget can be driven from a test with plain Python calls — no
event loop, no GPU. `FolderWatcher` is the model to copy: it decides *when a file
is complete* and emits `file_ready`; it has no idea a queue exists.

## CLI (optional)

```powershell
uv run python transcribe.py "D:\videos\*.mkv" --model large-v3 -o out --formats srt,vtt,json

# best accuracy on an interview: teach it the names and fix what it still misses
uv run python transcribe.py interview.mp4 `
  --hotwords "Arthur Hayes, Scott Bessent, GPIF, Ethena, balance sheet" `
  --fix "Balanchine=balance sheet" --fix "Besson=Bessent" --fix "GPF=GPIF"

# three files at once, when the GPU has the VRAM for it
uv run python transcribe.py "D:\videos\*.mkv" -j 3

uv run python transcribe.py --help
```

## Development

This repository ships the application only. The test suite, the manual-test
checklist and the screenshot tool are kept in the maintainer's working copy and
are not published, so clone-and-run needs nothing beyond `start.bat`.

```powershell
uv sync --no-dev            # runtime dependencies only (what start.bat installs)
uv sync                     # add the dev group: pytest, ruff, vulture
uv run ruff check .         # lint
uv run ruff format .        # format
uv run vulture              # dead-code scan
```

Because the app is Windows-first, `start.bat` is the supported entry point; it
builds the environment on first run and launches the GUI detached from the
console.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cudnn_ops64_9.dll is missing` | `uv sync --reinstall` — the CUDA wheels ship in the venv and the core adds them to `PATH` |
| Falls back to CPU | `uv run python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"` must print ≥ 1 |
| Model download seems stuck | The bar shows bytes fetched and an ETA; first run pulls ~3 GB for `large-v3` |
| Empty transcript | Check the file has an audio track (`ffmpeg -i file.mp4`) and set the language explicitly |
| Command windows flash while it works | Each spawned tool used to get its own console window on Windows. Fixed by `quiet_subprocess()`; if you still see them, you are running an older copy |
| **One sentence repeats for the whole file** | Decoder loop. The temperature ladder prevents most of them and the loop guard removes the rest; you get a "Repetition detected" warning with the timestamp. Re-run that file with "Use previous text as context" **off** in Advanced, and consider a smaller/faster model for the affected stretch |
| Repeated text over music | Keep VAD on; try "Use previous text as context" off in Advanced |
| Very slow | Confirm the GPU chip in the header, and pick the *Fast* preset |
| GPU still busy after a run | The model stays resident to make the next file instant. Click **Unload model from memory**, or just close the window — both return the full ~3.8 GB |
| A file was not transcribed, and the row says **Skipped** | Its transcript was already on disk. Remove the row and add it again, or tick **Overwrite existing transcripts** |
| Watching a folder does nothing | Check the *Auto-transcribe* status line, and that the file is media (not `.part`/`.crdownload`) and has stopped changing. A download that keeps a handle open is held back until it finishes |
| A half-downloaded file got transcribed | Raise **Settle** so the file must stay unchanged for longer. Nothing can detect a writer that stops, releases the file and resumes later — only a longer window helps |
| Jobs fail once you raise **Transcribe at once** | Out of VRAM: each parallel job needs its own workspace on top of the model. Go back to 1, or unload the model and use a smaller preset |
| Something else needs the GPU | Run on CPU instead (Device → CPU), or unload the model and switch to a smaller preset |

## Not included (by design)

**Speech separation** (splitting overlapping voices into separate audio tracks) and **speaker recognition across files** (matching a voice to a named person you already know). Labels are per-file only: "Speaker 1" in one video is unrelated to "Speaker 1" in another, because nothing is enrolled or stored.

## Licence

**MIT** — see [LICENSE](LICENSE). In short: use it, change it, ship it, sell it; just keep the copyright notice and the licence text with any copy or substantial portion of it. There is no warranty.

Third-party components keep their own licences and are installed from PyPI or fetched at run time rather than redistributed here:

| Component | Licence |
|---|---|
| `faster-whisper`, `ctranslate2`, `onnxruntime` | MIT |
| `huggingface-hub`, `tokenizers` | Apache-2.0 |
| `tqdm` | MPL-2.0 AND MIT |
| `PySide6` / `shiboken6` | LGPL-3.0-only (or GPL-2.0/3.0) |
| Whisper model weights | MIT (downloaded at run time) |
| `ffmpeg` | LGPL/GPL depending on the build; called as a separate process |

### Speaker-label models (attribution required)

When you turn on speaker labels, two models are downloaded at run time from
[`altunenes/speaker-diarization-community-1-onnx`](https://huggingface.co/altunenes/speaker-diarization-community-1-onnx),
an ONNX re-export of pyannote's
[`speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1).
They are **not** part of this repository and this repository does not redistribute
them, but if you publish a build or a transcript that uses them, the licence
requires attribution — the whole point of a CC-BY licence:

| Model | Licence | Required credit |
|---|---|---|
| pyannote `speaker-diarization-community-1` (segmentation + WeSpeaker embedding) | **CC-BY-4.0** | "Speaker diarization by pyannote (Hervé Bredin), CC-BY-4.0" |
| ONNX conversion by altunenes | see the model card | link to the model card |

That is a licence condition, not a courtesy: CC-BY-4.0 obliges you to name the
author of the diarization models wherever you redistribute the result. MIT on this
repository's own code does not change it.


If you ever publish a **packaged build** (an installer, or a zip containing the venv), the LGPL-3.0 terms for PySide6 start to apply: ship the LGPL text, offer the Qt sources, and keep the library replaceable.

