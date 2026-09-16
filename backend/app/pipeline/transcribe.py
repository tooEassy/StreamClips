from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .cancel import check
from ..config import settings
from ..store import Job, dump_json, format_clock, format_eta, set_stage


def _try_mlx(audio_path: Path, model: str, language: str) -> dict[str, Any] | None:
    try:
        import mlx_whisper  # type: ignore
    except Exception:
        return None
    mlx_id = {
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        "medium": "mlx-community/whisper-medium",
        "small": "mlx-community/whisper-small",
        "large-v3": "mlx-community/whisper-large-v3",
    }.get(model, f"mlx-community/whisper-{model}")
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=mlx_id,
        language=language,
        word_timestamps=True,
        verbose=False,
    )
    return result


def _faster_whisper(audio_path: Path, model: str, language: str, on_progress: Any | None) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    if on_progress:
        on_progress(0.0, "Загружаю модель Whisper — это может занять пару минут")
    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    if on_progress:
        on_progress(0.0, "Ищу речь на дорожке, потом пойдёт процент")
    segments_iter, info = whisper.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        best_of=5,
        condition_on_previous_text=False,
        no_repeat_ngram_size=3,
        hallucination_silence_threshold=2.0,
        initial_prompt=(
            "Русская разговорная речь. Стрим, Twitch, чат, игры."
        ),
    )
    duration = float(getattr(info, "duration", 0) or 0)
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    started: float | None = None
    origin = 0.0
    for seg in segments_iter:
        text = (seg.text or "").strip()
        item = {
            "start": float(seg.start or 0),
            "end": float(seg.end or 0),
            "text": text,
        }
        segments.append(item)
        if seg.words:
            for word in seg.words:
                words.append(
                    {
                        "start": float(word.start or item["start"]),
                        "end": float(word.end or item["end"]),
                        "word": (word.word or "").strip(),
                    }
                )
        if not on_progress:
            continue
        processed = item["end"]
        if started is None and processed > 0:
            started = time.monotonic()
            origin = processed
        eta = None
        if started is not None:
            elapsed = time.monotonic() - started
            audio_delta = max(0.0, processed - origin)
            if audio_delta >= 20 and elapsed >= 25:
                rate = audio_delta / elapsed
                remain = max(0.0, duration - processed)
                if rate > 0.02:
                    eta = remain / rate
        frac = min(0.99, processed / duration) if duration else 0.0
        clock = format_clock(processed)
        total = format_clock(duration) if duration else "?"
        label = f"Транскрипция {clock} / {total}"
        if eta is not None:
            label = f"{label} · осталось {format_eta(eta)}"
        on_progress(frac, label, eta, processed, duration)
    return {
        "language": language,
        "duration": duration,
        "text": " ".join(s["text"] for s in segments).strip(),
        "segments": segments,
        "words": words,
        "engine": "faster-whisper",
        "model": model,
    }


def _normalize_mlx(raw: dict[str, Any], model: str, language: str) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for seg in raw.get("segments") or []:
        segments.append(
            {
                "start": float(seg.get("start") or 0),
                "end": float(seg.get("end") or 0),
                "text": (seg.get("text") or "").strip(),
            }
        )
        for word in seg.get("words") or []:
            words.append(
                {
                    "start": float(word.get("start") or 0),
                    "end": float(word.get("end") or 0),
                    "word": (word.get("word") or word.get("text") or "").strip(),
                }
            )
    return {
        "language": language,
        "duration": float(raw.get("duration") or (segments[-1]["end"] if segments else 0)),
        "text": (raw.get("text") or "").strip(),
        "segments": segments,
        "words": words,
        "engine": "mlx-whisper",
        "model": model,
    }


def transcribe_job(job: Job, audio_path: Path) -> dict[str, Any]:
    check(job.id)
    model = (settings.whisper_model or job.settings.whisper_model).strip() or "large-v3"
    language = job.settings.language

    last_write = [0.0, -1.0]

    def on_progress(
        frac: float,
        stage: str,
        eta: float | None = None,
        processed: float | None = None,
        total: float | None = None,
    ) -> None:
        check(job.id)
        now = time.monotonic()
        due = last_write[1] < 0 or (now - last_write[0]) >= 8 or abs(frac - last_write[1]) >= 0.001
        if not due:
            return
        last_write[0] = now
        last_write[1] = frac
        set_stage(
            job,
            "transcribe",
            progress=frac,
            state="active",
            status="transcribing",
            stage=stage,
            eta_sec=eta,
            processed_sec=processed,
            total_sec=total,
        )

    raw = _try_mlx(audio_path, model, language)
    if raw is not None:
        payload = _normalize_mlx(raw, model, language)
    else:
        payload = _faster_whisper(audio_path, model, language, on_progress)
    dump_json(job.path("transcript.json"), payload)
    return payload
