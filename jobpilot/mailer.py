"""Отправка через SMTP (Gmail) с честными состояниями и проверка ответов через IMAP.

Состояния: queued → attempted (записано ДО связи с сервером) → sent (сервер принял письмо)
или send_unknown (связь оборвалась во время передачи — повторно НЕ отправляется) или bounced.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

log = logging.getLogger("jobpilot.mail")


class MailError(Exception):
    pass


class AuthError(MailError):
    pass


def build_message(applicant: dict, to: str, subject: str, body: str, attachment: Path | None, attachment_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((applicant["name"], applicant["email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=applicant["email"].split("@")[-1])
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment_name)
    return msg


def _connect(settings, smtp_factory=None):
    host = settings.secret("SMTP_HOST", "smtp.gmail.com")
    port = int(settings.secret("SMTP_PORT", "465"))
    if smtp_factory:
        return smtp_factory(host, port)
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=60, context=ssl.create_default_context())
    s = smtplib.SMTP(host, port, timeout=60)
    s.starttls(context=ssl.create_default_context())
    return s


def deliver(settings, msg: EmailMessage, smtp_factory=None) -> str:
    """Возвращает 'sent' | 'bounced:<причина>' | 'unknown:<причина>'. Ошибки до передачи — исключения."""
    phase = "connect"
    try:
        smtp = _connect(settings, smtp_factory)
        phase = "login"
        smtp.login(settings.secret("SMTP_USER"), settings.secret("SMTP_PASSWORD"))
        phase = "send"
        refused = smtp.send_message(msg)
        try:
            smtp.quit()
        except Exception:
            pass
        if refused:
            return f"bounced:адрес отклонён {refused}"
        return "sent"
    except smtplib.SMTPAuthenticationError as exc:
        raise AuthError(f"Gmail не принял логин/пароль приложения: {exc.smtp_code}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        return f"bounced:адрес отклонён {list(exc.recipients)}"
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        return f"bounced:сервер отклонил письмо {getattr(exc, 'smtp_code', '')}"
    except Exception as exc:
        if phase in ("connect", "login"):
            raise MailError(f"нет связи с почтовым сервером: {exc}") from exc
        return f"unknown:{type(exc).__name__}: {exc}"


def send_job(settings, store, row, smtp_factory=None) -> str:
    applicant = settings.applicant
    cv = settings.cv_path
    if not cv.exists():
        raise MailError(f"не найден файл резюме {cv}")
    msg = build_message(applicant, row["email"], row["subject"], row["body"], cv, applicant.get("cv_attachment_name", cv.name))
    # Сначала фиксируем попытку — если программа упадёт, письмо не уйдёт второй раз
    store.update(row["id"], "attempted", f"to {row['email']}", message_id=msg["Message-ID"],
                 sent_at=datetime.now().isoformat(timespec="seconds"))
    try:
        outcome = deliver(settings, msg, smtp_factory)
    except MailError:
        # ничего не передано — возвращаем в очередь
        store.update(row["id"], "queued", "отправка не началась", sent_at=None, message_id=None)
        raise
    if outcome == "sent":
        store.update(row["id"], "sent", f"принято сервером, to {row['email']}")
        out = settings.data_dir / "sent"
        out.mkdir(exist_ok=True)
        (out / f"{datetime.now():%Y%m%d-%H%M%S}_{re.sub(r'[^A-Za-z0-9]+', '_', row['company'])[:40]}.eml").write_bytes(msg.as_bytes())
    elif outcome.startswith("bounced:"):
        store.update(row["id"], "bounced", outcome.split(":", 1)[1])
    else:
        store.update(row["id"], "send_unknown", outcome.split(":", 1)[1])
    return outcome.split(":", 1)[0]


def send_self(settings, subject: str, body: str, smtp_factory=None) -> None:
    applicant = settings.applicant
    msg = EmailMessage()
    msg["From"] = formataddr(("JobPilot Auto", applicant["email"]))
    msg["To"] = applicant["email"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    outcome = deliver(settings, msg, smtp_factory)
    if outcome != "sent":
        raise MailError(f"сводка не отправлена: {outcome}")


# ---------------------------------------------------------------- IMAP
AUTO_REPLY = re.compile(r"eingangsbest|eingang (ihrer|deiner) bewerbung|(ihre|deine) bewerbung ist (bei uns )?eingegangen|automatische antwort|auto-?reply|"
                        r"automatic reply|out of office|abwesen|we have received your application|thank you for your application|"
                        r"vielen dank f(ü|ue)r (ihre|deine) bewerbung|wir haben (ihre|deine) bewerbung (erhalten|bekommen)|bewerbung erhalten", re.I)
JOB_BOARDS = ("arbeitsagentur", "stepstone", "linkedin", "indeed", "xing", "glassdoor", "monster", "stellenwerk", "jobware", "kununu")
ATS_DOMAINS = ("personio", "softgarden", "concludis", "greenhouse", "lever.co", "recruitee", "dvinci", "join.com", "smartrecruiters",
               "ashbyhq", "workable", "workday", "successfactors", "umantis", "rexx", "onlyfy", "jobs.", "karriere.")
LEGAL = {"gmbh", "ag", "se", "mbh", "kg", "kgaa", "co", "co.", "e.v.", "ev", "ohg", "ug", "gbr", "und", "der", "die", "das",
         "abteilung", "hamburg", "deutschland", "germany", "group", "gruppe", "holding", "the"}


def _base_domain(url: str) -> str:
    host = re.sub(r"^https?://", "", url or "").split("/")[0].split(":")[0].lower()
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else ""


def _company_word(name: str) -> str:
    words = [w for w in re.split(r"[\s,&|/()\-–]+", name or "") if w]
    for w in words[:3]:
        if len(w) >= 4 and w.isascii() and w.lower() not in LEGAL:
            return w
    return ""
INVITE = re.compile(r"vorstellungsgespr|kennenlern|interview|telefonat|termin|einladung|gespräch", re.I)
REJECT = re.compile(r"leider|absage|nicht berücksichtigen|anderen kandidat|unfortunately|not to move forward", re.I)
GENERIC_DOMAINS = {"gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "outlook.com", "hotmail.com", "yahoo.com", "t-online.de"}


def _dec(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _imap_date(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return d.strftime("%d-%b-%Y")


OUT_OF_OFFICE = re.compile(r"abwesen|out of office|nicht im b(ü|ue)ro|automatic reply|automatische antwort|auto-?reply", re.I)


def classify_reply(subject: str, text: str) -> str:
    """confirm (подтверждение получения) | ooo (автоответ об отсутствии) | reject | invite | other"""
    blob = subject + " " + text
    if OUT_OF_OFFICE.search(blob):
        return "ooo"
    if REJECT.search(blob):
        return "reject"
    if AUTO_REPLY.search(blob):
        return "confirm"
    if INVITE.search(blob):
        return "invite"
    return "other"


def _fetch(imap, mid) -> tuple[str, str, str]:
    typ, msgdata = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)] BODY.PEEK[TEXT]<0.3000>)")
    head_b, text_b = b"", b""
    for part in msgdata:   # сервер может вернуть заголовки и текст в любом порядке — различаем по описанию части
        if not isinstance(part, tuple) or len(part) < 2:
            continue
        desc = part[0] if isinstance(part[0], bytes) else str(part[0]).encode()
        data = part[1] if isinstance(part[1], bytes) else str(part[1]).encode()
        if b"HEADER" in desc.upper():
            head_b = data
        else:
            text_b = data
    head = email.message_from_bytes(head_b) if head_b else None
    subject = _dec(head["Subject"]) if head else ""
    sender = _dec(head["From"]) if head else ""
    text = text_b.decode("utf-8", errors="replace")
    return subject, sender, text


def _record(store, r, kind: str, subject: str, notes: list[str]) -> bool:
    """Записывает ответ. True — это настоящий ответ (дальше по этой вакансии не смотрим)."""
    if kind == "ooo":
        return False
    if kind == "confirm":
        if not (r["response_note"] or "").startswith("Получение подтверждено"):
            store.update(r["id"], None, response_note=f"Получение подтверждено: {subject[:120]}")
            notes.append(f"{r['company']}: работодатель подтвердил получение")
        return False
    hint = {"reject": "похоже на отказ", "invite": "похоже на приглашение"}.get(kind, "прочитай письмо")
    store.update(r["id"], "response", hint, response_at=datetime.now().isoformat(timespec="seconds"),
                 response_note=f"{subject[:150]} — {hint}")
    notes.append(f"{r['company']}: ответ «{subject[:80]}» — {hint}")
    return True


def portal_criteria(row) -> list[tuple[str, str]]:
    """Как искать письма по заявке через форму: домен сайта формы и/или название компании."""
    import json as _json
    out: list[tuple[str, str]] = []
    try:
        form = (_json.loads(row["extra"] or "{}") if "extra" in row.keys() else {}).get("form") or {}
    except Exception:
        form = {}
    dom = _base_domain(form.get("url", "") or row["apply_url"] or "")
    if dom and not any(a in dom for a in ATS_DOMAINS) and not any(b in dom for b in JOB_BOARDS):
        out.append(("FROM", dom))
    word = _company_word(row["company"])
    if word:
        out.append(("TEXT", word))
    return out


def _repair_blank_responses(store) -> None:
    """Ответы, записанные с пустой темой (заголовки письма не прочитались), возвращаю на прежний статус,
    чтобы письмо перечиталось и классифицировалось заново."""
    for r in store.by_status("response"):
        if not (r["response_note"] or "").startswith(" — "):
            continue
        prev = store.previous_status(r["id"], "response")
        if prev:
            store.update(r["id"], prev, "перечитаю ответ: тема письма не прочиталась", response_at=None, response_note=None)


def check_inbox(settings, store, imap_factory=None) -> list[str]:
    _repair_blank_responses(store)
    rows = store.by_status("sent", "send_unknown", "submitted", "applied_manually")
    if not rows:
        return []
    host = settings.secret("IMAP_HOST", "imap.gmail.com")
    imap = imap_factory(host) if imap_factory else imaplib.IMAP4_SSL(host)
    imap.login(settings.secret("SMTP_USER"), settings.secret("SMTP_PASSWORD"))
    notes: list[str] = []
    try:
        # 1) неясные отправки: ищем письмо в «Отправленных» по Message-ID
        unknown = [r for r in rows if r["status"] == "send_unknown" and r["message_id"]]
        if unknown:
            sent_box = None
            typ, boxes = imap.list()
            for b in boxes or []:
                line = b.decode(errors="replace") if isinstance(b, bytes) else str(b)
                if "\\Sent" in line:
                    sent_box = line.split(' "/" ')[-1].strip()
                    break
            if sent_box:
                imap.select(sent_box, readonly=True)
                for r in unknown:
                    typ, data = imap.search(None, "HEADER", "Message-ID", r["message_id"])
                    if data and data[0].split():
                        store.update(r["id"], "sent", "найдено в «Отправленных»")
                        notes.append(f"{r['company']}: письмо найдено в «Отправленных»")
        # 2) ответы и возвраты во «Входящих»
        imap.select("INBOX", readonly=True)
        for r in store.by_status("sent"):
            since = _imap_date(r["sent_at"])
            addr = (r["email"] or "").lower()
            domain = addr.split("@")[-1]
            who = addr if domain in GENERIC_DOMAINS else domain
            typ, data = imap.search(None, "SINCE", since, "FROM", who)
            ids = data[0].split() if data and data[0] else []
            for mid in ids[-3:]:
                subject, sender, text = _fetch(imap, mid)
                if _record(store, store.get(r["id"]), classify_reply(subject, text), subject, notes):
                    break
            typ, data = imap.search(None, "SINCE", since, "FROM", "mailer-daemon", "BODY", addr)
            if data and data[0].split():
                store.update(r["id"], "bounced", "возврат: адрес не принял письмо")
                notes.append(f"{r['company']}: письмо вернулось")
        # 3) заявки через формы: подтверждения ATS и ответы (по домену сайта формы или названию компании)
        for r in store.by_status("submitted", "applied_manually"):
            if not r["sent_at"]:
                continue
            since = _imap_date(r["sent_at"])
            seen: set = set()
            done = False
            for crit, value in portal_criteria(r):
                if done:
                    break
                typ, data = imap.search(None, "SINCE", since, crit, value)
                ids = [i for i in (data[0].split() if data and data[0] else []) if i not in seen]
                for mid in ids[-3:]:
                    seen.add(mid)
                    subject, sender, text = _fetch(imap, mid)
                    if any(b in sender.lower() for b in JOB_BOARDS):
                        continue
                    if crit == "TEXT" and not re.search(r"bewerb|application|applying", subject + " " + text[:1500], re.I):
                        continue
                    if _record(store, store.get(r["id"]), classify_reply(subject, text), subject, notes):
                        done = True
                        break
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return notes
