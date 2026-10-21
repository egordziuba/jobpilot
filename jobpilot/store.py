"""Единый журнал: SQLite. Вакансии, статусы, история событий, прошлые заявки."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .textutil import company_key, title_key

STATUS_RU = {
    "filtered": "Отсеяна",
    "verified": "Проверена",
    "prepared": "Подготовлена",
    "queued": "В очереди на отправку",
    "attempted": "Попытка отправки",
    "sent": "Отправка подтверждена сервером",
    "send_unknown": "Результат отправки неясен",
    "bounced": "Возврат письма",
    "response": "Ответ работодателя",
    "interview": "Интервью",
    "offer": "Оффер",
    "rejected": "Отказ",
    "applied_manually": "Подано вручную",
    "submitted": "Подано через форму (сайт подтвердил)",
    "skipped": "Пропущена",
    "closed": "Закрыта",
}
FINAL = {"sent", "bounced", "response", "interview", "offer", "rejected", "applied_manually", "submitted", "skipped", "closed",
         "send_unknown", "attempted"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  company_key TEXT NOT NULL,
  source TEXT, source_url TEXT, apply_url TEXT, external_url TEXT,
  company TEXT, title TEXT, location TEXT, posted TEXT, deadline TEXT,
  description TEXT, language TEXT,
  tier TEXT, score REAL, themes TEXT, reqs TEXT, gaps TEXT,
  channel TEXT, email TEXT, contact TEXT,
  status TEXT NOT NULL, reason TEXT,
  subject TEXT, body TEXT, message_id TEXT, approved INTEGER DEFAULT 0,
  first_seen TEXT, updated TEXT, sent_at TEXT, response_at TEXT, response_note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_dedupe ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT, at TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS history (
  company_key TEXT, title_key TEXT, email TEXT, date TEXT, channel TEXT, note TEXT,
  UNIQUE(company_key, title_key, email)
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "extra" not in cols:  # миграция: адрес работодателя, ссылки, данные формы
            self.db.execute("ALTER TABLE jobs ADD COLUMN extra TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ чтение
    def get(self, job_id: str):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def find_dupe(self, dkey: str):
        return self.db.execute("SELECT * FROM jobs WHERE dedupe_key=?", (dkey,)).fetchone()

    def by_status(self, *statuses: str):
        q = ",".join("?" * len(statuses))
        return self.db.execute(f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY score DESC, first_seen", statuses).fetchall()

    def all(self):
        return self.db.execute("SELECT * FROM jobs ORDER BY updated DESC").fetchall()

    def sent_since(self, since: datetime) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE sent_at IS NOT NULL AND sent_at >= ? AND COALESCE(channel,'email')='email' "
            "AND status IN ('attempted','sent','send_unknown','bounced','response','interview','offer','rejected')",
            (since.isoformat(timespec="seconds"),),
        ).fetchone()
        return int(row[0])

    def company_recent(self, ckey: str, days: int, exclude_id: str = "") -> bool:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        row = self.db.execute(
            "SELECT 1 FROM jobs WHERE company_key=? AND id<>? AND sent_at IS NOT NULL AND sent_at>=? LIMIT 1",
            (ckey, exclude_id, since),
        ).fetchone()
        return row is not None

    def in_history(self, company: str, title: str, email: str = "") -> str | None:
        ck, tk = company_key(company), title_key(title)
        rows = self.db.execute("SELECT * FROM history WHERE company_key=? OR (email<>'' AND email=?)", (ck, email.lower())).fetchall()
        for r in rows:
            if r["email"] and email and r["email"] == email.lower():
                return f"на этот адрес уже писали ({r['date']})"
            if r["company_key"] == ck and (not r["title_key"] or r["title_key"] == tk):
                return f"уже подавался в эту компанию ({r['date'] or 'дата не указана'})"
        return None

    def previous_status(self, job_id: str, current: str) -> str:
        """Последний статус из истории, отличный от current."""
        row = self.db.execute("SELECT status FROM events WHERE job_id=? AND status<>? AND status<>'' ORDER BY id DESC LIMIT 1",
                              (job_id, current)).fetchone()
        return row[0] if row else ""

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))
        self.db.commit()

    def events(self, job_id: str):
        return self.db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()

    # ------------------------------------------------------------ запись
    def log(self, job_id: str, status: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO events(job_id, at, status, detail) VALUES (?,?,?,?)", (job_id, now(), status, detail))

    def insert(self, job, analysis, status: str, reason: str = "") -> None:
        from .textutil import dedupe_key
        t = now()
        self.db.execute(
            """INSERT OR IGNORE INTO jobs(id, dedupe_key, company_key, source, source_url, apply_url, external_url,
               company, title, location, posted, deadline, description, language, tier, score, themes, reqs, gaps,
               channel, email, contact, status, reason, first_seen, updated, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.id, dedupe_key(job.company, job.title), company_key(job.company), job.source, job.source_url,
                job.apply_url, job.external_url, job.company, job.title,
                (analysis.location_note if analysis else job.location), job.posted,
                (analysis.deadline if analysis else job.deadline), job.description,
                analysis.language if analysis else "", analysis.tier if analysis else "",
                analysis.score if analysis else 0, json.dumps(analysis.themes if analysis else []),
                json.dumps(analysis.reqs if analysis else [], ensure_ascii=False),
                json.dumps(analysis.gaps if analysis else [], ensure_ascii=False),
                analysis.channel if analysis else "", analysis.email if analysis else "",
                json.dumps(analysis.contact if analysis else {}, ensure_ascii=False), status, reason, t, t,
                json.dumps((job.raw or {}).get("extra", {}), ensure_ascii=False),
            ),
        )
        self.log(job.id, status, reason)
        self.db.commit()

    def update(self, job_id: str, status: str | None = None, detail: str = "", **fields) -> None:
        sets, vals = ["updated=?"], [now()]
        if status:
            sets.append("status=?")
            vals.append(status)
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(job_id)
        self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
        if status:
            self.log(job_id, status, detail)
        self.db.commit()

    def add_history(self, company: str, title: str, email: str, date: str, channel: str, note: str = "") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO history(company_key, title_key, email, date, channel, note) VALUES (?,?,?,?,?,?)",
            (company_key(company), title_key(title) if title else "", (email or "").lower(), date, channel, note),
        )

    MARK_FROM = {"filtered", "verified", "prepared", "queued"}
    MARK_APPLIED = {"submitted", "applied_manually", "sent"}

    def import_marks_csv(self, path: Path) -> int:
        """data/marks.csv: статусы, поставленные вне программы (подача через форму, закрытые вакансии).
        Колонки: id,status,date,company,title,channel,note. Повторный импорт безопасен."""
        if not path.exists():
            return 0
        n = 0
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                jid, status = (row.get("id") or "").strip(), (row.get("status") or "").strip()
                if not jid or jid.startswith("#") or status not in STATUS_RU:
                    continue
                company, title = (row.get("company") or "").strip(), (row.get("title") or "").strip()
                date, note = (row.get("date") or "").strip(), (row.get("note") or "").strip()
                if status in self.MARK_APPLIED and company:
                    self.add_history(company, title, "", date, (row.get("channel") or "").strip(), note)
                cur = self.get(jid)
                if cur is not None and cur["status"] in self.MARK_FROM:
                    fields = {"sent_at": date} if status in self.MARK_APPLIED and date else {}
                    self.update(jid, status, "из marks.csv" + (f": {note}" if note else ""), **fields)
                    n += 1
        self.db.commit()
        return n

    def import_history_csv(self, path: Path) -> int:
        if not path.exists():
            return 0
        n = 0
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                company = (row.get("company") or "").strip()
                if not company or company.startswith("#"):
                    continue
                self.add_history(company, (row.get("title") or "").strip(), (row.get("email") or "").strip(),
                                 (row.get("date") or "").strip(), (row.get("channel") or "").strip(), (row.get("note") or "").strip())
                n += 1
        self.db.commit()
        return n
