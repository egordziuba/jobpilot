"""Сопроводительное письмо: задача работодателя → подтверждённое доказательство → возможная польза.

Рассказ о кандидате собирается ТОЛЬКО из profile/facts.toml. Необязательная модель (llm.py)
может лишь пересказать задачу работодателя. Перед отправкой текст проходит проверку
на запрещённые утверждения.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .analyze import Analysis
from .models import Job
from .textutil import _GENDER


class LetterError(Exception):
    pass


@dataclass
class Letter:
    subject: str
    body: str
    language: str


def clean_title(title: str) -> str:
    t = _GENDER.sub("", title)
    t = re.sub(r"\s*[-–|]\s*$", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–|")


def salutation(contact: dict, lang: str) -> str:
    sal, name = contact.get("salutation"), contact.get("name")
    if lang == "en":
        if sal == "Frau" and name:
            return f"Dear Ms {name},"
        if sal == "Herr" and name:
            return f"Dear Mr {name},"
        return "Dear Hiring Team,"
    if sal == "Frau" and name:
        return f"Sehr geehrte Frau {name},"
    if sal == "Herr" and name:
        return f"Sehr geehrter Herr {name},"
    return "Sehr geehrte Damen und Herren,"


def check_claims(text: str, forbidden: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in forbidden if w.lower() in low]


def compose(job: Job, a: Analysis, facts: dict, applicant: dict, task_override: str | None = None) -> Letter:
    lang = "en" if a.language == "en" else "de"
    themes = facts.get("themes", {})
    chosen = [t for t in a.themes if t in themes] or ["process"]
    primary = chosen[0]
    secondary = next((t for t in chosen[1:] if t != "finance"), None)
    finance = "finance" in chosen and primary != "finance"

    if task_override:
        task = task_override
    else:
        task = themes[primary][f"task_{lang}"]
        if secondary:
            joiner = " and " if lang == "en" else " sowie "
            task += joiner + themes[secondary][f"task_{lang}"]

    proofs = [themes[primary][f"proof_{lang}"]]
    if finance:
        proofs.append(themes["finance"][f"proof_{lang}"])
    elif secondary:
        proofs.append(themes[secondary][f"proof_{lang}"])
    benefit = themes[primary][f"benefit_{lang}"]
    study = facts.get("study", {}).get(lang, "")
    note = job.note.strip()

    applicant_part = " ".join([study, *proofs, benefit, note])
    bad = check_claims(applicant_part, facts.get("forbidden", {}).get("claims", []))
    if bad:
        raise LetterError("запрещённые утверждения: " + ", ".join(bad))

    title = clean_title(job.title)
    sign = "\n".join([applicant["name"], applicant["address"], f'{applicant["phone"]} · {applicant["email"]}'])
    ref = job.raw.get("ref") if job.source == "ba" else ""

    if lang == "en":
        subject = f"Application: {title}" + (f" (Ref. {ref})" if ref else "")
        parts = [
            salutation(a.contact, "en"),
            f"In your posting “{title}” you are looking for support with {task}. {study}",
            "\n\n".join(proofs),
            benefit,
            f"I am available for {applicant['availability_en']}." + (f" {note}" if note else "") + " Please find my CV attached.",
            "I would be glad to hear from you.",
            "Kind regards\n" + sign,
        ]
    else:
        if re.match(r"(?i)(werkstudent|working student)", title):
            subject = f"Bewerbung als {title}"
        else:
            subject = f"Bewerbung als Werkstudent – {title}"
        subject += f" (Ref. {ref})" if ref else ""
        parts = [
            salutation(a.contact, "de"),
            f"in Ihrer Ausschreibung „{title}“ suchen Sie Unterstützung bei {task}. {study}",
            "\n\n".join(proofs),
            benefit,
            f"Ich kann {applicant['availability_de']} arbeiten." + (f" {note}" if note else "") + " Meinen Lebenslauf finden Sie im Anhang.",
            "Über eine Rückmeldung und ein persönliches Gespräch freue ich mich.",
            "Mit freundlichen Grüßen\n" + sign,
        ]
    body = "\n\n".join(p for p in parts if p.strip())
    if "{" in body or "}" in body:
        raise LetterError("в письме остался незаполненный шаблон")
    words = len(body.split())
    if words > 330:
        raise LetterError(f"письмо слишком длинное ({words} слов)")
    return Letter(subject=subject, body=body, language=lang)
