"""Нормализация текста, ключи для дублей, расстояния."""
from __future__ import annotations

import html
import math
import re
import unicodedata

_GENDER = re.compile(
    r"\((?:[mwdfxgn]\s*[/|,]?\s*){2,4}\)|\((?:all genders?|alle geschlechter|gn\*?|w/m/x|m/f/x|d/f/m|m/w/div\.?|w/m/div\.?)\)",
    re.I,
)
_LEGAL = re.compile(
    r"\b(gmbh|mbh|ag|se|kg|kgaa|ohg|ug|e\.?\s?v|co|gbr|ltd|llc|inc|plc|holding|gruppe|group|deutschland|germany|stiftung)\b\.?",
    re.I,
)


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return clean_text(text)


def clean_text(text: str) -> str:
    """Убирает markdown-разметку BA (**жирный**, [текст](ссылка)) и лишние пробелы."""
    if not text:
        return ""
    text = text.replace("\r", "")
    text = re.sub(r"\[([^\]]*)\]\((?:https?://)?mailto:([^)]+)\)", r"\2", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def title_key(title: str) -> str:
    t = _GENDER.sub(" ", title or "")
    t = fold(t)
    t = re.sub(r"werkstudent(?:in|\*in|:in|/-?in|_in|en)?", "werkstudent", t)
    t = re.sub(r"working student", "werkstudent", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def company_key(company: str) -> str:
    c = fold(company or "")
    c = re.split(r"\babteilung\b|\bniederlassung\b|\bstandort\b", c)[0]
    c = c.replace("&", " ")
    c = _LEGAL.sub(" ", c)
    c = re.sub(r"[^a-z0-9]+", " ", c)
    return re.sub(r"\s+", " ", c).strip()


def dedupe_key(company: str, title: str) -> str:
    return f"{company_key(company)}|{title_key(title)}"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(d))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+|•|•", text or "")
    return [p.strip(" -–\t") for p in parts if p and p.strip(" -–\t")]


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", fold(text)[:4000])
    if not words:
        return "de"
    en = sum(w in {"the", "and", "you", "with", "your", "our", "we", "will", "for", "of", "to"} for w in words)
    de = sum(w in {"und", "der", "die", "das", "mit", "du", "sie", "wir", "fur", "von", "zu", "bei"} for w in words)
    return "en" if en > de * 1.3 else "de"
