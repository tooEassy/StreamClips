from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image

from ..store import Job


LayoutMode = Literal["face_full", "game_pip"]


@lru_cache(maxsize=1)
def _face_cascade() -> cv2.CascadeClassifier:
    path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(path)
    if cascade.empty():
        raise RuntimeError("Haar cascade not found")
    return cascade


def extract_frame(source: Path, seconds: float, dest: Path, width: int = 960) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, seconds):.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "3",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


def _bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        with Image.open(path) as img:
            rgb = np.asarray(img.convert("RGB"))
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return image


def _best_face(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    h, w = gray.shape[:2]
    faces = _face_cascade().detectMultiScale(
        gray,
        scaleFactor=1.08,
        minNeighbors=5,
        minSize=(max(32, w // 30), max(32, h // 20)),
    )
    if len(faces) == 0:
        return None
    x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    if (fw * fh) / (w * h) < 0.003:
        return None
    return int(x), int(y), int(fw), int(fh)


def _split_seam(gray: np.ndarray) -> tuple[float, float]:
    """Return (strength, x_norm) of the strongest vertical cut in the middle third."""
    h, w = gray.shape[:2]
    g = gray.astype(np.float32)
    best = 0.0
    best_x = w / 2
    for x in range(int(w * 0.34), int(w * 0.66), 3):
        left = g[:, max(0, x - 12) : x].mean()
        right = g[:, x : min(w, x + 12)].mean()
        score = abs(float(left) - float(right))
        if score > best:
            best = score
            best_x = x
    return best, best_x / w


def classify_frame(path: Path) -> dict:
    bgr = _bgr(path)
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    face = _best_face(gray)
    seam, seam_x = _split_seam(gray)
    area = 0.0
    cx = 0.5
    if face:
        x, y, fw, fh = face
        area = (fw * fh) / (w * h)
        cx = (x + fw / 2) / w

    split = seam >= 38.0
    if split and face and cx >= 0.55:
        mode: LayoutMode = "game_pip"
        cam = "right"
    elif split and face and cx <= 0.45:
        mode = "game_pip"
        cam = "left"
    elif split and not face:
        mode = "game_pip"
        cam = "right" if seam_x < 0.5 else "left"
    elif area >= 0.025:
        mode = "face_full"
        cam = "right"
    elif split:
        mode = "game_pip"
        cam = "right" if cx >= 0.5 else "left"
    else:
        mode = "face_full"
        cam = "right"

    return {
        "mode": mode,
        "cam_position": cam,
        "face_cx": round(cx, 3),
        "face_area": round(area, 3),
        "seam": round(seam, 1),
        "seam_x": round(seam_x, 3),
        "best_score": round(seam / 100.0 + area, 3),
        "center_skin": area,
        "pip_scores": {},
    }


def classify_moment(source: Path, start: float, end: float, workdir: Path) -> dict:
    duration = max(1.0, end - start)
    stamps = [
        start + min(0.8, duration * 0.08),
        start + duration * 0.45,
        start + duration * 0.78,
    ]
    votes: list[dict] = []
    for i, ts in enumerate(stamps):
        frame = workdir / f"layout_{int(ts * 10)}_{i}.jpg"
        try:
            extract_frame(source, ts, frame)
            votes.append(classify_frame(frame))
        except Exception:
            continue
    if not votes:
        return {"mode": "face_full", "cam_position": "right", "face_cx": 0.5, "votes": []}
    pip_votes = sum(1 for v in votes if v["mode"] == "game_pip")
    mode: LayoutMode = "game_pip" if pip_votes >= 2 else "face_full"
    if pip_votes == 1 and max(v["seam"] for v in votes) >= 50:
        mode = "game_pip"
    if mode == "game_pip":
        right = sum(1 for v in votes if v["cam_position"] == "right")
        cam = "right" if right >= (len(votes) / 2) else "left"
    else:
        cam = "right"
    best_face = max(votes, key=lambda v: float(v.get("face_area") or 0))
    face_cx = float(best_face.get("face_cx") or 0.5)
    if (best_face.get("face_area") or 0) < 0.01:
        face_cx = 0.5
    return {"mode": mode, "cam_position": cam, "face_cx": face_cx, "votes": votes}


def attach_layouts(job: Job, source: Path, moments) -> None:
    workdir = job.path("layouts")
    workdir.mkdir(exist_ok=True)
    for moment in moments:
        info = classify_moment(source, moment.start, moment.end, workdir)
        moment.layout_mode = info["mode"]
        moment.cam_position = info["cam_position"]
        moment.face_cx = info["face_cx"]
        extra = job.extra.setdefault("layouts", {})
        extra[moment.id] = {
            "mode": info["mode"],
            "cam_position": info["cam_position"],
            "face_cx": info["face_cx"],
            "votes": info["votes"],
        }
