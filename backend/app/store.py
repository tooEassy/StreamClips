from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .config import JOBS_DIR


JobStatus = Literal[
    "queued",
    "downloading",
    "extracting_audio",
    "transcribing",
    "analyzing",
    "ready",
    "rendering",
    "done",
    "error",
    "cancelled",
]


class StageBar(BaseModel):
    key: str
    label: str
    progress: float = 0.0
    state: Literal["pending", "active", "done"] = "pending"
    eta_sec: float | None = None
    processed_sec: float | None = None
    total_sec: float | None = None


STAGE_DEFS: list[tuple[str, str]] = [
    ("download", "Скачивание видео"),
    ("audio", "Звуковая дорожка"),
    ("transcribe", "Транскрипция"),
    ("analyze", "Отбор моментов"),
    ("render", "Монтаж"),
]


def format_clock(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def format_eta(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h >= 1:
        return f"~{h} ч {m:02d} мин"
    if m >= 1:
        return f"~{m} мин"
    return "~меньше минуты"


def default_stages() -> dict[str, StageBar]:
    return {key: StageBar(key=key, label=label) for key, label in STAGE_DEFS}


class JobSettings(BaseModel):
    facecam_position: Literal[
        "auto", "top_right", "top_left", "bottom_right", "bottom_left"
    ] = "auto"
    layout: Literal["auto", "split_face_top", "face_full"] = "auto"
    watermark: str = ""
    clip_min_sec: float = 8
    clip_max_sec: float = 28
    propose_count: int = 12
    whisper_model: str = "large-v3"
    language: str = "ru"
    cta_seconds: float = 2.5


class CaptionWord(BaseModel):
    word: str
    start: float
    end: float


class Moment(BaseModel):
    id: str
    start: float
    end: float
    score: float
    hook: str = ""
    title: str = ""
    reason: str = ""
    on_screen_text: str = ""
    tiktok_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    selected: bool = False
    preview_path: str | None = None
    output_path: str | None = None
    heuristic_score: float = 0.0
    energy: float = 0.0
    layout_mode: Literal["face_full", "game_pip"] = "face_full"
    cam_position: str = "right"
    face_cx: float = 0.5
    caption_words: list[CaptionWord] = Field(default_factory=list)
    captions_edited: bool = False


class Job(BaseModel):
    id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    stage: str = "В очереди"
    source_url: str | None = None
    source_name: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None
    settings: JobSettings = Field(default_factory=JobSettings)
    duration: float | None = None
    moments: list[Moment] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, StageBar] = Field(default_factory=default_stages)

    @property
    def dir(self) -> Path:
        return JOBS_DIR / self.id

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job(
    *,
    source_url: str | None = None,
    source_name: str | None = None,
    job_settings: JobSettings | None = None,
) -> Job:
    job_id = uuid4().hex[:12]
    job = Job(
        id=job_id,
        source_url=source_url,
        source_name=source_name,
        created_at=_now(),
        updated_at=_now(),
        settings=job_settings or JobSettings(),
    )
    job.dir.mkdir(parents=True, exist_ok=True)
    (job.dir / "previews").mkdir(exist_ok=True)
    (job.dir / "clips").mkdir(exist_ok=True)
    save_job(job)
    return job


def job_json_path(job_id: str) -> Path:
    return JOBS_DIR / job_id / "job.json"


def load_job(job_id: str) -> Job:
    path = job_json_path(job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    return Job.model_validate_json(path.read_text(encoding="utf-8"))


def save_job(job: Job) -> None:
    job.updated_at = _now()
    path = job_json_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = job.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(prefix="job.", suffix=".json", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_job(job: Job, **fields: Any) -> Job:
    for key, value in fields.items():
        setattr(job, key, value)
    save_job(job)
    return job


def set_stage(
    job: Job,
    key: str,
    *,
    progress: float | None = None,
    state: str | None = None,
    status: JobStatus | None = None,
    stage: str | None = None,
    eta_sec: float | None = None,
    processed_sec: float | None = None,
    total_sec: float | None = None,
) -> Job:
    if not job.stages:
        job.stages = default_stages()
    bar = job.stages.get(key)
    if bar is None:
        labels = dict(STAGE_DEFS)
        bar = StageBar(key=key, label=labels.get(key, key))
        job.stages[key] = bar
    if progress is not None:
        bar.progress = max(0.0, min(1.0, float(progress)))
    if state is not None:
        bar.state = state  # type: ignore[assignment]
        if state == "done":
            bar.progress = 1.0
            bar.eta_sec = None
        if state == "pending":
            bar.progress = 0.0
            bar.eta_sec = None
    if state != "done" and eta_sec is not None:
        bar.eta_sec = max(0.0, float(eta_sec))
    if processed_sec is not None:
        bar.processed_sec = max(0.0, float(processed_sec))
    if total_sec is not None:
        bar.total_sec = max(0.0, float(total_sec))
    if state == "active":
        order = [name for name, _ in STAGE_DEFS]
        if key in order:
            for prev in order[: order.index(key)]:
                prev_bar = job.stages.get(prev)
                if prev_bar and prev_bar.state != "done":
                    prev_bar.state = "done"
                    prev_bar.progress = 1.0
    fields: dict[str, Any] = {"stages": job.stages}
    if status is not None:
        fields["status"] = status
    if stage is not None:
        fields["stage"] = stage
    if progress is not None:
        fields["progress"] = bar.progress
    elif state == "done":
        fields["progress"] = 1.0
    return update_job(job, **fields)


def list_jobs() -> list[Job]:
    jobs: list[Job] = []
    if not JOBS_DIR.exists():
        return jobs
    for path in JOBS_DIR.iterdir():
        if (path / "job.json").exists():
            try:
                jobs.append(load_job(path.name))
            except Exception:
                continue
    jobs.sort(key=lambda item: item.created_at, reverse=True)
    return jobs


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
