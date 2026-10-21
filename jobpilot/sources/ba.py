"""Jobsuche der Bundesagentur für Arbeit — официальный REST-API сайта (v6 поиск, v4 детали)."""
from __future__ import annotations

import base64
import logging

from ..models import Job
from ..textutil import clean_text

log = logging.getLogger("jobpilot.ba")

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
HEADERS = {"X-API-Key": "jobboerse-jobsuche", "Accept": "application/json"}
DETAIL_PAGE = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"


def _first_list(payload: dict) -> list[dict]:
    for key in ("ergebnisliste", "stellenangebote"):
        if isinstance(payload.get(key), list):
            return payload[key]
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def parse_item(item: dict) -> Job | None:
    ref = item.get("referenznummer") or item.get("refnr")
    title = item.get("stellenangebotsTitel") or item.get("titel") or item.get("beruf") or ""
    company = item.get("firma") or item.get("arbeitgeber") or ""
    if not ref or not title:
        return None
    lat = lon = None
    location = ""
    locs = item.get("stellenlokationen") or []
    if locs:
        adr = locs[0].get("adresse") or {}
        location = " ".join(x for x in (adr.get("plz"), adr.get("ort")) if x)
        lat, lon = locs[0].get("breite"), locs[0].get("laenge")
    elif item.get("arbeitsort"):
        ao = item["arbeitsort"]
        location = " ".join(str(x) for x in (ao.get("plz"), ao.get("ort")) if x)
        koord = ao.get("koordinaten") or {}
        lat, lon = koord.get("lat"), koord.get("lon")
    external = item.get("externeURL") or item.get("externeUrl") or ""
    posted = (item.get("datumErsteVeroeffentlichung") or item.get("aktuelleVeroeffentlichungsdatum")
              or (item.get("veroeffentlichungszeitraum") or {}).get("von") or "")
    return Job(
        id=f"ba:{ref}",
        source="ba",
        company=company.strip(),
        title=title.strip(),
        source_url=DETAIL_PAGE.format(ref=ref),
        external_url=external,
        location=location,
        lat=lat,
        lon=lon,
        distance_km=item.get("entfernung"),
        homeoffice=bool(item.get("homeofficemoeglich")),
        posted=str(posted)[:10],
        needs_detail=True,
        raw={"ref": ref, "art": item.get("stellenangebotsart"), "hotyp": item.get("homeofficetyp")},
    )


def search(fetcher, block: dict, max_age_days: int) -> list[Job]:
    jobs: dict[str, Job] = {}
    for query in block.get("queries", []):
        for art in block.get("angebotsart", [34]):
            params = {
                "was": query,
                "wo": block.get("where") or None,
                "umkreis": block.get("radius_km") or None,
                "angebotsart": art,
                "veroeffentlichtseit": max_age_days,
                "size": 100,
                "page": 1,
                "pav": "false",
                "zeitarbeit": "false",
            }  # фильтр arbeitszeit=ho в v6 обнуляет выдачу (проверено 19.10.2026) — удалённые отбираются по тексту
            try:
                payload = fetcher.get_json(f"{BASE}/pc/v6/jobs", params=params, headers=HEADERS)
            except Exception as exc:  # один упавший запрос не останавливает остальные
                log.warning("BA поиск '%s' (%s): %s", query, block.get("where"), exc)
                continue
            if not payload:
                continue
            for item in _first_list(payload):
                job = parse_item(item)
                if job:
                    job.raw["nationwide"] = not block.get("where")
                if job and job.id not in jobs:
                    jobs[job.id] = job
    log.info("BA %s: %d объявлений", block.get("where") or "remote", len(jobs))
    return list(jobs.values())


def _walk_urls(node, path="", out=None) -> dict[str, str]:
    """Все ссылки из ответа деталей (кроме самой Arbeitsagentur): ключ → URL."""
    out = {} if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_urls(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node[:20]):
            _walk_urls(v, f"{path}[{i}]", out)
    elif isinstance(node, str) and node.startswith(("http://", "https://")) and "arbeitsagentur.de" not in node:
        out[path] = node
    return out


def detail_extra(data: dict) -> dict:
    """Адрес работодателя и ссылки на подачу из деталей вакансии (для формы и письма)."""
    extra: dict = {}
    adr = data.get("arbeitgeberAdresse") or data.get("arbeitgeberadresse") or {}
    if isinstance(adr, dict) and (adr.get("strasse") or adr.get("ort")):
        street = " ".join(x for x in (adr.get("strasse"), adr.get("hausnummer")) if x)
        extra["address"] = {"street": street, "zip": adr.get("plz") or "", "city": adr.get("ort") or ""}
    urls = _walk_urls(data)
    if urls:
        extra["urls"] = urls
    return extra


def pick_apply_url(urls: dict[str, str]) -> str:
    def rank(item):
        key, url = item[0].lower(), item[1].lower()
        score = 0
        if "extern" in key or "bewerb" in key or "apply" in key:
            score += 3
        if any(w in url for w in ("bewerb", "apply", "karriere", "career", "jobs", "stellen")):
            score += 2
        if any(w in url for w in ("logo", ".png", ".jpg", ".svg", "bild", "image")):
            score -= 5
        return score
    best = sorted(urls.items(), key=rank, reverse=True)
    return best[0][1] if best and rank(best[0]) > 0 else ""


def fetch_detail(fetcher, job: Job) -> str | None:
    """Описание вакансии. None = объявление снято (404) или недоступно."""
    code = base64.b64encode(job.raw.get("ref", job.id.split(":", 1)[1]).encode()).decode()
    for version in ("v4", "v3"):
        data = fetcher.get_json(f"{BASE}/pc/{version}/jobdetails/{code}", headers=HEADERS)
        if data:
            text = data.get("stellenangebotsBeschreibung") or data.get("stellenbeschreibung") or ""
            if not job.company:
                job.company = data.get("firma") or data.get("arbeitgeber") or ""
            extra = detail_extra(data)
            if extra:
                job.raw["extra"] = extra
                if not job.external_url and not job.apply_url:
                    job.apply_url = pick_apply_url(extra.get("urls", {}))
            return clean_text(text)
    return None
