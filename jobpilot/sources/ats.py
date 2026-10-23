"""Карьерные страницы компаний через официальные фиды ATS: Personio (XML) и публичные JSON API."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from ..models import Job
from ..net import FetchError
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


# Public schema references (no credentials required):
# https://github.com/lever/postings-api
# https://developers.smartrecruiters.com/docs/endpoints
# https://developers.ashbyhq.com/docs/public-job-posting-api
# https://workable.readme.io/reference/jobs-1

def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _identifier(value) -> str:
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _date(value) -> str:
    """Optional ISO dates, or Lever's millisecond timestamps."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
        return datetime.fromisoformat(_text(value)[:10]).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def _remote(flag, workplace, location="") -> bool:
    return flag is True or _text(workplace).lower() in ("remote", "hybrid") or "remote" in location.lower()


def _public_json(fetcher, url, params=None):
    try:
        data = fetcher.get_json(url, params=params)
    except FetchError as exc:
        if exc.status not in (404, 410):
            raise
        data = None
    if data is None:
        log.info("ATS board/posting unavailable (404/410 or unknown board): %s", url)
    return data


def _items(data, key=None) -> list:
    items = data.get(key) if isinstance(data, dict) and key else data
    if not isinstance(items, list):
        log.warning("ATS: missing or malformed %s list (unknown board)", key or "postings")
        return []
    return items


def _parse_items(items, parse, source, scope) -> list[Job]:
    jobs = []
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("posting must be an object")
            job = parse(item)
            if job is not None:
                jobs.append(job)
        except Exception as exc:
            log.warning("%s %s: malformed posting skipped: %s", source, scope, exc)
    return jobs


def lever(fetcher, entry: dict) -> list[Job]:
    company = entry["company"]  # API identifier; company_name is the optional display name.
    data = _public_json(fetcher, f"https://api.lever.co/v0/postings/{quote(company, safe='')}", {"mode": "json"})

    def parse(item):
        pid, title = _identifier(item.get("id")), _text(item.get("text"))
        if not pid or not title:
            return None
        categories = item.get("categories") or {}
        loc = _text(categories.get("location"))
        parts = [item.get("descriptionPlain") or item.get("description") or ""]
        for section in item.get("lists") or []:
            if isinstance(section, dict):
                parts.extend([_text(section.get("text")), _text(section.get("content"))])
        parts.append(item.get("additionalPlain") or item.get("additional") or "")
        url = _text(item.get("hostedUrl")) or f"https://jobs.lever.co/{quote(company, safe='')}/{quote(pid, safe='')}"
        return Job(
            id=f"lever:{company}:{pid}", source="lever", company=entry.get("company_name") or company,
            title=title, source_url=url, apply_url=_text(item.get("applyUrl")) or url,
            location=loc, homeoffice=_remote(None, item.get("workplaceType"), loc),
            posted=_date(item.get("createdAt")), description=strip_html("\n\n".join(parts)),
        )

    jobs = _parse_items(_items(data), parse, "Lever", company)
    log.info("Lever %s: %d", company, len(jobs))
    return jobs


_STUDENT_TITLE = re.compile(r"werkstudent|working student|student|praktik|intern|trainee", re.I)


def smartrecruiters(fetcher, entry: dict) -> list[Job]:
    company = entry["company"]
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(company, safe='')}/postings"
    offset, detail_calls = 0, 0
    jobs = []
    seen = set()

    def parse(item):
        nonlocal detail_calls
        pid, title = _identifier(item.get("id")), _text(item.get("name"))
        if not pid or not title or pid in seen:
            return None
        seen.add(pid)
        if detail_calls < 60 and _STUDENT_TITLE.search(title):
            detail_calls += 1  # Count failed calls too.
            try:
                detail = _public_json(fetcher, f"{base}/{quote(pid, safe='')}")
                if detail is None:
                    return None  # It disappeared between the list and detail requests.
                if isinstance(detail, dict):
                    item = {**item, **detail}
            except Exception as exc:
                log.warning("SmartRecruiters %s: detail %s unavailable: %s", company, pid, exc)
        if item.get("active") is False:
            return None
        loc_data = item.get("location") or {}
        loc = ", ".join(filter(None, (_text(loc_data.get(k)) for k in ("city", "region", "country"))))
        sections = (item.get("jobAd") or {}).get("sections") or {}
        parts = []
        for section in sections.values():
            if isinstance(section, dict):
                parts.extend([_text(section.get("title")), _text(section.get("text"))])
        url = f"https://jobs.smartrecruiters.com/{quote(company, safe='')}/{quote(pid, safe='')}"
        return Job(
            id=f"smartrecruiters:{company}:{pid}", source="smartrecruiters",
            company=entry.get("company_name") or (item.get("company") or {}).get("name") or company,
            title=title, source_url=url, apply_url=_text(item.get("applyUrl")) or url,
            location=loc, homeoffice=_remote(loc_data.get("remote"), item.get("workplaceType"), loc),
            posted=_date(item.get("releasedDate")), description=strip_html("\n\n".join(parts)),
        )

    while offset < 300:
        data = _public_json(fetcher, base, {"limit": min(100, 300 - offset), "offset": offset})
        page = _items(data, "content")
        if not page:
            break
        jobs.extend(_parse_items(page[:300 - offset], parse, "SmartRecruiters", company))
        offset += len(page)
        total = (data or {}).get("totalFound")
        if total is not None and offset >= int(total):
            break
    log.info("SmartRecruiters %s: %d", company, len(jobs))
    return jobs


def ashby(fetcher, entry: dict) -> list[Job]:
    board = entry["board"]
    data = _public_json(fetcher, f"https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe='')}")

    def parse(item):
        url = _text(item.get("jobUrl"))
        # The public schema guarantees jobUrl, but does not document an id field.
        pid = _identifier(item.get("id")) or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        title = _text(item.get("title"))
        if not pid or not title or item.get("isListed") is False:
            return None
        loc = _text(item.get("location"))
        return Job(
            id=f"ashby:{board}:{pid}", source="ashby", company=entry.get("company") or board,
            title=title, source_url=url, apply_url=_text(item.get("applyUrl")) or url,
            location=loc, homeoffice=_remote(item.get("isRemote"), item.get("workplaceType"), loc),
            posted=_date(item.get("publishedAt")),
            description=strip_html(item.get("descriptionPlain") or item.get("descriptionHtml") or ""),
        )

    jobs = _parse_items(_items(data, "jobs"), parse, "Ashby", board)
    log.info("Ashby %s: %d", board, len(jobs))
    return jobs


def workable(fetcher, entry: dict) -> list[Job]:
    subdomain = entry["subdomain"]
    data = _public_json(fetcher, f"https://apply.workable.com/api/v1/widget/accounts/{quote(subdomain, safe='')}", {"details": "true"})

    def parse(item):
        pid, title = _identifier(item.get("shortcode")), _text(item.get("title"))
        if not pid or not title:
            return None
        loc = ", ".join(filter(None, (_text(item.get(k)) for k in ("city", "state", "country"))))
        url = _text(item.get("url")) or _text(item.get("shortlink")) or f"https://apply.workable.com/{quote(subdomain, safe='')}/j/{quote(pid, safe='')}/"
        return Job(
            id=f"workable:{subdomain}:{pid}", source="workable", company=entry.get("company") or _text(data.get("name")) or subdomain,
            title=title, source_url=url, apply_url=_text(item.get("application_url")) or url,
            location=loc, homeoffice=_remote(item.get("telecommuting"), item.get("workplace_type"), loc),
            posted=_date(item.get("published_on") or item.get("created_at")),
            description=strip_html(item.get("description") or ""),
        )

    jobs = _parse_items(_items(data, "jobs"), parse, "Workable", subdomain)
    log.info("Workable %s: %d", subdomain, len(jobs))
    return jobs
