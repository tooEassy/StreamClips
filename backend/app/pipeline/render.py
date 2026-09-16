from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..store import Job, Moment, dump_json
from .captions import build_caption_overlay


OUTPUT_W = 1080
OUTPUT_H = 1920
FACE_H = 760
GAME_H = OUTPUT_H - FACE_H
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def write_ass(
    path: Path,
    words: list[dict],
    clip_start: float,
    clip_end: float,
    hook: str,
) -> Path:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Arial Black,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,0,8,40,40,70,1
Style: Line,Arial Black,58,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,0,2,50,50,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    if hook:
        hook_end = min(2.2, max(0.8, clip_end - clip_start))
        safe = hook.replace("\\", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,0:00:00.00,{_ass_timestamp(hook_end)},Hook,,0,0,0,,{safe}\n"
        )
    clip_words = [
        w
        for w in words
        if w.get("word") and w["end"] >= clip_start and w["start"] <= clip_end
    ]
    chunk: list[dict] = []
    for word in clip_words:
        chunk.append(word)
        joined = " ".join(w["word"] for w in chunk).strip()
        if len(chunk) >= 3 or len(joined) > 22:
            start = max(0.0, chunk[0]["start"] - clip_start)
            end = max(start + 0.12, min(clip_end, chunk[-1]["end"]) - clip_start)
            text = joined.replace("\n", " ").replace("{", "(").replace("}", ")")
            lines.append(
                f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Line,,0,0,0,,{text}\n"
            )
            chunk = []
    if chunk:
        start = max(0.0, chunk[0]["start"] - clip_start)
        end = max(start + 0.12, min(clip_end, chunk[-1]["end"]) - clip_start)
        text = " ".join(w["word"] for w in chunk).strip().replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Line,,0,0,0,,{text}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _esc_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", "’")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


def _word_chunks(words: list[dict], clip_start: float, clip_end: float) -> list[tuple[str, float, float]]:
    clip_words = [
        w
        for w in words
        if w.get("word") and w["end"] >= clip_start and w["start"] <= clip_end
    ]
    chunks: list[tuple[str, float, float]] = []
    buf: list[dict] = []
    for word in clip_words:
        buf.append(word)
        joined = " ".join(w["word"] for w in buf).strip()
        if len(buf) >= 3 or len(joined) > 22:
            start = max(0.0, buf[0]["start"] - clip_start)
            end = max(start + 0.12, min(clip_end, buf[-1]["end"]) - clip_start)
            chunks.append((joined, start, end))
            buf = []
    if buf:
        start = max(0.0, buf[0]["start"] - clip_start)
        end = max(start + 0.12, min(clip_end, buf[-1]["end"]) - clip_start)
        chunks.append((" ".join(w["word"] for w in buf).strip(), start, end))
    return chunks[:16]


def write_text_overlay(
    path: Path,
    *,
    top: str = "",
    bottom: str = "",
    corner: str = "",
) -> Path:
    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font_lg = ImageFont.truetype(FONT, 64)
        font_md = ImageFont.truetype(FONT, 52)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
    except OSError:
        font_lg = font_md = font_sm = ImageFont.load_default()

    def stroke(text: str, xy: tuple[float, float], font, fill=(255, 255, 255, 255)) -> None:
        x, y = xy
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 230))
        draw.text((x, y), text, font=font, fill=fill)

    if top:
        bbox = draw.textbbox((0, 0), top, font=font_lg)
        x = (OUTPUT_W - (bbox[2] - bbox[0])) / 2
        stroke(top[:42], (x, 70), font_lg)
    if bottom:
        bbox = draw.textbbox((0, 0), bottom, font=font_md)
        x = (OUTPUT_W - (bbox[2] - bbox[0])) / 2
        stroke(bottom[:48], (x, 1640), font_md)
    if corner:
        bbox = draw.textbbox((0, 0), corner, font=font_sm)
        x = OUTPUT_W - (bbox[2] - bbox[0]) - 28
        stroke(corner, (x, 24), font_sm, fill=(255, 245, 235, 230))
    img.save(path, "PNG")
    return path


def write_endcard(path: Path, watermark: str) -> Path:
    img = Image.new("RGB", (OUTPUT_W, OUTPUT_H), (20, 17, 14))
    draw = ImageDraw.Draw(img)
    try:
        font_lg = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 72)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 42)
    except OSError:
        font_lg = ImageFont.load_default()
        font_sm = font_lg
    title = "смотри стрим"
    lines = [(title, font_sm, 820, (232, 196, 168))]
    if watermark:
        lines.append((watermark, font_lg, 900, (255, 245, 235)))
    else:
        lines.append(("полный эфир на Twitch", font_lg, 900, (255, 245, 235)))
    for text, font, y, fill in lines:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (OUTPUT_W - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), text, font=font, fill=fill)
    img.save(path, "PNG")
    return path


def _cam_crop(position: str, width: int, height: int) -> tuple[int, int, int, int]:
    # Big cam panel (this overlay): half of the frame, skip header/chat.
    if position == "right":
        return int(width * 0.505), int(height * 0.09), int(width * 0.48), int(height * 0.52)
    if position == "left":
        return int(width * 0.015), int(height * 0.09), int(width * 0.48), int(height * 0.52)
    cam_w = int(width * 0.24)
    cam_h = int(height * 0.30)
    margin_x = int(width * 0.02)
    margin_y = int(height * 0.03)
    if position == "top_left":
        x, y = margin_x, margin_y
    elif position == "bottom_left":
        x, y = margin_x, height - cam_h - margin_y
    elif position == "bottom_right":
        x, y = width - cam_w - margin_x, height - cam_h - margin_y
    else:
        x, y = width - cam_w - margin_x, margin_y
    return x, y, cam_w, cam_h


def _game_crop(position: str, width: int, height: int) -> tuple[int, int, int, int]:
    if position == "right":
        return 0, 0, int(width * 0.50), height
    if position == "left":
        return int(width * 0.50), 0, int(width * 0.50), height
    crop_h = height
    crop_w = int(crop_h * (OUTPUT_W / GAME_H))
    crop_w = min(crop_w, width)
    if "right" in position:
        x = max(0, (width - crop_w) // 3)
    else:
        x = min(width - crop_w, (width - crop_w) * 2 // 3)
    return x, 0, crop_w, crop_h


def _face_full_crop(width: int, height: int, face_cx: float) -> tuple[int, int, int, int]:
    crop_h = height
    crop_w = min(width, int(round(height * 9 / 16)))
    x = int(face_cx * width - crop_w / 2)
    x = max(0, min(width - crop_w, x))
    return x, 0, crop_w, crop_h


def probe_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def render_moment(
    job: Job,
    moment: Moment,
    source: Path,
    words: list[dict],
) -> Path:
    out = job.path("clips", f"{moment.id}.mp4")
    endcard = job.path("clips", "endcard.png")
    watermark_png = job.path("clips", "watermark.png")
    if not endcard.exists():
        write_endcard(endcard, job.settings.watermark)
    if not watermark_png.exists():
        write_text_overlay(watermark_png, corner=job.settings.watermark)

    width, height = probe_size(source)
    duration = max(job.settings.clip_min_sec, min(job.settings.clip_max_sec, moment.end - moment.start))
    cta = job.settings.cta_seconds
    captions = build_caption_overlay(
        job.path("clips", "captions", moment.id),
        words,
        moment.start,
        duration,
        fallback=moment.on_screen_text or moment.hook,
    )

    layout = job.settings.layout
    if layout == "face_full":
        mode = "face_full"
    elif layout == "split_face_top":
        mode = "game_pip"
    else:
        mode = moment.layout_mode

    cam = job.settings.facecam_position
    if cam == "auto":
        cam = moment.cam_position or "right"

    if mode == "face_full":
        fx, fy, fw, fh = _face_full_crop(width, height, moment.face_cx or 0.5)
        prep = (
            f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"crop={fw}:{fh}:{fx}:{fy},scale={OUTPUT_W}:{OUTPUT_H},format=yuv420p[base];"
        )
    else:
        cx, cy, cw, ch = _cam_crop(cam, width, height)
        gx, gy, gw, gh = _game_crop(cam, width, height)
        prep = (
            f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,split=2[camsrc][gamesrc];"
            f"[camsrc]crop={cw}:{ch}:{cx}:{cy},scale={OUTPUT_W}:{FACE_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{FACE_H}[face];"
            f"[gamesrc]crop={gw}:{gh}:{gx}:{gy},scale={OUTPUT_W}:{GAME_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{GAME_H}[game];"
            f"[face][game]vstack=inputs=2,format=yuv420p[base];"
        )

    filter_complex = (
        prep
        + "[3:v]format=rgba[wm];[4:v]format=rgba,setpts=PTS-STARTPTS[cap];"
        + "[base][wm]overlay=0:0[marked];"
        + "[marked][cap]overlay=0:0,fps=30,setsar=1,format=yuv420p[mainv];"
        + f"[1:v]scale={OUTPUT_W}:{OUTPUT_H},setsar=1,fps=30,format=yuv420p,"
        + f"trim=duration={cta},setpts=PTS-STARTPTS[endv];"
        + f"[0:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
        + "aresample=48000,aformat=channel_layouts=stereo,dynaudnorm[maina];"
        + f"[2:a]atrim=duration={cta},asetpts=PTS-STARTPTS,"
        + "aformat=sample_rates=48000:channel_layouts=stereo[enda];"
        + "[mainv][maina][endv][enda]concat=n=2:v=1:a=1[v][a]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{moment.start:.3f}",
        "-t",
        f"{duration + 0.25:.3f}",
        "-i",
        str(source),
        "-loop",
        "1",
        "-t",
        str(cta + 0.2),
        "-i",
        str(endcard),
        "-f",
        "lavfi",
        "-t",
        str(cta + 0.2),
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-loop",
        "1",
        "-t",
        f"{duration + 0.3:.3f}",
        "-i",
        str(watermark_png),
        "-i",
        str(captions),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "19",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out.name),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(job.dir / "clips"))
    if result.returncode != 0:
        # cwd is clips/, but source/endcard are absolute; ass is relative. Output is filename.
        # Move: we wrote to clips/id.mp4 via cwd + out.name. Good.
        raise RuntimeError(result.stderr[-2500:] if result.stderr else "ffmpeg render failed")
    # Output path is job/clips/{id}.mp4 because cwd is clips and out.name is {id}.mp4
    written = job.path("clips", out.name)
    if not written.exists():
        raise RuntimeError("ffmpeg finished but output file is missing")
    return written


def write_clips_meta(job: Job, moments: list[Moment]) -> None:
    meta = []
    for moment in moments:
        meta.append(
            {
                "id": moment.id,
                "file": moment.output_path,
                "start": moment.start,
                "end": moment.end,
                "hook": moment.hook,
                "title": moment.title,
                "tiktok_caption": moment.tiktok_caption,
                "hashtags": moment.hashtags,
                "score": moment.score,
                "layout_mode": moment.layout_mode,
                "cam_position": moment.cam_position,
            }
        )
    dump_json(job.path("clips", "meta.json"), meta)
