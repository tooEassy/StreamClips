from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import OpenAI

from ..config import settings
from ..store import Job, Moment, dump_json
from . import brand
from .layout import attach_layouts, classify_moment


def _text_in_range(segments: list[dict], start: float, end: float) -> str:
    parts = []
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        parts.append(seg["text"])
    return " ".join(parts).strip()


def _lexicon_score(text: str) -> tuple[float, list[str]]:
    low = text.lower()
    hits: list[str] = []
    score = 0.0
    weights = {
        "death": 2.2,
        "panic": 2.0,
        "laugh": 2.4,
        "chat": 1.8,
        "wonder": 1.4,
        "newbie": 1.6,
    }
    for bucket, words in brand.EMOTION_LEXICON.items():
        found = []
        for w in words:
            if len(w) <= 4:
                if re.search(rf"(?<![а-яa-z]){re.escape(w)}(?![а-яa-z])", low):
                    found.append(w)
            elif w in low:
                found.append(w)
        if found:
            hits.extend(found[:3])
            score += weights[bucket]
    for skip in brand.SKIP_LEXICON:
        if skip in low:
            score -= 1.5
    return score, hits


def _energy_in_range(energy: dict, start: float, end: float) -> float:
    hop = float(energy.get("hop_sec") or 0.5)
    norm = energy.get("norm") or []
    if not norm:
        return 0.0
    i0 = max(0, int(start / hop))
    i1 = min(len(norm), int(end / hop) + 1)
    if i1 <= i0:
        return 0.0
    window = norm[i0:i1]
    peak = max(window)
    mean = sum(window) / len(window)
    return 0.65 * peak + 0.35 * mean


def _nms(windows: list[dict], iou_limit: float = 0.45, keep: int = 36) -> list[dict]:
    ordered = sorted(windows, key=lambda w: w["heuristic_score"], reverse=True)
    kept: list[dict] = []
    for cand in ordered:
        if len(kept) >= keep:
            break
        overlap = False
        for other in kept:
            inter = max(0, min(cand["end"], other["end"]) - max(cand["start"], other["start"]))
            union = max(cand["end"], other["end"]) - min(cand["start"], other["start"])
            if union > 0 and inter / union > iou_limit:
                overlap = True
                break
        if not overlap:
            kept.append(cand)
    kept.sort(key=lambda w: w["start"])
    return kept


def _trim_to_punchline(
    start: float,
    end: float,
    segments: list[dict],
    energy: dict,
    min_len: float,
) -> tuple[float, float]:
    """Cut trailing small-talk: keep a beat after the last hot line, not a fixed 15–35s block."""
    last_hot = start
    for seg in segments:
        if seg["end"] < start or seg["start"] > end:
            continue
        lex, _ = _lexicon_score(seg["text"])
        ener = _energy_in_range(energy, float(seg["start"]), float(seg["end"]))
        if lex >= 0.4 or ener >= 0.55:
            last_hot = max(last_hot, float(seg["end"]))
    hop = float(energy.get("hop_sec") or 0.5)
    norm = energy.get("norm") or []
    i0 = max(0, int(start / hop))
    i1 = min(len(norm), int(end / hop) + 1)
    for i in range(i1 - 1, i0, -1):
        if norm[i] >= 0.55:
            last_hot = max(last_hot, i * hop + hop)
            break
    cut = min(end, last_hot + 0.85)
    if cut - start < min_len:
        cut = min(end, start + min_len)
    return round(start, 2), round(cut, 2)


def build_candidates(job: Job, transcript: dict, energy: dict) -> list[dict]:
    segments = transcript.get("segments") or []
    duration = float(transcript.get("duration") or job.duration or 0)
    min_len = job.settings.clip_min_sec
    max_len = job.settings.clip_max_sec
    windows: list[dict] = []

    for i, seg in enumerate(segments):
        start = max(0.0, float(seg["start"]) - 0.45)
        end = min(duration or (start + 10), float(seg["end"]) + 0.4)
        j = i
        while j + 1 < len(segments) and segments[j + 1]["end"] - start <= max_len:
            nxt = segments[j + 1]
            gap = float(nxt["start"]) - float(segments[j]["end"])
            if gap > 0.9:
                break
            nxt_lex, _ = _lexicon_score(nxt["text"])
            nxt_ener = _energy_in_range(energy, float(nxt["start"]), float(nxt["end"]))
            # Keep going only while the next line is still the same beat.
            if nxt_lex < 0.4 and nxt_ener < 0.48:
                break
            end = min(start + max_len, float(nxt["end"]) + 0.35)
            j += 1
        start, end = _trim_to_punchline(start, end, segments, energy, min_len)
        if end - start < 6.5:
            continue
        text = _text_in_range(segments, start, end)
        if len(text) < 12:
            continue
        lex, hits = _lexicon_score(text)
        ener = _energy_in_range(energy, start, end)
        heuristic = lex * 1.1 + ener * 3.2 + min(len(hits), 4) * 0.2
        if duration and start > duration - 45 and lex < 1.5:
            heuristic *= 0.5
        windows.append(
            {
                "start": start,
                "end": end,
                "text": text[:900],
                "hits": hits,
                "energy": round(ener, 3),
                "heuristic_score": round(heuristic, 3),
            }
        )

    hop = float(energy.get("hop_sec") or 0.5)
    norm = energy.get("norm") or []
    if norm:
        threshold = sorted(norm)[int(len(norm) * 0.88)] if len(norm) > 20 else 0.7
        i = 0
        while i < len(norm):
            if norm[i] >= threshold:
                peak_t = i * hop
                start = max(0.0, peak_t - 2.2)
                # Grow forward only while energy stays hot, then stop.
                k = i
                while k + 1 < len(norm) and (k + 1) * hop - start <= max_len:
                    if norm[k + 1] < threshold * 0.55 and (k + 1) * hop - peak_t > 2.5:
                        break
                    k += 1
                end = min(duration or start + min_len, k * hop + 0.8)
                start, end = _trim_to_punchline(start, end, segments, energy, min_len)
                text = _text_in_range(segments, start, end)
                lex, hits = _lexicon_score(text)
                ener = _energy_in_range(energy, start, end)
                windows.append(
                    {
                        "start": start,
                        "end": end,
                        "text": text[:900],
                        "hits": hits,
                        "energy": round(ener, 3),
                        "heuristic_score": round(lex + ener * 3.5, 3),
                    }
                )
                i += int(10 / hop)
            else:
                i += 1

    return _nms(windows, keep=40)


def _client() -> OpenAI | None:
    if not settings.openai_api_key:
        return None
    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)


def _parse_json_payload(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
    return json.loads(raw)


def rank_with_llm(job: Job, candidates: list[dict]) -> list[dict] | None:
    client = _client()
    if client is None:
        return None
    compact = []
    for idx, cand in enumerate(candidates):
        compact.append(
            {
                "i": idx,
                "start": cand["start"],
                "end": cand["end"],
                "energy": cand["energy"],
                "heuristic": cand["heuristic_score"],
                "hits": cand["hits"][:5],
                "transcript": cand["text"][:500],
            }
        )
    prompt = f"""Ты редактор коротких роликов для стримера.
{brand.POSITIONING}

Столпы контента:
{chr(10).join('- ' + p for p in brand.PILLARS)}

{brand.VIRAL_RULES}

Ниже кандидаты из стрима. Выбери {job.settings.propose_count} лучших для TikTok/Shorts.

Правило нарезки (важнее длины):
- Одна мысль / одна реакция. Как только панчлайн или эмоция случились — РЕЖЬ.
- Хвост: 0.4–0.9 сек после последней сильной фразы. Дальше болтовня, приветствия, «ну ладно» — не брать.
- Лучше ролик 8–18 сек, чем 30 сек с скучным продолжением.
- Можно чуть сдвинуть start, чтобы хук был в первой секунде.
- Не растягивай окно до 15–35 секунд ради длины.

Важно: смотри на транскрипт. Не придумывай игру, бренд или вселенную, которых нет в речи.
Хештеги и хук должны совпадать с тем, что реально происходит.
В выборку обязательно клади смесь: живая реакция лица И, если есть, моменты с игрой на экране.

Верни ТОЛЬКО JSON-массив объектов:
[{{
  "i": number,  // индекс исходного кандидата, если опираешься на него
  "start": number,
  "end": number,
  "score": number,  // 0-100 потенциал просмотров
  "hook": string,   // фраза на экране в 1-ю секунду, до 8 слов
  "title": string,  // рабочий заголовок для нас
  "reason": string, // почему залетит, 1 предложение
  "on_screen_text": string, // короткий текст поверх, можно = hook
  "tiktok_caption": string, // описание ролика{(' + CTA на ' + brand.channel_cta(job.settings.watermark)) if brand.channel_cta(job.settings.watermark) else ''}
  "hashtags": [string]
}}]

Кандидаты:
{json.dumps(compact, ensure_ascii=False)}
"""
    response = client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.4,
        messages=[
            {
                "role": "system",
                "content": "Ты отбираешь вирусные нарезки стрима. Отвечай только валидным JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or "[]"
    data = _parse_json_payload(content)
    if not isinstance(data, list):
        return None
    ranked: list[dict] = []
    for item in data:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except Exception:
            continue
        if end <= start:
            continue
        duration = end - start
        if duration > job.settings.clip_max_sec:
            end = start + job.settings.clip_max_sec
        if end - start < 6:
            continue
        ranked.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "score": float(item.get("score") or 50),
                "hook": str(item.get("hook") or ""),
                "title": str(item.get("title") or ""),
                "reason": str(item.get("reason") or ""),
                "on_screen_text": str(item.get("on_screen_text") or item.get("hook") or ""),
                "tiktok_caption": str(item.get("tiktok_caption") or ""),
                "hashtags": [str(h) for h in (item.get("hashtags") or [])][:8],
                "i": item.get("i"),
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[: job.settings.propose_count]


def _fallback_rank(job: Job, candidates: list[dict]) -> list[dict]:
    top = sorted(candidates, key=lambda c: c["heuristic_score"], reverse=True)
    out = []
    for cand in top[: job.settings.propose_count]:
        hook = cand["text"].split(".")[0][:48].strip() or "Момент со стрима"
        out.append(
            {
                "start": cand["start"],
                "end": cand["end"],
                "score": min(92, 40 + cand["heuristic_score"] * 8),
                "hook": hook,
                "title": hook,
                "reason": "Пик эмоции в голосе и ключевые слова.",
                "on_screen_text": hook,
                "tiktok_caption": (
                    f"{hook} Полный стрим — {brand.channel_cta(job.settings.watermark)}"
                    if brand.channel_cta(job.settings.watermark)
                    else hook
                ),
                "hashtags": brand.default_hashtags(),
            }
        )
    return out


def make_preview(source: Path, start: float, dest: Path, duration: float = 7.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start):.2f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.2f}",
            "-vf",
            "scale=540:-2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-an",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def analyze_job(job: Job, transcript: dict, energy: dict, source: Path) -> list[Moment]:
    candidates = build_candidates(job, transcript, energy)
    dump_json(job.path("candidates.json"), candidates)
    ranked = None
    try:
        ranked = rank_with_llm(job, candidates)
    except Exception:
        ranked = None
    if not ranked:
        ranked = _fallback_rank(job, candidates)
    segs = transcript.get("segments") or []
    for item in ranked:
        item["start"], item["end"] = _trim_to_punchline(
            float(item["start"]),
            float(item["end"]),
            segs,
            energy,
            job.settings.clip_min_sec,
        )

    moments: list[Moment] = []
    for item in ranked:
        moment_id = uuid4().hex[:8]
        preview = job.path("previews", f"{moment_id}.mp4")
        try:
            make_preview(source, item["start"], preview)
            preview_rel = f"previews/{moment_id}.mp4"
        except Exception:
            preview_rel = None
        src_idx = item.get("i")
        heuristic = 0.0
        energy_val = 0.0
        if isinstance(src_idx, int) and 0 <= src_idx < len(candidates):
            heuristic = candidates[src_idx]["heuristic_score"]
            energy_val = candidates[src_idx]["energy"]
        moments.append(
            Moment(
                id=moment_id,
                start=item["start"],
                end=item["end"],
                score=round(float(item["score"]), 1),
                hook=item["hook"],
                title=item["title"],
                reason=item["reason"],
                on_screen_text=item["on_screen_text"],
                tiktok_caption=item["tiktok_caption"],
                hashtags=item["hashtags"],
                preview_path=preview_rel,
                heuristic_score=heuristic,
                energy=energy_val,
            )
        )
    attach_layouts(job, source, moments)
    _mix_gameplay_moments(job, source, moments, candidates)
    return moments


def _overlaps(start: float, end: float, moments: list[Moment]) -> bool:
    for other in moments:
        inter = max(0.0, min(end, other.end) - max(start, other.start))
        if inter > 8:
            return True
    return False


def _mix_gameplay_moments(job: Job, source: Path, moments: list[Moment], candidates: list[dict]) -> None:
    if sum(1 for m in moments if m.layout_mode == "game_pip") >= 2:
        return
    workdir = job.path("layouts")
    added = 0
    attempts = 0
    for cand in sorted(candidates, key=lambda c: -c["heuristic_score"]):
        if added >= 2 or attempts >= 12:
            break
        attempts += 1
        if _overlaps(cand["start"], cand["end"], moments):
            continue
        try:
            info = classify_moment(source, cand["start"], cand["end"], workdir)
        except Exception:
            continue
        if info["mode"] != "game_pip":
            continue
        moment_id = uuid4().hex[:8]
        preview = job.path("previews", f"{moment_id}.mp4")
        preview_rel = None
        try:
            make_preview(source, cand["start"], preview)
            preview_rel = f"previews/{moment_id}.mp4"
        except Exception:
            pass
        hook = (cand["text"].split(".")[0] or "момент с игрой")[:48]
        moment = Moment(
            id=moment_id,
            start=cand["start"],
            end=cand["end"],
            score=min(88.0, 58 + cand["heuristic_score"] * 4),
            hook=hook,
            title=hook,
            reason="На экране игра + лицо — лучше держит досмотр в TikTok, чем только камера.",
            on_screen_text=hook,
            tiktok_caption=(
                f"{hook} Полный стрим — {brand.channel_cta(job.settings.watermark)}"
                if brand.channel_cta(job.settings.watermark)
                else hook
            ),
            hashtags=brand.default_hashtags(),
            preview_path=preview_rel,
            heuristic_score=cand["heuristic_score"],
            energy=cand["energy"],
            layout_mode="game_pip",
            cam_position=info["cam_position"],
            face_cx=info["face_cx"],
        )
        moments.append(moment)
        added += 1
    moments.sort(key=lambda m: m.score, reverse=True)
    del moments[job.settings.propose_count :]
