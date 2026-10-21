"""Необязательно: модель пересказывает задачу работодателя одной фразой.

О кандидате модель ничего не пишет. При любой ошибке или сомнительном ответе
используется шаблонная фраза из profile/facts.toml.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jobpilot.llm")

API = "https://api.anthropic.com/v1/messages"

PROMPT_DE = (
    "Lies die Stellenanzeige und vervollständige den Satz „Sie suchen Unterstützung bei …“ mit EINER "
    "Nominalphrase im Dativ (höchstens 14 Wörter), die die Hauptaufgabe beschreibt. Keine Aussagen über "
    "Bewerber, keine Anführungszeichen, kein Punkt am Ende. Antworte nur mit der Phrase.\n\nStellenanzeige:\n"
)
PROMPT_EN = (
    "Read the job posting and complete the sentence 'you are looking for support with …' with ONE noun "
    "phrase (max. 14 words) describing the main task. No statements about applicants, no quotes, no final "
    "period. Reply with the phrase only.\n\nJob posting:\n"
)


def summarize_task(fetcher, api_key: str, model: str, text: str, lang: str) -> str | None:
    if not api_key:
        return None
    prompt = (PROMPT_EN if lang == "en" else PROMPT_DE) + text[:3500]
    try:
        data = fetcher.post_json(
            API,
            {"model": model, "max_tokens": 80, "messages": [{"role": "user", "content": prompt}]},
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    except Exception as exc:
        log.warning("LLM недоступна: %s", exc)
        return None
    return validate(out)


def validate(phrase: str) -> str | None:
    p = phrase.strip().strip("„“\"'").rstrip(".")
    words = p.split()
    if not 2 <= len(words) <= 16 or "\n" in p:
        return None
    if re.search(r"\b(ich|mein|meine|mir|mich|du|dein|i|my|me)\b", p, re.I):
        return None
    if re.match(r"(?i)(unterstützung|support)\b", p):
        return None
    return p
