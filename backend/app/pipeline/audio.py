from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from .cancel import check, register_proc
from ..store import Job, dump_json


def extract_audio(source: Path, dest: Path, job: Job | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(dest),
    ]
    if job is None:
        subprocess.run(cmd, check=True, capture_output=True)
        return dest
    check(job.id)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    register_proc(job.id, process)
    code = process.wait()
    check(job.id)
    if code != 0 or not dest.exists():
        raise RuntimeError("ffmpeg не смог вытащить звук")
    return dest


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
        channels = handle.getnchannels()
    if width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 255.0 - 0.5
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def compute_energy(job: Job, audio_path: Path, hop_sec: float = 0.5) -> dict:
    samples, rate = _read_wav_mono(audio_path)
    hop = max(1, int(rate * hop_sec))
    n_windows = max(1, len(samples) // hop)
    rms = np.zeros(n_windows, dtype=np.float32)
    for i in range(n_windows):
        chunk = samples[i * hop : (i + 1) * hop]
        rms[i] = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))
    # Normalize to 0..1 by 95th percentile to ignore mic spikes a bit.
    p95 = float(np.percentile(rms, 95)) or 1.0
    norm = np.clip(rms / p95, 0, 1.5)
    payload = {
        "hop_sec": hop_sec,
        "rms": [round(float(x), 5) for x in rms],
        "norm": [round(float(x), 5) for x in norm],
    }
    dump_json(job.path("energy.json"), payload)
    return payload
