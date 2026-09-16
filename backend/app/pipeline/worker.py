from __future__ import annotations

import queue
import threading
import traceback

from ..store import Job, load_job, load_json, save_job, set_stage, update_job
from .analyze import analyze_job
from .audio import compute_energy, extract_audio
from .cancel import JobCancelled, check, is_cancelled
from .ingest import download_vod, ingest_local_file, probe_duration, source_path
from .render import render_moment, write_clips_meta
from .transcribe import transcribe_job


_queue: queue.Queue[tuple[str, str, list[str] | None]] = queue.Queue()
_started = False


def start_worker() -> None:
    global _started
    if _started:
        return
    thread = threading.Thread(target=_loop, daemon=True, name="clip-worker")
    thread.start()
    _started = True


def enqueue_process(job_id: str) -> None:
    _queue.put(("process", job_id, None))


def enqueue_render(job_id: str, moment_ids: list[str]) -> None:
    _queue.put(("render", job_id, moment_ids))


def _words_for_moment(moment, transcript_words: list[dict]) -> list[dict]:
    if moment.captions_edited:
        return [
            {"word": item.word, "start": item.start, "end": item.end}
            for item in moment.caption_words
            if item.word.strip()
        ]
    if moment.caption_words:
        return [
            {"word": item.word, "start": item.start, "end": item.end}
            for item in moment.caption_words
            if item.word.strip()
        ]
    return transcript_words


def _mark_cancelled(job_id: str) -> None:
    try:
        job = load_job(job_id)
        update_job(job, status="cancelled", stage="Остановлено", error=None, progress=job.progress)
    except Exception:
        traceback.print_exc()


def _loop() -> None:
    while True:
        kind, job_id, moment_ids = _queue.get()
        try:
            job = load_job(job_id)
            if is_cancelled(job_id) or job.status == "cancelled":
                _mark_cancelled(job_id)
                continue
            if kind == "process":
                _process(job)
            elif kind == "render":
                _render(job, moment_ids or [])
        except JobCancelled:
            _mark_cancelled(job_id)
        except Exception as exc:
            try:
                job = load_job(job_id)
                if is_cancelled(job_id) or job.status == "cancelled":
                    _mark_cancelled(job_id)
                else:
                    update_job(
                        job,
                        status="error",
                        error=str(exc)[:2000],
                        stage="Ошибка",
                    )
            except Exception:
                traceback.print_exc()
        finally:
            _queue.task_done()


def _process(job: Job) -> None:
    check(job.id)
    set_stage(
        job,
        "download",
        progress=0.0,
        state="active",
        status="downloading",
        stage="Готовлю исходник",
    )
    src = job.path("source.mp4")
    if not src.exists():
        if job.source_url:
            download_vod(job, job.source_url)
        else:
            uploaded = job.path("upload.bin")
            if not uploaded.exists():
                raise RuntimeError("Нет ни VOD-ссылки, ни загруженного файла")
            ingest_local_file(job, uploaded)
            uploaded.unlink(missing_ok=True)
            set_stage(job, "download", progress=1.0, state="done", stage="Файл принят")
    else:
        set_stage(job, "download", progress=1.0, state="done", stage="Видео уже на диске")
    check(job.id)
    src = source_path(job)
    duration = probe_duration(src)
    update_job(job, duration=duration)
    set_stage(
        job,
        "audio",
        progress=0.15,
        state="active",
        status="extracting_audio",
        stage="Достаю звуковую дорожку",
    )
    audio = job.path("audio.wav")
    extract_audio(src, audio, job)
    set_stage(job, "audio", progress=1.0, state="done", stage="Звук готов")
    set_stage(
        job,
        "transcribe",
        progress=0.0,
        state="active",
        status="transcribing",
        stage="Загружаю Whisper. Первые минуты без процента — это нормально",
    )
    transcript = transcribe_job(job, audio)
    check(job.id)
    set_stage(job, "transcribe", progress=1.0, state="done", stage="Транскрипция готова")
    if not job.duration:
        update_job(job, duration=float(transcript.get("duration") or duration))

    set_stage(
        job,
        "analyze",
        progress=0.2,
        state="active",
        status="analyzing",
        stage="Считаю энергию голоса",
    )
    energy = compute_energy(job, audio)
    check(job.id)
    set_stage(
        job,
        "analyze",
        progress=0.45,
        state="active",
        status="analyzing",
        stage="ИИ отбирает моменты",
    )
    moments = analyze_job(job, transcript, energy, src)
    job.moments = moments
    job.status = "ready"
    job.progress = 1.0
    job.stage = f"Готово к ревью · {len(moments)} моментов"
    set_stage(job, "analyze", progress=1.0, state="done")
    save_job(job)


def _render(job: Job, moment_ids: list[str]) -> None:
    check(job.id)
    job = load_job(job.id)
    wanted = set(moment_ids)
    selected = [m for m in job.moments if m.id in wanted]
    if not selected:
        raise RuntimeError("Не выбрано ни одного момента")
    for moment in job.moments:
        moment.selected = moment.id in wanted
    update_job(job, moments=job.moments)
    set_stage(
        job,
        "render",
        progress=0.0,
        state="active",
        status="rendering",
        stage="Монтирую выбранные клипы",
    )
    transcript = load_json(job.path("transcript.json"))
    words = transcript.get("words") or []
    src = source_path(job)
    total = len(selected)
    for i, moment in enumerate(selected, start=1):
        check(job.id)
        set_stage(
            job,
            "render",
            progress=(i - 1) / total,
            state="active",
            status="rendering",
            stage=f"Рендер {i}/{total}: {moment.hook[:40]}",
        )
        path = render_moment(job, moment, src, _words_for_moment(moment, words))
        for item in job.moments:
            if item.id == moment.id:
                item.output_path = f"clips/{path.name}"
                item.selected = True
        save_job(job)
    write_clips_meta(job, [m for m in job.moments if m.output_path])
    set_stage(
        job,
        "render",
        progress=1.0,
        state="done",
        status="done",
        stage=f"Смонтировано {total} роликов",
    )
