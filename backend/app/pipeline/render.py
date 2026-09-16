from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..store import Job, Moment, dump_json
from . import brand
from .captions import build_caption_overlay


OUTPUT_W = 1080
OUTPUT_H = 1920
FACE_H = 760
GAME_H = OUTPUT_H - FACE_H
CORNER_CAM = {"top_left", "top_right", "bottom_left", "bottom_right"}
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
TWITCH_PURPLE = (145, 70, 255, 255)
TWITCH_INK = (20, 17, 14, 255)


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


def _draw_twitch_glitch(draw: ImageDraw.ImageDraw, x: float, y: float, height: float) -> float:
    width = height * 0.9
    body = [
        (x + width * 0.08, y),
        (x + width * 0.92, y),
        (x + width * 0.92, y + height * 0.70),
        (x + width * 0.70, y + height * 0.96),
        (x + width * 0.48, y + height * 0.96),
        (x + width * 0.36, y + height * 0.82),
        (x + width * 0.20, y + height * 0.82),
        (x + width * 0.08, y + height * 0.70),
    ]
    draw.polygon(body, fill=TWITCH_PURPLE)
    eye_w = width * 0.11
    eye_h = height * 0.22
    draw.rectangle(
        [x + width * 0.30, y + height * 0.22, x + width * 0.30 + eye_w, y + height * 0.22 + eye_h],
        fill=TWITCH_INK,
    )
    draw.rectangle(
        [x + width * 0.56, y + height * 0.22, x + width * 0.56 + eye_w, y + height * 0.22 + eye_h],
        fill=TWITCH_INK,
    )
    return width


def write_watermark_overlay(path: Path, watermark: str) -> Path:
    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    text = (watermark or "").strip()
    if not text:
        img.save(path, "PNG")
        return path
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT, 42)
    except OSError:
        font = ImageFont.load_default()
    icon_h = 52
    gap = 14
    icon_w = icon_h * 0.9
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    group_w = icon_w + gap + text_w
    group_h = max(icon_h, text_h)
    x = OUTPUT_W - 56 - group_w
    y = (OUTPUT_H - group_h) / 2
    _draw_twitch_glitch(draw, x, y + (group_h - icon_h) / 2, icon_h)
    tx = x + icon_w + gap
    ty = y + (group_h - text_h) / 2 - 4
    for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
        draw.text((tx + dx, ty + dy), text, font=font, fill=(0, 0, 0, 230))
    draw.text((tx, ty), text, font=font, fill=(255, 245, 235, 240))
    img.save(path, "PNG")
    return path


def _handle_parts(watermark: str) -> tuple[str, str]:
    raw = (watermark or "").strip()
    cleaned = (
        raw.replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .strip("/")
    )
    if "/" in cleaned:
        _, nick = cleaned.rsplit("/", 1)
        return "twitch.tv/", nick or cleaned
    if cleaned:
        return "twitch.tv/", cleaned
    return "", "Twitch"


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, floor: int = 64):
    for size in range(start, floor - 1, -4):
        font = _font(FONT, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font, bbox
    font = _font(FONT, floor)
    return font, draw.textbbox((0, 0), text, font=font)


def _crop_opaque(img: Image.Image) -> Image.Image:
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return img
    pad = 12
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(img.width, bbox[2] + pad)
    bottom = min(img.height, bbox[3] + pad)
    return img.crop((left, top, right, bottom))


def write_endcard_title(path: Path) -> Path:
    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font("/System/Library/Fonts/Supplemental/Arial.ttf", 48)
    text = "смотри стрим"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (OUTPUT_W - (bbox[2] - bbox[0])) / 2
    draw.text((x, 0), text, font=font, fill=(232, 196, 168, 255))
    _crop_opaque(img).save(path, "PNG")
    return path


def write_endcard_nick(path: Path, watermark: str) -> Path:
    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    prefix, nick = _handle_parts(watermark)
    nick_font, nick_box = _fit_font(draw, nick, OUTPUT_W - 80, 168, 72)
    nick_w = nick_box[2] - nick_box[0]
    nick_h = nick_box[3] - nick_box[1]
    prefix_font = _font("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
    prefix_box = draw.textbbox((0, 0), prefix, font=prefix_font) if prefix else (0, 0, 0, 0)
    prefix_w = prefix_box[2] - prefix_box[0]
    prefix_h = prefix_box[3] - prefix_box[1]
    icon_h = 92
    icon_w = icon_h * 0.9
    gap = 18
    header_w = icon_w + (gap + prefix_w if prefix else 0)
    content_w = max(header_w, nick_w)
    x0 = (OUTPUT_W - content_w) / 2
    y = 0
    _draw_twitch_glitch(draw, x0, y + max(0, (prefix_h - icon_h) / 2), icon_h)
    if prefix:
        draw.text((x0 + icon_w + gap, y + max(0, (icon_h - prefix_h) / 2)), prefix, font=prefix_font, fill=(232, 196, 168, 230))
    nick_x = (OUTPUT_W - nick_w) / 2
    nick_y = max(icon_h, prefix_h) + 18
    for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (3, 3)):
        draw.text((nick_x + dx, nick_y + dy), nick, font=nick_font, fill=(0, 0, 0, 210))
    draw.text((nick_x, nick_y), nick, font=nick_font, fill=(255, 245, 235, 255))
    _crop_opaque(img).save(path, "PNG")
    return path


def render_endcard_clip(folder: Path, dest: Path, watermark: str, duration: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    bg = folder / "endcard_bg.png"
    title = folder / "endcard_title.png"
    nick = folder / "endcard_nick.png"
    script = folder / "endcard.filter"
    Image.new("RGB", (OUTPUT_W, OUTPUT_H), (16, 13, 11)).save(bg)
    write_endcard_title(title)
    write_endcard_nick(nick, watermark)
    dur = max(2.4, float(duration))
    script.write_text(
        "[0:v]fps=30,format=yuv420p[bg];\n"
        "[1:v]format=rgba,fade=t=in:st=0.08:d=0.32:alpha=1[title];\n"
        "[2:v]format=rgba,fade=t=in:st=0.22:d=0.42:alpha=1,"
        "scale=w='iw*(0.82+0.18*min(1,max(0,(t-0.22)/0.45)))':"
        "h='ih*(0.82+0.18*min(1,max(0,(t-0.22)/0.45)))':eval=frame[nick];\n"
        "[bg][title]overlay=(W-w)/2:(H-h)/2-280[mid];\n"
        "[mid][nick]overlay=(W-w)/2:(H-h)/2+10\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{dur:.2f}",
            "-i",
            str(bg),
            "-loop",
            "1",
            "-t",
            f"{dur:.2f}",
            "-i",
            str(title),
            "-loop",
            "1",
            "-t",
            f"{dur:.2f}",
            "-i",
            str(nick),
            "-filter_complex_script",
            str(script),
            "-t",
            f"{dur:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "endcard render failed")
    return dest


def _even(value: int) -> int:
    return int(value) - (int(value) % 2)


def _cam_crop(position: str, width: int, height: int) -> tuple[int, int, int, int]:
    # Big cam panel (this overlay): half of the frame, skip header/chat.
    if position == "right":
        return int(width * 0.505), int(height * 0.09), int(width * 0.48), int(height * 0.52)
    if position == "left":
        return int(width * 0.015), int(height * 0.09), int(width * 0.48), int(height * 0.52)
    cam_w = _even(int(width * 0.21))
    cam_h = _even(int(height * 0.26))
    margin_x = int(width * 0.01)
    if position == "top_left":
        x, y = margin_x, int(height * 0.04)
    elif position == "bottom_left":
        x, y = margin_x, int(height * 0.52)
    elif position == "bottom_right":
        x, y = width - cam_w - margin_x, int(height * 0.52)
    else:
        x, y = width - cam_w - margin_x, int(height * 0.04)
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


def _game_fill_crop(width: int, height: int) -> tuple[int, int, int, int]:
    crop_h = height
    crop_w = min(width, _even(int(round(height * 9 / 16))))
    x = (width - crop_w) // 2
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
    endcard = job.path("clips", "endcard.mp4")
    watermark_png = job.path("clips", "watermark.png")
    duration = max(job.settings.clip_min_sec, min(job.settings.clip_max_sec, moment.end - moment.start))
    cta = max(2.5, float(job.settings.cta_seconds))
    write_watermark_overlay(watermark_png, job.settings.watermark)
    render_endcard_clip(job.path("clips"), endcard, job.settings.watermark, cta)

    width, height = probe_size(source)
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

    user_cam = job.settings.facecam_position
    cam = user_cam if user_cam != "auto" else (moment.cam_position or "right")

    if user_cam in CORNER_CAM:
        cx, cy, cw, ch = _cam_crop(user_cam, width, height)
        gx, gy, gw, gh = _game_fill_crop(width, height)
        face_h = _even(max(420, min(760, int(round(OUTPUT_W * ch / max(cw, 1))))))
        prep = (
            f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,split=2[camsrc][gamesrc];"
            f"[gamesrc]crop={gw}:{gh}:{gx}:{gy},scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{OUTPUT_H}[game];"
            f"[camsrc]crop={cw}:{ch}:{cx}:{cy},scale={OUTPUT_W}:{face_h}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{face_h}[face];"
            f"[game][face]overlay=0:0,format=yuv420p[base];"
        )
    elif mode == "face_full":
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
        + f"[1:v]trim=duration={cta:.3f},setpts=PTS-STARTPTS,fps=30,setsar=1,format=yuv420p[endv];"
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
    brand.refresh_tiktok_copy(job)
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
