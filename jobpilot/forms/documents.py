"""Комплект файлов для формы: Anschreiben (PDF из сохранённого письма), резюме, Zeugnisse."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..pdfletter import build_pdf


def slug(text: str, n: int = 40) -> str:
    t = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE).strip("_")
    return t[:n].strip("_") or "job"


def strip_ref(subject: str) -> str:
    return re.sub(r"\s*\(Ref\.[^)]*\)\s*$", "", subject or "").strip()


def recipient_lines(row) -> list[str]:
    extra = {}
    try:
        extra = json.loads(row["extra"] or "{}") if "extra" in row.keys() else {}
    except Exception:
        extra = {}
    contact = {}
    try:
        contact = json.loads(row["contact"] or "{}")
    except Exception:
        contact = {}
    lines = [row["company"]]
    name = (contact.get("full_name") or contact.get("name") or "").strip()
    sal = contact.get("salutation") or ""
    if name:
        lines.append("Personalabteilung")
        lines.append(f"{sal} {name}".strip())
    else:
        lines.append("Personalabteilung")
    addr = extra.get("address") or {}
    street = (addr.get("street") or "").strip()
    city = " ".join(x for x in ((addr.get("zip") or "").strip(), (addr.get("city") or "").strip()) if x)
    if street:
        lines.append(street)
    if city:
        lines.append(city)
    elif row["location"]:
        loc = re.split(r"\s+·\s+", row["location"])[0].strip()
        if loc:
            lines.append(loc)
    return lines


def letter_text_plain(body: str) -> str:
    """Текст письма для текстового поля формы: без строк адреса и телефона в подписи (они уже в форме)."""
    lines = (body or "").rstrip().split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(("Mit freundlichen Grüßen", "Kind regards", "Best regards")):
            tail = [x for x in lines[i + 1:] if x.strip()]
            return "\n".join(lines[: i + 1] + tail[:1])
    return body or ""


def prepare(settings, row, out_root: Path | None = None) -> dict:
    """Готовит файлы в data/applications/<id>/ с аккуратными именами (их видит работодатель)."""
    ap = settings.applicant
    fm = settings.cfg.get("forms", {})
    out = (out_root or settings.data_dir / "applications") / slug(row["id"], 60)
    out.mkdir(parents=True, exist_ok=True)
    who = slug(ap.get("name", "Bewerber"), 40)
    docs: dict = {"dir": out, "letter": None, "cv": None, "certificates": []}

    if row["body"]:
        lang = "en" if (row["language"] or "") == "en" else "de"
        name = f"Cover_Letter_{who}.pdf" if lang == "en" else f"Anschreiben_{who}.pdf"
        place = (re.search(r"\d{5}\s+(.+)$", ap.get("address", "")) or [None, ""])[1]
        docs["letter"] = str(build_pdf(out / name, ap, recipient_lines(row), strip_ref(row["subject"]),
                                       row["body"], place=place, lang=lang))
    cv = settings.cv_path
    if cv.exists():
        target = out / (ap.get("cv_attachment_name") or cv.name)
        if not target.exists() or target.stat().st_mtime < cv.stat().st_mtime:
            shutil.copy2(cv, target)
        docs["cv"] = str(target)
    for rel in fm.get("certificates", []) or []:
        src = settings.root / rel
        if src.exists():
            docs["certificates"].append(str(src))
    return docs
