from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .cancel import JobCancelled, check, register_proc
from ..config import settings
from ..store import Job, set_stage


TWITCH_VOD_RE = re.compile(
    r"(?:twitch\.tv/(?:videos/|[^/]+/video/)|\b)(\d{8,})\b",
    re.IGNORECASE,
)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def remux_faststart(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )


def normalize_twitch_url(raw: str) -> str:
    raw = raw.strip()
    match = TWITCH_VOD_RE.search(raw)
    if match:
        return f"https://www.twitch.tv/videos/{match.group(1)}"
    if "twitch.tv" in raw:
        return raw
    raise ValueError("Нужна ссылка на Twitch VOD вида https://www.twitch.tv/videos/...")


def _parse_yt_dlp_percent(line: str) -> float | None:
    line = line.strip()
    if line.startswith("PCT"):
        try:
            return max(0.0, min(1.0, float(line.split(None, 1)[1].strip()) / 100.0))
        except (ValueError, IndexError):
            return None
    match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+~", line)
    if match:
        return float(match.group(1)) / 100.0
    match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+[\d.]+\s*GiB", line)
    if match:
        return float(match.group(1)) / 100.0
    return None


def vod_cache_path(url: str) -> Path:
    match = TWITCH_VOD_RE.search(url)
    vid = match.group(1) if match else "vod"
    folder = settings.data_dir / "vods"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{vid}.mp4"


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        try:
            os.symlink(src, dst)
        except OSError:
            subprocess.run(["cp", str(src), str(dst)], check=True)


def download_vod(job: Job, url: str) -> Path:
    url = normalize_twitch_url(url)
    cache = vod_cache_path(url)
    final = job.path("source.mp4")
    check(job.id)
    if cache.exists() and cache.stat().st_size > 5_000_000:
        set_stage(
            job,
            "download",
            progress=0.9,
            state="active",
            status="downloading",
            stage="Беру VOD из кэша",
        )
        _link_or_copy(cache, final)
        set_stage(job, "download", progress=1.0, state="done", stage="Видео готово")
        return final

    raw = cache.with_suffix(".raw.mp4")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "--merge-output-format",
        "mp4",
        "--concurrent-fragments",
        "8",
        "--newline",
        "--progress-template",
        "download:PCT %(progress.percent)s",
        "-o",
        str(raw),
        url,
    ]
    set_stage(
        job,
        "download",
        progress=0.0,
        state="active",
        status="downloading",
        stage="Скачиваю VOD",
    )
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    register_proc(job.id, process)
    assert process.stdout is not None
    last_bucket = -1
    try:
        for line in process.stdout:
            check(job.id)
            pct = _parse_yt_dlp_percent(line)
            if pct is None:
                continue
            bucket = int(pct * 200)
            if bucket == last_bucket:
                continue
            last_bucket = bucket
            set_stage(
                job,
                "download",
                progress=pct,
                state="active",
                status="downloading",
                stage=f"Скачиваю VOD {int(pct * 100)}%",
            )
    except JobCancelled:
        raise
    except Exception:
        check(job.id)
        raise
    code = process.wait()
    check(job.id)
    if code != 0 or not raw.exists():
        raise RuntimeError("yt-dlp не смог скачать VOD. Проверь, что запись публичная.")
    set_stage(job, "download", progress=0.97, state="active", stage="Собираю файл")
    remux_faststart(raw, cache)
    raw.unlink(missing_ok=True)
    _link_or_copy(cache, final)
    set_stage(job, "download", progress=1.0, state="done", stage="Видео скачано")
    return final


def ingest_local_file(job: Job, src: Path) -> Path:
    dest = job.path("source.mp4")
    try:
        remux_faststart(src, dest)
        return dest
    except subprocess.CalledProcessError:
        pass
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


def source_path(job: Job) -> Path:
    path = job.path("source.mp4")
    if not path.exists():
        raise FileNotFoundError("source.mp4")
    return path
