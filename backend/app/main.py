from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings
from .pipeline.cancel import clear as clear_cancel
from .pipeline.cancel import request as request_cancel
from .pipeline import brand
from .pipeline.captions import editor_words
from .pipeline.ingest import normalize_twitch_url
from .pipeline.worker import enqueue_process, enqueue_render, start_worker
from .store import CaptionWord, JobSettings, default_stages, list_jobs, load_job, load_json, new_job, save_job


app = FastAPI(title="Stream Clips", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    start_worker()


class RenderBody(BaseModel):
    moment_ids: list[str]


class CaptionsBody(BaseModel):
    words: list[CaptionWord]


def _job_or_404(job_id: str):
    try:
        return load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc


def _moment_or_404(job, moment_id: str):
    for moment in job.moments:
        if moment.id == moment_id:
            return moment
    raise HTTPException(404, "moment not found")


def _settings_from(
    facecam_position: str,
    propose_count: int,
    whisper_model: str | None,
    watermark: str | None,
) -> JobSettings:
    return JobSettings(
        facecam_position=facecam_position if facecam_position in {
            "auto", "top_right", "top_left", "bottom_right", "bottom_left"
        } else "auto",  # type: ignore[arg-type]
        propose_count=max(4, min(24, propose_count)),
        whisper_model=whisper_model or settings.whisper_model,
        watermark=(watermark or settings.watermark or "").strip(),
    )


@app.get("/api/health")
def health() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return {
        "ok": bool(ffmpeg and ffprobe),
        "ffmpeg": ffmpeg,
        "has_llm_key": bool(settings.openai_api_key),
        "whisper_model": settings.whisper_model,
        "watermark": settings.watermark,
        "data_dir": str(settings.data_dir),
    }


@app.get("/api/jobs")
def jobs() -> dict:
    return {"jobs": [j.model_dump() for j in list_jobs()]}


@app.post("/api/jobs")
async def create_job(
    source_url: str | None = Form(default=None),
    facecam_position: str = Form(default="auto"),
    propose_count: int = Form(default=12),
    whisper_model: str | None = Form(default=None),
    watermark: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    url = source_url.strip() if source_url else None
    if url:
        try:
            url = normalize_twitch_url(url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not url and file is None:
        raise HTTPException(400, "Нужна ссылка на Twitch VOD или файл")

    job = new_job(
        source_url=url,
        source_name=file.filename if file else url,
        job_settings=_settings_from(facecam_position, propose_count, whisper_model, watermark),
    )
    if file is not None:
        dest = job.path("upload.bin")
        with dest.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
        job.extra["upload_suffix"] = suffix
        save_job(job)
    enqueue_process(job.id)
    return job.model_dump()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        job = load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc
    brand.refresh_tiktok_copy(job)
    return job.model_dump()


@app.post("/api/jobs/{job_id}/render")
def render_job(job_id: str, body: RenderBody) -> dict:
    try:
        job = load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc
    if job.status not in {"ready", "done", "error", "rendering"}:
        raise HTTPException(409, "Сначала дождись отбора моментов")
    if not body.moment_ids:
        raise HTTPException(400, "Выбери хотя бы один момент")
    enqueue_render(job.id, body.moment_ids)
    job.status = "rendering"
    job.stage = "Монтаж в очереди"
    save_job(job)
    return job.model_dump()


@app.get("/api/jobs/{job_id}/moments/{moment_id}/captions")
def get_captions(job_id: str, moment_id: str) -> dict:
    job = _job_or_404(job_id)
    moment = _moment_or_404(job, moment_id)
    if moment.caption_words:
        return {"words": [w.model_dump() for w in moment.caption_words]}
    transcript_path = job.path("transcript.json")
    if not transcript_path.exists():
        return {"words": []}
    transcript = load_json(transcript_path)
    return {"words": editor_words(transcript.get("words") or [], moment.start, moment.end)}


@app.put("/api/jobs/{job_id}/moments/{moment_id}/captions")
def save_captions(job_id: str, moment_id: str, body: CaptionsBody) -> dict:
    job = _job_or_404(job_id)
    moment = _moment_or_404(job, moment_id)
    moment.caption_words = [item for item in body.words if item.word.strip()]
    moment.captions_edited = True
    save_job(job)
    return {"words": [w.model_dump() for w in moment.caption_words]}


@app.post("/api/jobs/{job_id}/moments/{moment_id}/rerender")
def rerender_moment(job_id: str, moment_id: str) -> dict:
    job = _job_or_404(job_id)
    _moment_or_404(job, moment_id)
    if job.status not in {"ready", "done", "error"}:
        raise HTTPException(409, "Дождись конца текущего этапа, потом перемонтируй")
    enqueue_render(job.id, [moment_id])
    job.status = "rendering"
    job.stage = "Перемонтаж субтитров в очереди"
    save_job(job)
    return job.model_dump()


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    try:
        job = load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc
    job.settings.whisper_model = settings.whisper_model
    job.settings.clip_min_sec = 8
    job.settings.clip_max_sec = 28
    job.status = "queued"
    job.error = None
    job.stage = "В очереди"
    job.progress = 0.0
    job.stages = default_stages()
    save_job(job)
    clear_cancel(job.id)
    enqueue_process(job.id)
    return job.model_dump()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        job = load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc
    if job.status in {"done", "ready"}:
        raise HTTPException(409, "Эту задачу уже не нужно останавливать")
    if job.status == "cancelled":
        return job.model_dump()
    request_cancel(job.id)
    job.status = "cancelled"
    job.stage = "Остановлено"
    job.error = None
    save_job(job)
    return job.model_dump()


@app.get("/api/jobs/{job_id}/files/{rest:path}")
def job_file(job_id: str, rest: str):
    try:
        job = load_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "job not found") from exc
    path = (job.dir / rest).resolve()
    if job.dir.resolve() not in path.parents and path != job.dir.resolve():
        raise HTTPException(403, "bad path")
    if not path.is_file():
        raise HTTPException(404, "file not found")
    media = "application/octet-stream"
    if path.suffix == ".mp4":
        media = "video/mp4"
    elif path.suffix == ".json":
        media = "application/json"
    elif path.suffix == ".png":
        media = "image/png"
    if path.suffix in {".mp4", ".webm", ".mov", ".wav"}:
        return FileResponse(path, media_type=media)
    return FileResponse(path, media_type=media, filename=path.name)
