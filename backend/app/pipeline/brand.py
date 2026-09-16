"""Generic viral-clip rubric for any streamer."""

import re

LANGUAGE = "ru"


POSITIONING = (
    "Русскоязычный стример. Короткие ролики должны цеплять с первой секунды: "
    "лицо, эмоция, чат, один понятный момент — не гайд и не длинный гринд."
)

PILLARS = [
    "Живая реакция: паника, смех, непонимание, восторг, фейл",
    "Чат участвует: учит, выбирает, спорит со стримером",
    "Ситуация, а не процесс: одно событие с началом и панчлайном",
]


VIRAL_RULES = """
Цель ролика — охват в TikTok и YouTube Shorts, затем переход на полный стрим.

Формат:
- 8–28 секунд, одна мысль
- Лицо или сильный хук в первую секунду
- Вертикаль 9:16
- Водяной знак — ник/ссылка канала, если заданы
- Конец 2 секунды: призыв смотреть стрим
- Язык как в записи, обычно русский
- Без токсичности и унижения

Что обычно залетает:
1. Живая реакция лица
2. Диалог с чатом
3. Неожиданный фейл, победа, абсурдная фраза
4. Хук-вопрос в первые 1–2 секунды

Что резать:
- Тихий гринд без речи и эмоции
- Длинные объяснения механик без шутки
- Паузы, AFK, настройки, очереди
- Токсичность и ссоры

Хук — фраза для первой секунды экрана, не заголовок статьи.
Примеры:
- «Чат, я СНОВА умер»
- «Мне сказали нажать N. Что такое N»
- «Я не понимаю, что происходит»
"""

EMOTION_LEXICON = {
    "death": [
        "умерла",
        "умер",
        "смерть",
        "убили",
        "я труп",
        "опять",
        "респ",
        "release spirit",
        "вы дух",
    ],
    "panic": [
        "ааа",
        "а-а-а",
        "блин",
        "блять",
        "ё-моё",
        "ужас",
        "паника",
        "не понимаю",
        "что происходит",
        "помогите",
        "спасите",
        "куда",
        "стой",
    ],
    "laugh": [
        "хах",
        "ахах",
        "хаха",
        "смешно",
        "ору",
        "лол",
        "кек",
        "ржу",
        "умора",
    ],
    "chat": [
        "чат",
        "выберите",
        "выбирайте",
        "скажите",
        "объясните",
        "что нажать",
        "талант",
        "класс",
    ],
    "wonder": [
        "вау",
        "красиво",
        "ого",
        "офигеть",
        "крылья",
        "wow",
    ],
    "newbie": [
        "я не знаю",
        "первый раз",
        "новичок",
        "научите",
        "что это",
        "как это",
        "я учусь",
    ],
}

SKIP_LEXICON = [
    "сейчас настрою",
    "подождите",
    "био",
    "браузер",
    "сейчас вернусь",
    "afk",
    "минуту",
    "реклама",
]


def channel_cta(watermark: str | None) -> str:
    return twitch_url(watermark)


def twitch_url(watermark: str | None) -> str:
    raw = (watermark or "").strip()
    if not raw:
        return ""
    cleaned = (
        raw.replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .strip()
    )
    if "twitch.tv/" in cleaned.lower():
        _, nick = cleaned.rsplit("/", 1)
        return f"twitch.tv/{nick}" if nick else cleaned
    return f"twitch.tv/{cleaned.lstrip('@')}"


def twitch_nick(watermark: str | None) -> str:
    url = twitch_url(watermark)
    if "/" not in url:
        return ""
    return url.rsplit("/", 1)[-1]


def default_hashtags() -> list[str]:
    return ["#стрим", "#нарезка", "#тикток"]


_TOPIC_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("hearthstone", "хартстоун", "нерф", "мана", "таверн", "наксирам"), ["hearthstone", "хартстоун"]),
    (("wow", "варкрафт", "warcraft", "азерот", "небеснорожден"), ["wow", "warcraft"]),
    (("diablo", "диабло"), ["diablo"]),
    (("чат",), ["чат"]),
]

_FAMILIES = [
    {"wow", "warcraft", "варкрафт"},
    {"hearthstone", "хартстоун"},
    {"diablo", "диабло"},
]


def _norm_tag(raw: str) -> str:
    tag = (raw or "").strip().lstrip("#")
    tag = tag.replace(" ", "")
    return tag


def topical_hashtags(
    text: str,
    extras: list[str] | None = None,
    watermark: str | None = None,
) -> list[str]:
    blob = f" {text.lower()} "
    tags: list[str] = []
    for keys, names in _TOPIC_RULES:
        if any(key in blob for key in keys):
            tags.extend(names)
    nick = twitch_nick(watermark)
    if nick:
        tags.append(nick)
    for extra in extras or []:
        tags.append(_norm_tag(extra))
    tags.extend(["стрим", "нарезка", "тикток"])

    matched_families = []
    tagset = {t.lower() for t in tags}
    for family in _FAMILIES:
        if tagset & family:
            matched_families.append(family)
    drop: set[str] = set()
    if len(matched_families) > 1:
        # Prefer tags that came from the source text, not extras.
        text_hits = []
        for keys, names in _TOPIC_RULES:
            if any(key in blob for key in keys):
                text_hits.extend(n.lower() for n in names)
        keep_family = None
        for family in _FAMILIES:
            if family & set(text_hits):
                keep_family = family
                break
        if keep_family:
            for family in _FAMILIES:
                if family is not keep_family:
                    drop |= family

    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = _norm_tag(tag).lower()
        if not key or key in seen or key in drop:
            continue
        seen.add(key)
        out.append(f"#{_norm_tag(tag)}")
        if len(out) >= 6:
            break
    return out


def _caption_line(hook: str) -> str:
    line = (hook or "").strip()
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"twitch\.tv/\S+", "", line, flags=re.I)
    line = re.sub(r"(полный стрим|переходи на)\s*[—:\-]*\s*", "", line, flags=re.I)
    line = re.sub(r"\s+", " ", line).strip(" -—|")
    return line


def tiktok_description(
    *,
    hook: str,
    watermark: str | None,
    hashtags: list[str],
) -> str:
    line = _caption_line(hook) or "момент со стрима"
    parts = [line]
    cta = twitch_url(watermark)
    if cta:
        parts.append(cta)
    if hashtags:
        parts.append(" ".join(hashtags))
    return "\n\n".join(parts)


def refresh_tiktok_copy(job) -> None:
    for moment in job.moments:
        text = " ".join(
            part
            for part in (moment.hook, moment.title, moment.reason, moment.on_screen_text)
            if part
        )
        moment.hashtags = topical_hashtags(text, moment.hashtags, job.settings.watermark)
        moment.tiktok_caption = tiktok_description(
            hook=moment.hook or moment.title,
            watermark=job.settings.watermark,
            hashtags=moment.hashtags,
        )
