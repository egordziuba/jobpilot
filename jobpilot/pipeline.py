"""Один рабочий цикл: поиск → проверка → подготовка → отправка → сверка ответов → отчёт."""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from . import llm, mailer
import re

from .analyze import RULES_VERSION, analyze, rejected_title, title_has_core
from .models import Job
from .textutil import fold
from .letters import LetterError, compose
from .net import Fetcher
from .sources import ats, ba, manual
from .store import FINAL, Store
from .textutil import dedupe_key

log = logging.getLogger("jobpilot")

JSON_ATS = ("lever", "smartrecruiters", "ashby", "workable")


@dataclass
class RunResult:
    started: str = ""
    mode: str = "dry_run"
    collected: int = 0
    new: int = 0
    filtered: int = 0
    prepared: int = 0
    queued: int = 0
    sent: int = 0
    unknown: int = 0
    bounced: int = 0
    closed: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    filtered_reasons: dict[str, int] = field(default_factory=dict)


class RunLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < 3 * 3600:
                raise RuntimeError("другой запуск ещё идёт (data/run.lock). Если это не так — удали файл.")
        self.path.write_text(f"{os.getpid()} {datetime.now().isoformat()}", encoding="utf-8")
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def collect(settings, fetcher, result: RunResult) -> tuple[list, dict[str, set[str]]]:
    jobs = []
    live_ids: dict[str, set[str]] = {}
    jobs += manual.load(settings.data_dir)
    search = settings.search
    for block in search.get("ba", []):
        try:
            jobs += ba.search(fetcher, block, int(search.get("max_age_days", 21)))
        except Exception as exc:
            result.errors.append(f"Arbeitsagentur ({block.get('where') or 'remote'}): {exc}")
    for kind, func, identifier in (
        ("personio", ats.personio, "slug"), ("greenhouse", ats.greenhouse, "token"),
        ("recruitee", ats.recruitee, "slug"), ("lever", ats.lever, "company"),
        ("smartrecruiters", ats.smartrecruiters, "company"), ("ashby", ats.ashby, "board"),
        ("workable", ats.workable, "subdomain"),
    ):
        for entry in search.get(kind, []):
            key = f"{kind}:{entry.get(identifier)}"
            try:
                found = func(fetcher, entry)
                jobs += found
                live_ids[key] = {j.id for j in found}
            except Exception as exc:
                result.errors.append(f"{key}: {exc}")
    return jobs, live_ids


def is_live(settings, fetcher, row, live_ids) -> bool:
    if row["source"] == "ba":
        from .models import Job
        job = Job(id=row["id"], source="ba", company=row["company"], title=row["title"], raw={"ref": row["id"].split(":", 1)[1]})
        try:
            return ba.fetch_detail(fetcher, job) is not None
        except Exception:
            return True  # сеть недоступна — не закрываем вакансию, но и не считаем это доказательством
    if row["source"] in ("personio", "greenhouse", "recruitee", *JSON_ATS):
        _, slug, _ = row["id"].split(":", 2)
        ids = live_ids.get(f"{row['source']}:{slug}")
        # New JSON sources require evidence from this run before sending.
        if ids is None and row["source"] in JSON_ATS:
            log.warning("Cannot verify posting at source: %s", row["id"])
            return False
        return True if ids is None else row["id"] in ids
    return True


def decide(a, settings) -> tuple[str, str]:
    """Статус после анализа: queued (уйдёт сам) | prepared (нужен ты) | verified (по желанию, без задачи)."""
    cfg_run = settings.run
    auto_tiers = set(cfg_run.get("auto_send_tiers", ["close"]))
    min_score = float(cfg_run.get("min_score_auto", 7))
    if a.channel == "email":
        if a.tier in auto_tiers and a.score >= min_score and not a.gaps:
            return "queued", "автоотправка разрешена"
        if a.tier == "close" or a.score >= 7:
            if a.gaps:
                return "prepared", "пробелы: " + "; ".join(a.gaps)
            if a.tier not in auto_tiers:
                return "prepared", "смежная — отправить только с твоего согласия"
            return "prepared", f"балл {a.score} ниже порога {min_score}"
        return "verified", "смежная, слабое совпадение — по желанию"
    if a.tier == "close":
        return "prepared", "подача только через сайт работодателя"
    return "verified", "смежная, подача только через сайт — по желанию"


def reclassify(settings, store, result: RunResult, today: date) -> None:
    """После обновления правил пересматривает ещё не отправленные вакансии по сохранённому тексту."""
    if store.get_meta("rules_version") == str(RULES_VERSION):
        return
    changed = 0
    for row in store.by_status("verified", "prepared", "queued"):
        if row["approved"] or not row["body"]:
            continue
        job = Job(id=row["id"], source=row["source"], company=row["company"], title=row["title"],
                  source_url=row["source_url"] or "", apply_url=row["apply_url"] or "", external_url=row["external_url"] or "",
                  location=row["location"] or "", deadline=row["deadline"] or "", description=row["description"] or "",
                  email_hint=(row["email"] or "") if row["source"] == "manual" else "",
                  raw={"ref": row["id"].split(":", 1)[1]} if row["source"] == "ba" else {})
        a = analyze(job, settings.geo, today, skip_location=True)
        if not a.ok:
            store.update(row["id"], "filtered", f"новые правила: {a.reason}", reason=a.reason)
            changed += 1
            continue
        status, why = decide(a, settings)
        if status != row["status"] or why != row["reason"] or a.channel != (row["channel"] or "") or a.email != (row["email"] or ""):
            store.update(row["id"], status if status != row["status"] else None, f"новые правила: {why}", reason=why,
                         tier=a.tier, score=a.score, channel=a.channel, email=a.email,
                         contact=json.dumps(a.contact, ensure_ascii=False),
                         gaps=json.dumps(a.gaps, ensure_ascii=False), reqs=json.dumps(a.reqs, ensure_ascii=False))
            changed += 1
    store.set_meta("rules_version", str(RULES_VERSION))
    if changed:
        result.notes.append(f"Правила отбора обновлены, пересмотрено вакансий: {changed}")


def process_new(settings, store, fetcher, jobs, result: RunResult, today: date) -> None:
    use_llm = bool(settings.cfg.get("llm", {}).get("enabled")) and bool(settings.secret("ANTHROPIC_API_KEY"))
    seen_keys: set[str] = set()

    for job in jobs:
        if store.get(job.id):
            continue
        dkey = dedupe_key(job.company, job.title)
        if dkey in seen_keys or store.find_dupe(dkey):
            continue
        seen_keys.add(dkey)
        result.new += 1
        result.new_ids.append(job.id)

        def filtered(reason: str, analysis=None) -> None:
            store.insert(job, analysis, "filtered", reason)
            result.filtered += 1
            key = reason.split(" (")[0].split(":")[0]
            result.filtered_reasons[key] = result.filtered_reasons.get(key, 0) + 1

        bad = rejected_title(job.title)
        if bad:
            filtered(f"не тот профиль ({bad})")
            continue
        if job.raw.get("nationwide") and job.lat is not None:
            title_f = fold(job.title)
            if not (job.homeoffice or re.search(r"remote|home ?office|mobil", title_f)):
                filtered("вне региона и не удалённая")
                continue
            if not title_has_core(job.title):
                filtered("мало совпадений по задачам (вне региона)")
                continue
        if job.needs_detail:
            try:
                desc = ba.fetch_detail(fetcher, job)
            except Exception as exc:
                result.errors.append(f"детали {job.id}: {exc}")
                result.new -= 1
                result.new_ids.pop()
                continue  # попробуем в следующий раз
            if desc is None:
                filtered("объявление недоступно")
                continue
            job.description = desc

        a = analyze(job, settings.geo, today)
        if not a.ok:
            filtered(a.reason, a)
            continue

        hist = store.in_history(job.company, job.title, a.email)
        if hist:
            store.insert(job, a, "skipped", hist)
            continue

        task = None
        if use_llm:
            task = llm.summarize_task(fetcher, settings.secret("ANTHROPIC_API_KEY"), settings.cfg["llm"].get("model", "claude-haiku-5-5"),
                                      job.description, a.language)
        try:
            letter = compose(job, a, settings.facts, settings.applicant, task_override=task)
        except LetterError as exc:
            store.insert(job, a, "verified", f"письмо не собрано: {exc}")
            continue

        status, why = decide(a, settings)
        store.insert(job, a, "verified", "проверена")
        store.update(job.id, status, why, subject=letter.subject, body=letter.body, reason=why)
        if status == "queued":
            result.queued += 1
        elif status == "prepared":
            result.prepared += 1


def expire(store, result: RunResult, today: date) -> None:
    for row in store.by_status("verified", "prepared", "queued"):
        if row["deadline"] and row["deadline"] < today.isoformat():
            store.update(row["id"], "closed", f"срок истёк {row['deadline']}")
            result.closed += 1


def send_queue(settings, store, fetcher, live_ids, result: RunResult, smtp_factory=None, sleep=time.sleep) -> None:
    cfg_run = settings.run
    midnight = datetime.combine(date.today(), datetime.min.time())
    remaining = min(int(cfg_run.get("max_emails_per_day", 5)) - store.sent_since(midnight), int(cfg_run.get("max_emails_per_run", 3)))
    if remaining <= 0:
        result.notes.append("Дневной лимит писем исчерпан — очередь продолжится завтра.")
        return
    pause = cfg_run.get("seconds_between_emails", [60, 150])
    cooldown = int(cfg_run.get("company_cooldown_days", 30))
    rows = sorted(store.by_status("queued"), key=lambda r: (r["deadline"] or "9999", -(r["score"] or 0)))
    sent_now = 0
    for row in rows:
        if sent_now >= remaining:
            break
        if not row["email"]:
            store.update(row["id"], "prepared", "нет адреса для отправки")
            continue
        if store.company_recent(row["company_key"], cooldown, row["id"]):
            store.update(row["id"], "prepared", f"компании уже писали за последние {cooldown} дн.")
            continue
        hist = store.in_history(row["company"], row["title"], row["email"])
        if hist:
            store.update(row["id"], "skipped", hist)
            continue
        if row["source"] in JSON_ATS and row["id"].rsplit(":", 1)[0] not in live_ids:
            # A failed/disabled board is not proof of removal. Keep it queued for retry.
            result.errors.append(f"Источник не проверен перед отправкой: {row['id']}")
            continue
        if not is_live(settings, fetcher, row, live_ids):
            store.update(row["id"], "closed", "объявление снято перед отправкой")
            result.closed += 1
            continue
        if sent_now:
            sleep(random.uniform(*pause))
        try:
            outcome = mailer.send_job(settings, store, row, smtp_factory)
        except mailer.AuthError as exc:
            result.errors.append(str(exc) + " — проверь SMTP_PASSWORD в .env. Отправка остановлена.")
            return
        except mailer.MailError as exc:
            result.errors.append(f"{exc}. Отправка остановлена до следующего запуска.")
            return
        if outcome == "sent":
            result.sent += 1
            sent_now += 1
        elif outcome == "unknown":
            result.unknown += 1
            sent_now += 1
        else:
            result.bounced += 1


def run(settings, fetcher=None, smtp_factory=None, imap_factory=None, today: date | None = None, sleep=time.sleep) -> RunResult:
    from . import report

    today = today or date.today()
    result = RunResult(started=datetime.now().isoformat(timespec="seconds"), mode="live" if settings.live else "dry_run")
    fetcher = fetcher or Fetcher(pause=float(settings.search.get("request_pause_seconds", 0.6)))
    with RunLock(settings.data_dir / "run.lock"):
        store = Store(settings.db_path)
        try:
            store.import_history_csv(settings.data_dir / "already_applied.csv")
            marked = store.import_marks_csv(settings.data_dir / "marks.csv")
            if marked:
                result.notes.append(f"Статусы из marks.csv: {marked}")
            reclassify(settings, store, result, today)
            jobs, live_ids = collect(settings, fetcher, result)
            result.collected = len(jobs)
            process_new(settings, store, fetcher, jobs, result, today)
            expire(store, result, today)
            store.import_marks_csv(settings.data_dir / "marks.csv")   # новые находки, которые уже поданы вне программы
            if settings.smtp_ready and settings.run.get("check_inbox", True):
                try:
                    result.notes += mailer.check_inbox(settings, store, imap_factory)
                except Exception as exc:
                    result.errors.append(f"проверка почты: {exc}")
            if settings.live:
                if settings.smtp_ready:
                    send_queue(settings, store, fetcher, live_ids, result, smtp_factory, sleep)
                else:
                    result.errors.append("Режим live, но в .env нет SMTP_USER/SMTP_PASSWORD — письма не отправлены.")
            path = report.write_html(settings, store, result)
            result.notes.append(f"Отчёт: {path}")
            if settings.smtp_ready and settings.run.get("report_email_to_self", True) and report.worth_mailing(store, result):
                try:
                    subj, body = report.summary_email(settings, store, result)
                    mailer.send_self(settings, subj, body, smtp_factory)
                except Exception as exc:
                    result.errors.append(f"сводка на почту: {exc}")
            (settings.data_dir / "last_run.json").write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            store.close()
    return result
