"""Ручные лиды: data/manual_leads.csv (+ текст объявления в data/manual/<файл>.txt).

Сюда можно вписать вакансию, найденную где угодно (LinkedIn, stellenwerk, сайт компании).
Она проходит ту же проверку, что и найденные автоматически.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import Job

FIELDS = ["id", "company", "title", "url", "email", "deadline", "location", "remote", "description_file", "note"]


def load(data_dir: Path) -> list[Job]:
    path = data_dir / "manual_leads.csv"
    if not path.exists():
        return []
    jobs = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("id") and row.get("company") and row.get("title")):
                continue
            if row["id"].startswith("#"):
                continue
            desc = ""
            if row.get("description_file"):
                f = data_dir / "manual" / row["description_file"]
                if f.exists():
                    desc = f.read_text(encoding="utf-8")
            jobs.append(Job(
                id=f"manual:{row['id'].strip()}",
                source="manual",
                company=row["company"].strip(),
                title=row["title"].strip(),
                source_url=(row.get("url") or "").strip(),
                apply_url=(row.get("url") or "").strip(),
                location=(row.get("location") or "").strip(),
                homeoffice=(row.get("remote") or "").strip().lower() in {"1", "yes", "ja", "true"},
                deadline=(row.get("deadline") or "").strip(),
                description=desc,
                email_hint=(row.get("email") or "").strip(),
                note=(row.get("note") or "").strip(),
            ))
    return jobs
