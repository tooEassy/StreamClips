from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_W = 1080
OUTPUT_H = 1920
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
FONT_FALLBACK = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# Dynamic Minimalism + one brand accent (not Hormozi yellow).
WHITE = (255, 255, 255, 255)
WHITE_WAIT = (255, 255, 255, 190)
CORAL = (232, 93, 76, 255)
STROKE = (0, 0, 0, 255)

ATTACH_NEXT = {
    "я", "ты", "он", "она", "мы", "вы", "не", "ни", "в", "во", "на", "и", "а",
    "но", "ну", "же", "бы", "ли", "это", "как", "что", "у", "с", "со", "к",
    "ко", "о", "об", "от", "по", "до", "за", "из", "для", "да", "нет", "вот",
    "уже", "ещё", "еще", "там", "тут", "так", "то", "мне", "тебе", "ему",
    "ей", "нас", "вас", "мой", "моя", "все", "всё", "просто",
}

SKIP_RE = re.compile(r"^[\W_]+$|^\(.*\)$|^\[.*\]$")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (FONT_PATH, FONT_FALLBACK):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean(raw: str) -> str:
    text = (raw or "").strip()
    text = text.replace("\n", " ")
    return text


def _display(word: str) -> str:
    text = re.sub(r"[.,;:]+$", "", _clean(word))
    return text.upper()


def editor_words(words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    return [
        {
            "word": item["raw"],
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
        }
        for item in clip_words(words, clip_start, clip_end)
    ]


def clip_words(words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    out: list[dict] = []
    for item in words:
        raw = _clean(str(item.get("word") or ""))
        if not raw or SKIP_RE.match(raw):
            continue
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start)
        if end < clip_start or start > clip_end:
            continue
        out.append(
            {
                "text": _display(raw),
                "raw": raw,
                "start": max(clip_start, start),
                "end": min(clip_end, max(end, start + 0.08)),
            }
        )
    return out


def _group_words(items: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    buf: list[dict] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            groups.append(buf)
            buf = []

    for i, item in enumerate(items):
        nxt = items[i + 1] if i + 1 < len(items) else None
        if buf and nxt and (item["start"] - buf[-1]["end"]) > 0.85:
            flush()
        buf.append(item)
        joined = " ".join(w["text"] for w in buf)
        punct = bool(re.search(r"[.!?…]$", item["raw"]))
        tiny_next = bool(nxt and nxt["raw"].lower().strip(".,!?") in ATTACH_NEXT)
        if punct and len(buf) >= 1 and not tiny_next:
            flush()
            continue
        if len(buf) >= 3:
            flush()
            continue
        if len(joined) >= 18 and len(buf) >= 2 and not tiny_next:
            flush()
            continue
    flush()
    return groups


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _stroke_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, fill) -> None:
    x, y = xy
    for dx, dy in (
        (-5, 0), (5, 0), (0, -5), (0, 5),
        (-4, -4), (4, 4), (-4, 4), (4, -4),
        (-6, 0), (6, 0), (0, -6), (0, 6),
        (-3, 0), (3, 0), (0, -3), (0, 3),
    ):
        draw.text((x + dx, y + dy), text, font=font, fill=STROKE)
    draw.text((x, y), text, font=font, fill=fill)


def render_caption_png(path: Path, tokens: list[str], active: int) -> Path:
    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    if not tokens:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
        return path
    draw = ImageDraw.Draw(img)
    size = 76
    gap = 22
    font = _font(size)
    widths = []
    total_w = 0
    for token in tokens:
        w, _h = _measure(draw, token, font)
        widths.append(w)
        total_w += w
    if tokens:
        total_w += gap * (len(tokens) - 1)
    while total_w > OUTPUT_W - 80 and size > 52:
        size -= 4
        font = _font(size)
        widths = []
        total_w = 0
        for token in tokens:
            w, _h = _measure(draw, token, font)
            widths.append(w)
            total_w += w
        if tokens:
            total_w += gap * (len(tokens) - 1)

    x = (OUTPUT_W - total_w) / 2
    y = 1460
    for i, token in enumerate(tokens):
        fill = CORAL if i == active else (WHITE if i < active else WHITE_WAIT)
        _stroke_text(draw, (x, y), token, font, fill)
        x += widths[i] + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def _transparent_png(path: Path) -> Path:
    if not path.exists():
        Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0)).save(path, "PNG")
    return path


def build_caption_overlay(
    folder: Path,
    words: list[dict],
    clip_start: float,
    duration: float,
    fallback: str = "",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    empty = _transparent_png(folder / "_empty.png")
    clip_end = clip_start + duration
    items = clip_words(words, clip_start, clip_end)
    groups = _group_words(items)

    timeline: list[tuple[float, float, Path]] = []
    if not groups and fallback:
        png = render_caption_png(folder / "hook.png", _display(fallback).split()[:4], 0)
        timeline.append((0.0, min(1.6, duration), png))
    else:
        for g_i, group in enumerate(groups):
            for w_i, word in enumerate(group):
                start = word["start"] - clip_start
                if w_i + 1 < len(group):
                    end = group[w_i + 1]["start"] - clip_start
                else:
                    end = word["end"] - clip_start + 0.12
                    if g_i + 1 < len(groups):
                        end = min(end, groups[g_i + 1][0]["start"] - clip_start - 0.04)
                start = max(0.0, start)
                end = min(duration, max(start + 0.10, end))
                png = render_caption_png(
                    folder / f"{g_i:02d}_{w_i}.png",
                    [w["text"] for w in group],
                    w_i,
                )
                timeline.append((start, end, png))

    timeline.sort(key=lambda item: item[0])
    frames: list[tuple[Path, float]] = []
    cursor = 0.0
    for start, end, png in timeline:
        if start > cursor + 0.02:
            frames.append((empty, start - cursor))
        frames.append((png, max(0.08, end - start)))
        cursor = max(cursor, end)
    if cursor < duration:
        frames.append((empty, duration - cursor))
    if not frames:
        frames.append((empty, duration))

    concat_path = folder / "captions.ffconcat"
    mov_path = folder / "captions.mov"
    lines = ["ffconcat version 1.0\n"]
    for path, dur in frames:
        lines.append(f"file '{path.as_posix()}'\n")
        lines.append(f"duration {max(0.04, dur):.3f}\n")
    lines.append(f"file '{frames[-1][0].as_posix()}'\n")
    concat_path.write_text("".join(lines), encoding="utf-8")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vf",
            f"fps=30,format=rgba,scale={OUTPUT_W}:{OUTPUT_H}",
            "-c:v",
            "qtrle",
            "-t",
            f"{duration:.3f}",
            str(mov_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not mov_path.exists():
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "caption overlay failed")
    return mov_path
