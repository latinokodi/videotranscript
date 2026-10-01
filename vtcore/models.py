"""Whisper model resolution, download and the resident model cache."""

from __future__ import annotations

import contextlib
import gc
import time

from .config import Config, EventFn
from .errors import ModelLoadError

_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


_MAX_CACHED_MODELS = 1


def _release(model: object) -> bool:
    """Ask CTranslate2 to give one model's memory back right now.

    Dropping the last Python reference normally frees a model, but a lingering
    reference — a worker still inside a file, a traceback, a logged payload —
    keeps the memory pinned. Measured on this machine with large-v3:
    `clear_model_cache()` alone left 3.7 GB of VRAM resident, while CTranslate2's
    own `unload_model()` actually returned it. Returns whether the native call ran.
    """
    inner = getattr(model, "model", model)
    unload = getattr(inner, "unload_model", None)
    if not callable(unload):
        return False
    with contextlib.suppress(Exception):
        unload()
        return True
    return False


def unload_model() -> int:
    """Free every resident model and empty the cache.

    Returns how many models were released from memory. Idempotent: calling it
    when nothing is loaded returns 0. This is what the UI's "unload" action and
    the window's close path call, so closing the app never leaves the GPU holding
    weights it no longer needs.
    """
    models = list(_MODEL_CACHE.values())
    if not models:
        return 0
    for model in models:
        _release(model)
    _MODEL_CACHE.clear()
    # The cache normally held the only reference; collect cycles so the Python
    # wrappers go too and nothing keeps the released weights reachable.
    gc.collect()
    return len(models)


def model_cache_state() -> tuple[int, int]:
    """Return ``(models resident, cache limit)`` for status displays.

    Callers should use this rather than reading the module's private cache, so
    the eviction policy stays an implementation detail of this module.
    """
    return len(_MODEL_CACHE), _MAX_CACHED_MODELS


def hf_repo_id(model: str) -> str:
    """Map a short model name (`large-v3`) to its Hugging Face repo id."""
    with contextlib.suppress(Exception):
        from faster_whisper.utils import _MODELS  # type: ignore[attr-defined]

        if model in _MODELS:
            return str(_MODELS[model])
    return model


def ensure_model_downloaded(model: str, on_event: EventFn | None = None) -> bool:
    """Fetch the model files up front so a real download percentage can be shown.

    Returns False when progress cannot be reported (offline, API error, or an
    unknown repo), so the caller can fall back to an indeterminate indicator.
    Already-cached models short-circuit almost immediately.
    """

    def emit(event: str, **payload) -> None:
        if on_event:
            on_event(event, payload)

    try:
        from huggingface_hub import HfApi, hf_hub_download
        from tqdm.auto import tqdm as base_tqdm
    except ImportError:
        return False

    repo = hf_repo_id(model)
    try:
        info = HfApi().model_info(repo, files_metadata=True)
        files = [
            (sibling.rfilename, int(sibling.size or 0))
            for sibling in (info.siblings or [])
            if sibling.rfilename and not sibling.rfilename.endswith((".md", ".gitattributes"))
        ]
    except Exception:
        return False

    total_bytes = sum(size for _name, size in files)
    if not files or total_bytes <= 0:
        return False

    emit("download", pct=0.0, done_bytes=0, total_bytes=total_bytes, model=model)
    completed = 0

    class _ProgressTqdm(base_tqdm):  # type: ignore[misc, valid-type]
        """Reports aggregate progress across the files downloaded so far."""

        def __init__(self, *args, **kwargs) -> None:
            kwargs.setdefault("disable", True)
            super().__init__(*args, **kwargs)

        def update(self, n: int | float = 1):
            result = super().update(n)
            current = completed + int(self.n or 0)
            emit(
                "download",
                pct=min(99.0, current / total_bytes * 100),
                done_bytes=current,
                total_bytes=total_bytes,
                model=model,
            )
            return result

    for name, size in files:
        try:
            hf_hub_download(repo, name, tqdm_class=_ProgressTqdm)
        except TypeError:
            # Older huggingface_hub without tqdm_class: download silently.
            hf_hub_download(repo, name)
        except Exception as exc:
            emit("log", text=f"! model download failed ({exc})", style="warning")
            return False
        completed += size

    emit("download", pct=100.0, done_bytes=total_bytes, total_bytes=total_bytes, model=model)
    return True


def load_model(cfg: Config, on_event: EventFn | None = None):
    """Return a WhisperModel, reusing a cached one and falling back to CPU."""
    from faster_whisper import WhisperModel  # imported late so the DLL bootstrap wins

    def emit(event: str, **payload) -> None:
        if on_event:
            on_event(event, payload)

    cfg.resolve_runtime()
    key = (cfg.model, cfg.resolved_device, cfg.resolved_compute)

    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        emit(
            "log",
            text=f"reusing loaded model: {cfg.model} ({cfg.resolved_device}/{cfg.resolved_compute})",
        )
        emit(
            "model_ready",
            seconds=0.0,
            device=cfg.resolved_device,
            compute=cfg.resolved_compute,
            cached=True,
        )
        return cached

    emit(
        "log",
        text=f"Model: {cfg.model}  |  device: {cfg.resolved_device}  |  compute: {cfg.resolved_compute}",
    )
    emit("status", text=f"Loading {cfg.model} (first run downloads it)...")
    emit("stage", name="download", label=f"Checking {cfg.model} files")
    have_progress = ensure_model_downloaded(cfg.model, on_event)
    if not have_progress:
        emit("download", pct=None, done_bytes=0, total_bytes=0, model=cfg.model)

    emit("stage", name="load", label=f"Loading {cfg.model} into {cfg.resolved_device}")
    started = time.time()
    try:
        model = WhisperModel(cfg.model, device=cfg.resolved_device, compute_type=cfg.resolved_compute)
    except Exception as exc:
        emit("log", text=f"! GPU load failed ({exc.__class__.__name__}: {exc})", style="error")
        if cfg.device == "cuda":
            raise ModelLoadError(f"could not load {cfg.model} on CUDA: {exc}") from exc
        emit("log", text="  retrying on CPU...", style="warning")
        cfg.resolved_device, cfg.resolved_compute = "cpu", "int8"
        key = (cfg.model, cfg.resolved_device, cfg.resolved_compute)
        try:
            model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
        except Exception as cpu_exc:
            raise ModelLoadError(f"could not load {cfg.model} on CPU either: {cpu_exc}") from cpu_exc

    # Evict before inserting: release the outgoing model rather than just
    # dropping it, or its weights stay in VRAM while the new one loads.
    while len(_MODEL_CACHE) >= _MAX_CACHED_MODELS:
        stale_key = next(iter(_MODEL_CACHE))
        if _release(_MODEL_CACHE.pop(stale_key)):
            emit("log", text=f"freed {stale_key[0]} to make room for {cfg.model}")
    _MODEL_CACHE[key] = model

    emit(
        "model_ready",
        seconds=round(time.time() - started, 2),
        device=cfg.resolved_device,
        compute=cfg.resolved_compute,
        cached=False,
    )
    return model
