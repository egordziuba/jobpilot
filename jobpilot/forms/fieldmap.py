"""Чистая логика автозаполнения (без браузера): что это за поле, чем его заполнить, что оставить тебе.

Правило: заполняются только поля, ответ на которые есть в config.toml / резюме. Зарплата и даты
без настройки, вопросы про разрешение на работу, Schwerbehinderung, свободные вопросы работодателя и
галочки согласия НЕ заполняются автоматически — они попадают в список «сделай сам».
"""
from __future__ import annotations

import re
from datetime import date as _date
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- профиль


@dataclass
class Profile:
    salutation: str = "Herr"
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""          # улица с номером дома
    zip: str = ""
    city: str = ""
    state: str = ""
    country: str = "Deutschland"
    birth_date: str = ""      # пусто = не заполнять
    earliest_start: str = ""  # пусто = оставить тебе
    linkedin: str = ""
    hours_max: int = 0
    availability: str = ""
    accept_privacy: bool = False
    available_until: str = ""
    salary_hourly: float = 0
    hours_min: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def street_name(self) -> str:
        m = re.match(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)$", self.street.strip())
        return m.group(1).strip() if m else self.street

    @property
    def house_no(self) -> str:
        m = re.match(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)$", self.street.strip())
        return m.group(2).strip() if m else ""


def build_profile(settings) -> Profile:
    ap = settings.applicant
    fm = settings.cfg.get("forms", {})
    hours = settings.cfg.get("application", {})
    name = ap.get("name", "").strip()
    first = ap.get("first_name") or (name.split(" ")[0] if name else "")
    last = name[len(first):].strip() if name.startswith(first) else (name.split(" ")[-1] if name else "")
    street, zip_, city = fm.get("street", ""), fm.get("zip", ""), fm.get("city", "")
    addr = ap.get("address", "")
    m = re.match(r"^\s*(.+?),\s*(\d{5})\s+(.+?)\s*$", addr)
    if m:
        street = street or m.group(1)
        zip_ = zip_ or m.group(2)
        city = city or m.group(3)
    return Profile(
        salutation=fm.get("salutation", "Herr"),
        first_name=first,
        last_name=last,
        email=ap.get("email", ""),
        phone=ap.get("phone", ""),
        street=street,
        zip=zip_,
        city=city,
        state=fm.get("state", ""),
        country=fm.get("country", "Deutschland"),
        birth_date=fm.get("birth_date", ""),
        earliest_start=fm.get("earliest_start", ""),
        linkedin=fm.get("linkedin_url", ""),
        hours_max=int(hours.get("hours_max", ap.get("hours_max", 0)) or 0),
        availability=ap.get("availability_de", ""),
        accept_privacy=bool(fm.get("accept_privacy", False)),
        available_until=fm.get("available_until", ""),
        salary_hourly=float(fm.get("salary_hourly", 0) or 0),
        hours_min=int(hours.get("hours_min", ap.get("hours_min", 0)) or 0),
    )


# --------------------------------------------------------------------------- классификация полей

def fold(text: str) -> str:
    t = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


# Порядок важен: более узкие правила раньше общих.
TEXT_RULES: list[tuple[str, str]] = [
    ("skip", r"captcha|honeypot|frc-captcha|\bsuche\b|search|sword|newsletter|autofill-helper|gutschein|coupon"),
    ("disability", r"schwerbehind|behinderung|disabilit|gleichstellung"),
    ("permit", r"arbeitserlaubnis|aufenthalt|work permit|visa|visum|staatsangehoerigkeit|nationalitaet|nationality|citizenship"),
    ("academic_title", r"akademisch|academic|honorific|\btitel\b"),
    ("salutation", r"anrede|salutation|\bgender\b|geschlecht|\bsex\b|ansprache|pronoun"),
    ("email_confirm", r"(e-?mail).{0,30}(wiederhol|bestaetig|confirm|repeat|erneut|nochmal)|(wiederhol|bestaetig|confirm|repeat).{0,20}(e-?mail)"),
    ("email", r"e-?mail"),
    ("first_name", r"vorname|first.?name|given.?name|\bfname\b|forename"),
    ("full_name", r"vor- ?und nachname|vollstaendiger name|full.?name|^(ihr |dein |your )?name\b|^name ?\*?$"),
    ("last_name", r"nachname|last.?name|family.?name|surname|\blname\b|familienname|zuname"),
    ("phone", r"telefon|phone|mobil|handy|\btel\b|rufnummer|mobile"),
    ("street", r"strasse|street|anschrift|adresse|address(?!.*e-?mail)"),
    ("house_no", r"hausnummer|hausnr|house.?(no|number)|street.?number"),
    ("zip", r"\bplz\b|postleitzahl|\bzip\b|zip.?code|postal|post.?code|postcode"),
    ("state", r"bundesland|\bstate\b|\bregion\b|province"),
    ("city", r"\bort\b|wohnort|\bstadt\b|\bcity\b|\btown\b|ortschaft|address-level2"),
    ("country", r"\bland\b|country|\bstaat\b"),
    ("birth_date", r"geburt|birth|\bbday\b|\bdob\b"),
    ("source", r"aufmerksam|quelle|\bsource\b|how did you (hear|find)|wie (bist|sind|haben|hast) (du|sie)|gefunden|herkunft|referr|kanal|channel|where did you|erfahren"),
    ("salary", r"gehalt|salary|verguetung|compensation|\blohn\b|jahresbrutto|einkommen|stundenlohn|wage"),
    ("available_until", r"bis wann.{0,60}verfuegbar|verfuegbar\s+bis|available\s+until|available_until"),
    ("start_date", r"eintritt|verfuegbar ab|verfuegbarkeit|\bstart|beginn|fruehest|earliest|available from|ab wann|kuendigungsfrist|notice period"),
    ("hours", r"wochenstunden|stunden pro woche|stunden/woche|hours per week|wochenarbeitszeit|stundenumfang|arbeitsumfang"),
    ("linkedin", r"linkedin"),
    ("xing", r"\bxing\b"),
    ("website", r"website|homepage|portfolio|github|webseite|\burl\b"),
    ("message", r"anschreiben|nachricht|message|cover|motivation|kommentar|comment|bemerkung|anmerkung|zusaetzlich|additional|mitteilung|freitext|ueber dich|about you"),
]

CHECKBOX_RULES: list[tuple[str, str]] = [
    ("skip", r"cybotcookiebot|cookie|newsletter|marketing"),
    ("disability", r"schwerbehind|behinderung|disabilit|gleichstellung"),
    ("talent_pool", r"talent.?pool|zukuenftig|future (job|position|opportunit|vacanc)|weitere (stellen|positionen)|aufbewahr|laenger speichern|longer|kandidatenpool|bewerberpool"),
    ("privacy", r"datenschutz|privacy|einwillig|consent|zustimm|dsgvo|gdpr|\bagb\b|terms|nutzungsbedingungen|kenntnis genommen|einverstanden|data protection"),
]

FILE_RULES: list[tuple[str, str]] = [
    ("autofill", r"automatisch(es)? ausfuellen|autofill|auto-fill|\bparse|wir fuellen|formular .{0,40}automatisch|import"),
    ("letter", r"anschreiben|cover|motivationsschreiben|anschr\b|coverletter|cover_letter"),
    ("cv", r"lebenslauf|\bcv\b|cvdocuments|resume|vita|curriculum"),
    ("certificates", r"zeugnis|certificate|zertifikat|nachweis|transcript|erdocuments|reference"),
    ("other", r"sonstig|weitere|\bother|anlage|attachment|additional|zusaetzlich|dokument|unterlagen|document|upload|datei|file|drag"),
]

# Ключи, которые программа заполняет сама (если есть значение).
AUTO_KEYS = {"salutation", "first_name", "last_name", "full_name", "email", "email_confirm", "phone", "street",
             "house_no", "zip", "city", "state", "country", "birth_date", "source", "start_date", "hours",
             "linkedin", "message", "available_until"}
# Ключи, которые оставляются тебе (зарплата — кроме настроенной почасовой ставки).
ASK_KEYS = {"salary": "зарплатные ожидания (считаются под эту роль и часы)",
            "permit": "вопрос про разрешение на работу / гражданство",
            "disability": "Schwerbehinderung (добровольно)",
            "academic_title": "академический титул"}


def _rules_match(rules, hay: str) -> str:
    for key, rx in rules:
        if re.search(rx, hay):
            return key
    return ""


def field_hay(d: dict) -> str:
    return fold(" ".join(str(d.get(k) or "") for k in ("label", "placeholder", "name", "id", "aria")))


AUTOCOMPLETE = {
    "given-name": "first_name", "family-name": "last_name", "name": "full_name", "email": "email",
    "tel": "phone", "tel-national": "phone", "street-address": "street", "address-line1": "street",
    "postal-code": "zip", "address-level2": "city", "address-level1": "state", "country": "country",
    "country-name": "country", "bday": "birth_date", "honorific-prefix": "academic_title", "sex": "salutation",
}


def classify(d: dict) -> str:
    """Тип поля по описанию из браузера (label/name/id/placeholder/autocomplete)."""
    tag, typ = d.get("tag", ""), (d.get("type") or "").lower()
    if typ in ("hidden", "submit", "button", "image", "reset", "search", "password"):
        return "skip"
    if typ == "file":
        return "file"
    hay = field_hay(d)
    if typ == "checkbox":
        return _rules_match(CHECKBOX_RULES, hay + " " + fold(d.get("context", ""))[:200]) or "checkbox_other"
    if typ == "radio":
        return "radio"
    if "helper" in fold(d.get("id", "")):
        return "skip"
    key = _rules_match(TEXT_RULES[:4], hay)  # skip / disability / permit / titel — раньше всего остального
    if key:
        return key
    ac = (d.get("autocomplete") or "").lower().split()
    for token in reversed(ac):
        if token in AUTOCOMPLETE:
            return AUTOCOMPLETE[token]
    if typ == "email":
        return "email_confirm" if re.search(TEXT_RULES[5][1], hay) else "email"
    if typ == "tel":
        return "phone"
    key = _rules_match(TEXT_RULES, hay)
    if key:
        return key
    if tag == "textarea":
        return "message"
    return "unknown"


def classify_file(d: dict) -> str:
    """cv / letter / certificates / other / autofill — сначала по подписи, потом по ближайшему тексту."""
    hay = field_hay(d)
    if hay:
        key = _rules_match(FILE_RULES, hay)
        if key:
            return key
    for ctx in d.get("contexts") or []:
        key = _rules_match(FILE_RULES, fold(ctx))
        if key:
            return key
    return "other"


# --------------------------------------------------------------------------- выбор значений

SALUTATION_OPTIONS = {
    "Herr": [r"^herr\b", r"^mr\.?\b", r"^maennlich$", r"^male$", r"^m$", r"maennlich", r"\bmale\b"],
    "Frau": [r"^frau\b", r"^ms\.?\b", r"^mrs\.?\b", r"^weiblich$", r"^female$", r"^w$"],
}
COUNTRY_OPTIONS = [r"^deutschland$", r"^germany$", r"^de$", r"^deu$", r"deutschland", r"germany"]
SOURCE_OPTIONS = {
    "ba": [r"bundesagentur", r"agentur fuer arbeit", r"arbeitsagentur", r"jobboerse der", r"\bba\b", r"arbeitsamt",
           r"jobsuche", r"jobboerse", r"online-?stellenboerse", r"stellenboerse", r"jobportal", r"internet"],
    "company": [r"karriereseite", r"karriere-?(web)?seite", r"unternehmens(website|homepage|webseite|seite)",
                r"firmen(website|homepage|webseite)", r"homepage", r"website", r"webseite", r"career", r"karriere"],
    "stellenwerk": [r"stellenwerk", r"hochschul", r"universitaet", r"uni\b", r"jobboerse", r"stellenboerse", r"internet"],
    "linkedin": [r"linkedin"],
    "stepstone": [r"stepstone"],
    "indeed": [r"indeed"],
    "xing": [r"xing"],
}
PLACEHOLDER_OPTION = re.compile(r"^(bitte |please )?(waehlen|auswaehlen|select|choose|--|-|\.\.\.)|^$|\*$|^anrede|^wie bist|^land$")
HOURLY_OPTIONS = [r"\bpro stunde\b", r"\bper hour\b"]
ANNUAL_SALARY = re.compile(r"\bp\s*\.\s*a\s*\.|\bpro jahr\b|\bjaehrlich\b|\bannual\b")


def pick_option(options: list[str], patterns: list[str]) -> str | None:
    folded = [(o, fold(o)) for o in options]
    for rx in patterns:
        for orig, f in folded:
            if PLACEHOLDER_OPTION.search(f) and not re.search(rx, f):
                continue
            if re.search(rx, f):
                return orig
    return None


def source_kind(job: dict) -> str:
    via = fold(job.get("via") or "")
    for k in ("linkedin", "stepstone", "indeed", "xing", "stellenwerk"):
        if k in via:
            return k
    src = job.get("source") or ""
    if src == "ba":
        return "ba"
    return "company"


@dataclass
class Action:
    idx: int
    key: str
    kind: str            # fill | select | check | files
    value: object
    label: str = ""


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)        # что сделать тебе
    skipped: list[str] = field(default_factory=list)     # что сознательно не трогаю
    files: dict[str, list[str]] = field(default_factory=dict)  # idx → пути (для отчёта)


def _label(d: dict) -> str:
    return (d.get("label") or d.get("placeholder") or d.get("name") or d.get("id") or "поле").strip()[:70]


def value_for(key: str, d: dict, p: Profile, job: dict, letter_text: str):
    """(вид действия, значение) или None, если заполнять не нужно/нельзя."""
    tag, typ = d.get("tag"), (d.get("type") or "").lower()
    opts = d.get("options") or []
    if key == "salary":
        if p.salary_hourly <= 0 or ANNUAL_SALARY.search(field_hay(d)):
            return None
        if tag == "select":
            o = pick_option(opts, HOURLY_OPTIONS)
            return ("select", o) if o else None
        n = f"{p.salary_hourly:g}"
        if typ == "number":
            return ("fill", n)
        text = f"{n} € brutto pro Stunde"
        if re.search(r"stundenzahl|wochenstunden|stunden pro woche|hours per week", fold(d.get("label", ""))):
            minimum, maximum = p.hours_min, p.hours_max
            if minimum > 0 or maximum > 0:
                weekly = (f"{minimum}–{maximum}" if minimum > 0 and maximum > 0 and minimum != maximum
                          else str(minimum if minimum > 0 else maximum))
                text += f" bei {weekly} Stunden pro Woche"
        return ("fill", text)
    if tag == "select":
        if key == "salutation":
            o = pick_option(opts, SALUTATION_OPTIONS.get(p.salutation, []))
        elif key == "country":
            o = pick_option(opts, COUNTRY_OPTIONS)
        elif key == "source":
            o = pick_option(opts, SOURCE_OPTIONS.get(source_kind(job), []))
        elif key == "state" and p.state:
            o = pick_option(opts, [rf"^{re.escape(fold(p.state))}$"])
        else:
            o = None
        return ("select", o) if o else None
    simple = {
        "first_name": p.first_name, "last_name": p.last_name, "full_name": p.full_name,
        "email": p.email, "email_confirm": p.email, "phone": p.phone, "zip": p.zip, "city": p.city,
        "state": p.state, "linkedin": p.linkedin, "birth_date": p.birth_date, "start_date": p.earliest_start,
        "available_until": p.available_until,
    }
    if key == "country":
        return ("fill", p.country)
    if key == "salutation":
        return ("fill", p.salutation) if p.salutation else None
    if key == "street":
        return ("fill", p.street_name if job.get("_has_house_no") else p.street)
    if key == "house_no":
        return ("fill", p.house_no) if p.house_no else None
    if key == "hours":
        if not p.hours_max:
            return None
        return ("fill", str(p.hours_max) if typ == "number" else p.availability or str(p.hours_max))
    if key == "message":
        return ("fill", letter_text) if letter_text else None
    if key in simple:
        v = simple[key]
        if key == "start_date" and fold(v) in ("sofort", "ab sofort", "asap", "immediately"):
            today = _date.today()
            if typ == "date":
                return ("fill", today.isoformat())
            hint = fold(" ".join([d.get("placeholder") or "", d.get("label") or ""]))
            if re.search(r"tt\.mm|dd\.mm|datum|date", hint):
                return ("fill", today.strftime("%d.%m.%Y"))
            return ("fill", "ab sofort")
        if key in ("birth_date", "start_date", "available_until") and v and typ == "date":
            m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", v)
            v = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else v
        return ("fill", v) if v else None
    return None


def plan_fields(descs: list[dict], p: Profile, job: dict, letter_text: str, docs: dict) -> Plan:
    """Строит план: какие поля заполнить, какие файлы куда загрузить и что оставить тебе."""
    plan = Plan()
    keyed = [(d, classify(d)) for d in descs]
    job = dict(job)
    job["_has_house_no"] = any(k == "house_no" and d.get("visible") for d, k in keyed)
    radio_groups: dict[str, list[dict]] = {}
    files: list[tuple[dict, str]] = []
    messages: list[tuple[dict, bool, str]] = []

    for d, key in keyed:
        visible = bool(d.get("visible"))
        tag, typ = d.get("tag"), (d.get("type") or "").lower()
        required = bool(d.get("required"))
        if key == "skip":
            continue
        if key == "file":
            files.append((d, classify_file(d)))
            continue
        if not visible and not (tag == "select" and d.get("in_form")) and typ != "checkbox":
            continue
        label = _label(d)
        if key == "radio":
            radio_groups.setdefault(d.get("name") or label, []).append(d)
            continue
        if typ == "checkbox":
            if d.get("checked"):
                continue
            if key == "privacy":
                if p.accept_privacy:
                    plan.actions.append(Action(d["idx"], key, "check", True, label))
                else:
                    plan.todo.append(f"поставь галочку согласия: «{label[:90]}»")
            elif key in ("talent_pool", "disability", "skip"):
                plan.skipped.append(f"не отмечаю: «{label[:60]}»")
            elif required and visible:
                plan.todo.append(f"обязательная галочка: «{label[:90]}» — реши сам")
            continue
        if (d.get("value") or "").strip() and tag != "select":
            continue  # уже заполнено (тобой или браузером) — не трогаю; списки выставляются всегда
        salary_siblings = []
        if tag == "select":
            salary_siblings = [s for s, k in keyed if k == "salary" and s.get("tag") != "select"
                               and s["idx"] in (d.get("siblings") or [])]
            if key == "unknown" and salary_siblings:
                key = "salary"
        if key in ASK_KEYS:
            res = (value_for(key, d, p, job, letter_text) if key == "salary"
                   and not any(ANNUAL_SALARY.search(field_hay(s)) for s in salary_siblings) else None)
            if res:
                plan.actions.append(Action(d["idx"], key, res[0], res[1], label))
            elif required:
                plan.todo.append(f"заполни сам: {ASK_KEYS[key]} — «{label}»")
            else:
                plan.skipped.append(f"не заполняю: «{label}»")
            continue
        if key == "message":
            messages.append((d, required, label))
            continue
        if key in AUTO_KEYS:
            res = value_for(key, d, p, job, letter_text)
            if res:
                plan.actions.append(Action(d["idx"], key, res[0], res[1], label))
            elif required:
                why = {"source": "где нашёл вакансию (подходящего варианта не нашёл)",
                       "start_date": "дата начала (в config.toml → [forms] earliest_start пусто)",
                       "available_until": "доступность до (в config.toml → [forms] available_until пусто)",
                       "birth_date": "дата рождения"}.get(key, "")
                plan.todo.append(f"заполни сам: {why or label} — «{label}»")
            continue
        if required:
            plan.todo.append(f"вопрос формы: «{label}»")

    for name, group in radio_groups.items():
        if any(g.get("checked") for g in group):
            continue
        if any(g.get("required") for g in group):
            plan.todo.append(f"выбери вариант: «{_label(group[0]).split(' — ')[0]}»")

    _plan_files(plan, files, docs)
    letter_uploaded = any(a.kind == "files" and docs.get("letter") in a.value for a in plan.actions)
    for d, required, label in messages[:1]:
        if letter_text and (required or not letter_uploaded):
            plan.actions.append(Action(d["idx"], "message", "fill", letter_text, label))
        elif required:
            plan.todo.append(f"заполни сам: «{label}»")
        else:
            plan.skipped.append(f"не дублирую письмо в поле «{label}» — Anschreiben уже загружен файлом")
    return plan


def _plan_files(plan: Plan, files: list[tuple[dict, str]], docs: dict) -> None:
    cv, letter, certs = docs.get("cv"), docs.get("letter"), list(docs.get("certificates") or [])
    usable = [(d, k) for d, k in files if k != "autofill"]
    by_kind: dict[str, list[dict]] = {}
    for d, k in usable:
        by_kind.setdefault(k, []).append(d)
    used_letter = False

    def put(d, paths, key):
        paths = [x for x in paths if x]
        if paths:
            plan.actions.append(Action(d["idx"], key, "files", paths, _label(d)))
            plan.files[str(d["idx"])] = paths

    if by_kind.get("cv"):
        d = by_kind["cv"][0]
        extra = []
        if d.get("multiple") and not by_kind.get("letter") and letter:
            extra, used_letter = [letter], True
        put(d, [cv, *extra], "cv")
    if by_kind.get("letter") and letter:
        put(by_kind["letter"][0], [letter], "letter")
        used_letter = True
    if by_kind.get("certificates") and certs:
        d = by_kind["certificates"][0]
        put(d, certs if d.get("multiple") else certs[:1], "certificates")
        certs = []
    generic = by_kind.get("other", [])
    if not by_kind.get("cv") and generic:
        d = generic[0]
        if d.get("multiple"):
            put(d, [cv, *( [letter] if letter and not used_letter else []), *certs], "documents")
            used_letter = used_letter or bool(letter)
        else:
            put(d, [cv], "cv")
            if len(generic) > 1 and letter and not used_letter:
                put(generic[1], [letter], "letter")
                used_letter = True
    if not usable:
        return
    if not any(a.kind == "files" and cv in a.value for a in plan.actions):
        plan.todo.append("загрузи резюме вручную: поле для файла не распознано")


# --------------------------------------------------------------------------- системы подачи и результат

ATS_PATTERNS: list[tuple[str, str]] = [
    ("concludis", r"/_/apply/|concludis"),
    ("personio", r"jobs\.personio\.(de|com)|tx_personio|personio"),
    ("greenhouse", r"greenhouse\.io"),
    ("lever", r"jobs\.lever\.co"),
    ("recruitee", r"recruitee\.com"),
    ("softgarden", r"softgarden\.(io|de)"),
    ("dvinci", r"dvinci"),
    ("join", r"join\.com"),
    ("smartrecruiters", r"smartrecruiters\.com"),
    ("workday", r"myworkdayjobs\.com|\.wd\d+\.|workday"),
    ("successfactors", r"successfactors|jobs\.sap\.com"),
    ("taleo", r"taleo\.net"),
    ("rexx", r"rexx-systems|rexx-recruitment"),
    ("umantis", r"umantis"),
    ("onlyfy", r"onlyfy|prescreen\.io"),
]
ACCOUNT_ATS = {"workday", "successfactors", "taleo"}


def detect_ats(url: str, html_hint: str = "") -> str:
    hay = (url or "").lower() + " " + (html_hint or "")[:200000].lower()
    for name, rx in ATS_PATTERNS:
        if re.search(rx, hay):
            return name
    return ""


SUCCESS = re.compile(
    r"vielen dank fuer (deine|ihre|dein|ihr) (bewerbung|interesse)|"
    r"(deine|ihre) bewerbung (wurde|ist) (erfolgreich |bei uns )?(uebermittelt|versendet|versandt|abgeschickt|eingegangen|gesendet|eingereicht|angekommen)|"
    r"bewerbung (wurde )?(erfolgreich )?(gesendet|uebermittelt|abgeschlossen|eingereicht|versendet)|"
    r"erfolgreich (beworben|uebermittelt|versendet|eingereicht)|"
    r"thank(s| you) for (your )?(application|applying)|"
    r"(your )?application (has been|was) (successfully )?(submitted|received|sent)|"
    r"we have received your application|application submitted"
)
SUCCESS_URL = re.compile(r"/success|danke|thank|confirmation|bestaetigung|erfolgreich|/sent\b|applied")
INVALID = re.compile(r"pflichtfeld|bitte (fuelle|fuellen|gib|geben|waehle|waehlen)|ist erforderlich|required|ungueltig|invalid|"
                     r"fehlt noch|nicht alle erforderlichen|darf nicht leer|must not be empty|please (fill|enter|select)")


def outcome_from(text: str, url: str, form_present: bool, error_texts: list[str]) -> str:
    """success | invalid | unknown — по тексту страницы, адресу и видимым ошибкам."""
    t = fold(text)
    if SUCCESS.search(t) or (SUCCESS_URL.search((url or "").lower()) and not form_present):
        return "success"
    if form_present and any(INVALID.search(fold(e)) for e in error_texts):
        return "invalid"
    return "unknown"
