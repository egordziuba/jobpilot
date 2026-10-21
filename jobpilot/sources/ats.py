"""Карьерные страницы компаний через официальные фиды ATS: Personio (XML), Greenhouse и Recruitee (JSON)."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from ..models import Job
from ..textutil import strip_html

log = logging.getLogger("jobpilot.ats")


def personio(fetcher, entry: dict) -> list[Job]:
    slug = entry["slug"]
    base = f"https://{slug}.jobs.personio.de"
    text = fetcher.get_text(f"{base}/xml", params={"language": "de"})
    if not text:
        return []
    root = ET.fromstring(text)
    jobs = []
    for pos in root.iter("position"):
        pid = (pos.findtext("id") or "").strip()
        title = (pos.findtext("name") or "").strip()
        if not pid or not title:
            continue
        parts = []
        for desc in pos.iter("jobDescription"):
            name = desc.findtext("name") or ""
            value = strip_html(desc.findtext("value") or "")
            parts.append(f"{name}\n{value}".strip())
        schedule = pos.findtext("schedule") or ""
        office = pos.findtext("office") or ""
        description = "\n\n".join(parts + ([f"Arbeitszeit: {schedule}"] if schedule else []))
        jobs.append(Job(
            id=f"personio:{slug}:{pid}",
            source="personio",
            company=entry.get("company") or pos.findtext("subcompany") or slug,
            title=title,
            source_url=f"{base}/job/{pid}",
            apply_url=f"{base}/job/{pid}",
            location=office,
            homeoffice="remote" in office.lower(),
            posted=(pos.findtext("createdAt") or "")[:10],
            description=description,
        ))
    log.info("Personio %s: %d", slug, len(jobs))
    return jobs


def greenhouse(fetcher, entry: dict) -> list[Job]:
    token = entry["token"]
    data = fetcher.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    jobs = []
    for item in (data or {}).get("jobs", []):
        loc = (item.get("location") or {}).get("name", "")
        jobs.append(Job(
            id=f"greenhouse:{token}:{item.get('id')}",
            source="greenhouse",
            company=entry.get("company") or token,
            title=item.get("title", ""),
            source_url=item.get("absolute_url", ""),
            apply_url=item.get("absolute_url", ""),
            location=loc,
            homeoffice="remote" in loc.lower(),
            posted=(item.get("updated_at") or "")[:10],
            description=strip_html(item.get("content", "")),
        ))
    log.info("Greenhouse %s: %d", token, len(jobs))
    return jobs


def recruitee(fetcher, entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = fetcher.get_json(f"https://{slug}.recruitee.com/api/offers/")
    jobs = []
    for item in (data or {}).get("offers", []):
        desc = strip_html((item.get("description") or "") + "\n" + (item.get("requirements") or ""))
        jobs.append(Job(
            id=f"recruitee:{slug}:{item.get('id')}",
            source="recruitee",
            company=entry.get("company") or item.get("company_name") or slug,
            title=item.get("title", ""),
            source_url=item.get("careers_url", ""),
            apply_url=item.get("careers_apply_url") or item.get("careers_url", ""),
            location=item.get("location") or item.get("city") or "",
            homeoffice=bool(item.get("remote")),
            posted=(item.get("published_at") or "")[:10],
            description=desc,
        ))
    log.info("Recruitee %s: %d", slug, len(jobs))
    return jobs
