"""Отправка через SMTP (Gmail) с честными состояниями и проверка ответов через IMAP.

Состояния: queued → attempted (записано ДО связи с сервером) → sent (сервер принял письмо)
или send_unknown (связь оборвалась во время передачи — повторно НЕ отправляется) или bounced.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default
from email.utils import formataddr, formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path

from .textutil import strip_html

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
AUTO_REPLY = re.compile(r"eingangsbest\w*|eingang (ihrer|deiner) bewerbung|(ihre|deine) bewerbung ist (bei uns )?eingegangen|automatische antwort|auto-?reply|"
                        r"automatic reply|out of office|abwesen|we have received your application|thank you for your application|"
                        r"vielen dank f(ü|ue)r (ihre|deine) bewerbung|wir haben (ihre|deine) bewerbung (erhalten|bekommen)|bewerbung erhalten|"
                        r"unterlagen sind (sicher |gut )?(bei uns )?eingegangen", re.I)
JOB_BOARDS = ("arbeitsagentur", "stepstone", "linkedin", "indeed", "xing", "glassdoor", "monster", "stellenwerk", "jobware", "kununu")
ATS_DOMAINS = ("personio", "softgarden", "concludis", "greenhouse", "lever.co", "recruitee", "dvinci", "join.com", "smartrecruiters",
               "workday", "successfactors", "umantis", "rexx", "onlyfy", "jobs.", "karriere.")
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


OUT_OF_OFFICE = re.compile(
    r"\bout[ -]of[ -](?:the[ -])?office\b|\babwesenheits(?:notiz|nachricht|assistent)\b|"
    r"\b(?:ich bin|bin derzeit|bin aktuell)\s+(?:derzeit\s+|aktuell\s+|bis\s+[^.!?]*?\s+)?abwesend\b|"
    r"\bnicht im b(?:ü|ue)ro\b|\b(?:I am|I'm)\s+(?:currently\s+)?(?:away|on (?:annual )?leave|on vacation)\b", re.I)
INVITE = re.compile(
    r"\bvorstellungsgespr(?:ä|ae)ch\w*|\bkennenlerngespr(?:ä|ae)ch\w*|"
    r"\b(?:(?:telefon|video|phone|telephone)[ -]?)?interview\w*|"
    r"\bladen\s+(?:wir\s+)?(?:Sie|dich|euch)\b[^.!?]*?\bein\b|"
    r"\b(?:Terminvorschl(?:ag|äge|aege)|wann h(?:ä|ae)tten Sie Zeit)\b", re.I)
SCHEDULING_LINK = re.compile(r"https?://[^\s<>]*(?:calendly|teams|zoom|meet)[^\s<>]*", re.I)
SCHEDULING_INVITE = re.compile(
    r"\b(?:please\s+)?(?:book|schedule|choose|pick|select)\s+[^.!?]*?\b(?:time|slot|meeting|call)\b|"
    r"\b(?:wir m(?:ö|oe)chten Sie|we (?:would like to|want to))\s+[^.!?]*?\b(?:kennenlernen|meet)\b|"
    r"\b(?:bitte\s+)?(?:w(?:ä|ae)hlen|buchen|vereinbaren) Sie\s+[^.!?]*?\b(?:Termin|Zeitfenster)\b", re.I)
INVITE_CONDITIONAL = re.compile(
    r"\bggf\b\.?|\b(?:gegebenenfalls|falls|sofern|wenn|sollten Sie|solltest du|bei Interesse|"
    r"if|should you|once|in case)\b", re.I)
INVITE_NEGATED = re.compile(r"\b(?:nicht|kein\w*|not|unable|cannot|can['’]t|won['’]t)\b", re.I)
# Отдельное предложение остаётся единицей условия. Отрицание относится к своей
# части: «Freitag ist nicht möglich, wir laden Sie aber ... ein» — приглашение.
# Не разрываем вставки вроде «nicht, wie besprochen, zum Interview einladen».
INVITE_CLAUSES = re.compile(
    r";\s*|,\s*(?=(?:wir|ich|we|I)\b)|\b(?:aber|jedoch|but|however|instead|stattdessen)\b", re.I)
REJECT = re.compile(
    r"\bnicht\s+(?:weiter\s+)?ber(?:ü|ue)cksichtigen\b|"
    r"\bf(?:ü|ue)r\s+(?:eine andere Person|einen anderen Kandidaten|eine andere Kandidatin)\s+entschieden\b|"
    r"\bgegen\s+(?:Sie|dich|Ihre Bewerbung|deine Bewerbung)\s+entschieden\b|"
    r"\b(?:anderweitig|bereits)\s+besetzt\b|\bkeine\s+Stelle\s+anbieten\b|"
    r"\bposition has been filled\b|\bnot (?:to move|be moving) forward\b|"
    r"\b(?:move|moving) forward with other candidates\b|"
    r"\bdecided to proceed with other candidates\b|\bregret to inform\b", re.I)
ABSAGE = re.compile(r"\bAbsage\b", re.I)
# «…entschieden, den Prozess mit anderen Kandidaten fortzuführen» — отказ без слова «leider».
OTHER_CANDIDATES = re.compile(r"\bmit\s+anderen\s+(?:Kandidat|Bewerber)\w*", re.I)
CHOSE_OTHERS = re.compile(r"\b(?:entschieden|entscheiden|fortzuf(?:ü|ue)hren|fortzusetzen|weiterzuf(?:ü|ue)hren)\b", re.I)
ABSAGE_CONTEXT = re.compile(
    r"\b(?:Bewerbung\w*|Bewerber\w*|Kandidat\w*|Stelle|Position|Pr(?:ü|ue)fung|Auswahl)\b|"
    r"\bAbsage\s+(?:erteilen|mitteilen|senden)\b", re.I)
REQUEST_INFO = re.compile(
    r"\bbitte senden Sie uns\b[^.!?]*?\b(?:Unterlagen|Zeugnisse|Immatrikulationsbescheinigung)\b|"
    r"\bR(?:ü|ue)ckfrage\w*|\bcould you send\b", re.I)
REPLY_RU = {"invite": "приглашение", "reject": "отказ", "request_info": "запрос документов / уточнение",
            "confirm": "Получение подтверждено", "ooo": "автоответ об отсутствии", "other": "прочитай письмо"}


def classify_reply(subject: str, text: str) -> tuple[str, str]:
    """Приоритет правил: отсутствие → приглашение → отказ → уточнение → получение.

    Приглашение проверяем по предложениям: условия исключают его, отрицание
    проверяем в соответствующей части предложения. Условное обещание интервью
    остаётся подтверждением рассмотрения, если нет отказа или запроса документов.
    Возвращаем исходную решающую фразу/предложение как объяснение.
    """
    blob = re.sub(r"\s+", " ", subject) + ". " + re.sub(r"\s+", " ", text)
    if match := OUT_OF_OFFICE.search(blob):
        return "ooo", match.group(0)
    # Точка в «ggf.» не завершает предложение и не снимает условие.
    sentences = re.split(r"(?<!\bggf\.)(?<=[.!?])\s+", blob, flags=re.I)
    conditional_invite = ""
    # HTML href может быть вынесен после текста: фраза остаётся в своём предложении.
    has_link = SCHEDULING_LINK.search(blob)
    for sentence in sentences:
        if not (INVITE.search(sentence) or (has_link and SCHEDULING_INVITE.search(sentence))):
            continue
        if INVITE_CONDITIONAL.search(sentence):
            conditional_invite = conditional_invite or sentence.strip()
            continue
        for clause in INVITE_CLAUSES.split(sentence):
            if INVITE_NEGATED.search(clause):
                continue
            match = INVITE.search(clause) or (has_link and SCHEDULING_INVITE.search(clause))
            if match:
                return "invite", match.group(0)
    for sentence in sentences:
        if match := REJECT.search(sentence):
            return "reject", match.group(0)
        if OTHER_CANDIDATES.search(sentence) and CHOSE_OTHERS.search(sentence):
            return "reject", sentence.strip()
        if ABSAGE.search(sentence) and ABSAGE_CONTEXT.search(sentence):
            return "reject", sentence.strip()
        if re.search(r"\bleider\b", sentence, re.I) and re.search(r"\b(?:nicht|keine\w*|andere\w*|absagen)\b", sentence, re.I):
            return "reject", sentence.strip()
        if re.search(r"\bunfortunately\b", sentence, re.I) and re.search(r"\b(?:not|other|unable)\b", sentence, re.I):
            return "reject", sentence.strip()
    for kind, rule in (("request_info", REQUEST_INFO), ("confirm", AUTO_REPLY)):
        if match := rule.search(blob):
            return kind, match.group(0)
    if conditional_invite:
        return "confirm", conditional_invite
    return "other", ""


def _fetch(imap, mid, metadata: dict | None = None) -> tuple[str, str, str]:
    typ, msgdata = imap.fetch(mid, "(BODY.PEEK[]<0.200000>)")
    if typ != "OK":
        raise MailError("не удалось прочитать ответ работодателя")
    whole_b, head_b, text_b = b"", b"", b""
    for part in msgdata:   # сервер может вернуть заголовки и текст в любом порядке — различаем по описанию части
        if not isinstance(part, tuple) or len(part) < 2:
            continue
        desc = part[0] if isinstance(part[0], bytes) else str(part[0]).encode()
        data = part[1] if isinstance(part[1], bytes) else str(part[1]).encode()
        desc = desc.upper()
        if b"BODY[]" in desc or b"RFC822" in desc:
            whole_b = data
        elif b"HEADER" in desc:
            head_b = data
        elif b"TEXT" in desc:
            text_b = data
    raw = whole_b or head_b + text_b
    if not raw:
        raise MailError("сервер вернул пустой ответ работодателя")
    msg = email.message_from_bytes(raw, policy=default)
    plain, html = [], []

    def collect(part):
        # Не обходим вложенные письма и multipart-вложения.
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return
        if part.get_content_maintype() == "multipart":
            for child in part.iter_parts():
                collect(child)
        elif part.get_content_type() in ("text/plain", "text/html"):
            try:
                content = part.get_content()
            except (LookupError, UnicodeError):
                content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
            if content.strip():
                if part.get_content_type() == "text/plain":
                    plain.append(content)
                else:
                    # strip_html убирает href; сохраняем ссылки для правил планирования.
                    links = re.findall(r'''href\s*=\s*["'](https?://[^"']+)["']''', content, re.I)
                    html.append(strip_html(content) + "\n" + "\n".join(links))

    collect(msg)
    if metadata is not None:
        metadata["key"] = str(msg.get("Message-ID") or hashlib.sha256(raw).hexdigest())
        try:
            metadata["date"] = parsedate_to_datetime(str(msg.get("Date", ""))).isoformat(timespec="seconds")
        except (ValueError, TypeError, OverflowError):
            metadata["date"] = datetime.now().isoformat(timespec="seconds")
    return _dec(msg["Subject"]), _dec(msg["From"]), "\n".join(plain or html)


def _record(store, r, kind: str, evidence: str, subject: str, notes: list[str], metadata: dict) -> bool:
    """Записывает ответ. True — это настоящий ответ (дальше по этой вакансии не смотрим)."""
    if kind == "ooo":
        return False
    key = "reply:" + hashlib.sha256((r["id"] + "\0" + metadata["key"]).encode()).hexdigest()
    if store.get_meta(key):
        return False
    hint = REPLY_RU[kind]
    note = f"{hint}: «{evidence}»" if evidence else f"{hint}: {subject[:150]}"
    if kind == "confirm":
        status = "submitted" if r["status"] == "send_unknown" and not r["message_id"] else r["status"]
        store.update(r["id"], status, note, response_note=note, response_at=metadata["date"])
        store.set_meta(key, kind)
        notes.append(f"{r['company']}: работодатель подтвердил получение — {note}")
        return False
    store.update(r["id"], "response", note, response_at=metadata["date"], response_note=note)
    store.set_meta(key, kind)
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
            for mid in ids:
                metadata = {}
                subject, sender, text = _fetch(imap, mid, metadata)
                if _record(store, store.get(r["id"]), *classify_reply(subject, text), subject, notes, metadata):
                    break
            typ, data = imap.search(None, "SINCE", since, "FROM", "mailer-daemon", "BODY", addr)
            if data and data[0].split():
                store.update(r["id"], "bounced", "возврат: адрес не принял письмо")
                notes.append(f"{r['company']}: письмо вернулось")
        # 3) заявки через формы: подтверждения ATS и ответы (по домену сайта формы или названию компании)
        forms = store.by_status("submitted", "applied_manually") + [
            r for r in store.by_status("send_unknown") if not r["message_id"]]
        for r in forms:
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
                for mid in ids:
                    seen.add(mid)
                    metadata = {}
                    subject, sender, text = _fetch(imap, mid, metadata)
                    if any(b in sender.lower() for b in JOB_BOARDS):
                        continue
                    if crit == "TEXT" and not re.search(r"bewerb|application|applying", subject + " " + text[:1500], re.I):
                        continue
                    kind, evidence = classify_reply(subject, text)
                    if (r["status"] == "send_unknown" and kind == "confirm" and crit == "TEXT"
                            and not any(a in sender.lower() for a in ATS_DOMAINS)
                            and _company_word(r["company"]).lower() not in sender.lower()):
                        continue
                    if _record(store, store.get(r["id"]), kind, evidence, subject, notes, metadata):
                        done = True
                        break
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return notes
