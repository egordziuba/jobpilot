"""Анализ вакансии: Werkstudent ли это, совпадение по задачам, пробелы, канал подачи, контакт."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .models import Job
from .textutil import detect_language, fold, haversine_km, sentences

# ---------------------------------------------------------------- темы
THEMES: dict[str, dict[str, list[str]]] = {
    "automation": {
        "title": [r"automatis", r"automation", r"\brpa\b", r"low[- ]?code", r"no[- ]?code", r"workflow", r"prozessautomat"],
        "text": [r"automatisier", r"\bautomation", r"\brpa\b", r"low[- ]?code", r"no[- ]?code", r"\bn8n\b", r"power automate",
                 r"zapier", r"\bmake\.com", r"workflows?\b", r"schnittstellen", r"integration(en)? von systemen", r"\bapis?\b",
                 r"wiederkehrende (aufgaben|ablaufe|prozesse)", r"manuelle (aufgaben|prozesse|ablaufe)"],
    },
    "ai": {
        "title": [r"\bki\b", r"\bai\b", r"kunstliche intelligenz", r"genai", r"generative", r"\bllm", r"copilot", r"\bagent"],
        "text": [r"\bki[- ]", r"\bki\b", r"kunstliche intelligenz", r"\bgenai\b", r"generative (ai|ki)", r"\bllms?\b",
                 r"copilot", r"chatgpt", r"\bclaude\b", r"ki-agent", r"\bagents?\b", r"prompt", r"\bai[- ]", r"\bai\b"],
    },
    "enablement": {
        "title": [r"enablement", r"adoption", r"community", r"change", r"schulung", r"training", r"wissensmanagement"],
        "text": [r"enablement", r"adoption", r"schulung", r"trainings?\b", r"community", r"change[- ]management",
                 r"befahig", r"wissensmanagement", r"leitfaden|anleitungen", r"lernformate|lernmaterial"],
    },
    "process": {
        "title": [r"prozess", r"process", r"business analy", r"digitalisierung", r"transformation", r"organisation", r"operations"],
        "text": [r"prozessoptimierung", r"prozessanalyse", r"prozessmanagement", r"geschaftsprozess", r"business analy",
                 r"\bbpmn\b", r"anforderungs(management|analyse|aufnahme)", r"requirements?", r"prozesse (analys|optimier|dokumentier|aufnehm)",
                 r"digitalisierung", r"digitale transformation", r"process (optimi|improvement|analysis)"],
    },
    "data": {
        "title": [r"reporting", r"business intelligence", r"\bbi\b", r"data", r"daten", r"analytics", r"analyst"],
        "text": [r"reporting", r"dashboards?", r"business intelligence", r"datenanalyse", r"datenaufbereitung", r"datenqualitat",
                 r"\bkpis?\b", r"\bsql\b", r"\bexcel\b", r"auswertungen", r"data analy", r"analytics"],
    },
    "project": {
        "title": [r"projekt", r"project", r"\bpmo\b", r"koordination", r"innovation"],
        "text": [r"projektmanagement", r"projektkoordination", r"projektplanung", r"\bpmo\b", r"projektsteuerung",
                 r"project management", r"project coordination", r"arbeitspakete"],
    },
    "finance": {
        "title": [r"finance", r"finanz", r"versicherung", r"insurance", r"bank"],
        "text": [r"versicherung", r"insurance", r"finanzdienst", r"\bbank", r"finance", r"fonds|asset management"],
    },
}
CORE = {"automation", "ai", "process", "data", "enablement"}

REJECT_TITLE = [
    r"vertrieb", r"\bsales\b", r"akquise", r"kundenberatung", r"kundenservice", r"verkauf", r"\bkasse\b", r"empfang",
    r"recruiting", r"recruiter", r"steuer", r"buchhaltung", r"lohn", r"accounting", r"wirtschaftsprufung", r"\baudit",
    r"softwareentwickl", r"developer", r"entwickler", r"frontend", r"backend", r"full[- ]?stack", r"devops", r"embedded",
    r"elektro", r"mechanik", r"maschinenbau", r"\bbau\b", r"architektur", r"labor", r"pflege", r"lager", r"marketing",
    r"social media", r"grafik", r"design", r"redaktion", r"journalis", r"\blegal\b", r"recht", r"jurist", r"it-sicherheit",
    r"security", r"helpdesk", r"service desk", r"immobilien", r"einkauf", r"office support", r"assistenz",
    r"quality", r"qualitat", r"patient", r"customer service", r"\btax\b", r"aktuar", r"mathematik", r"arbeitsschutz",
    r"gesundheitsschutz", r"\behs\b", r"web ?development", r"webentwickl", r"wordpress", r"shopify", r"net-?sec",
    r"netzwerk", r"network", r"software development", r"system engineering", r"produktionsmanagement",
    r"logistic operational", r"metering", r"fahrzeug", r"pruflabor",
]
RULES_VERSION = 3
SOFT_NEG_TITLE = {r"controlling": 2, r"\bhr\b|personal": 2, r"procurement": 1, r"support": 1}

WERKSTUDENT = re.compile(r"werkstudent|working student|studentische (hilfskraft|aushilfe|mitarbeit)|student assistant|studentenjob|werkstudierend")

OK_PLACES = ["hamburg", "lubeck", "molln", "ratzeburg", "ahrensburg", "geesthacht", "norderstedt", "reinbek", "wedel",
             "pinneberg", "schwarzenbek", "bad oldesloe", "glinde", "barsbuttel", "bargteheide", "quickborn", "elmshorn"]
FULL_REMOTE = re.compile(r"100\s*%\s*remote|vollstandig remote|voll remote|fully remote|full remote|komplett remote|"
                         r"remote aus ganz deutschland|deutschlandweit remote|remote[- ]first|ortsunabhangig|"
                         r"remote \(deutschland\)|remote, deutschland|germany \(remote\)|remote within germany")

# ---------------------------------------------------------------- канал подачи
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"arbeitsagentur\.de|example\.|stepstone|indeed|linkedin|xing|personio|greenhouse|recruitee|softgarden|"
                       r"noreply|no-reply|donotreply|datenschutz|privacy|^dsb@|dpo@|presse@|press@|webmaster")
APPLY_NEAR = re.compile(r"bewirb|bewerb|unterlagen|lebenslauf|\bcv\b|application|apply|schick|sende|zusenden|richten sie|reach out")
APPLY_STRONG = re.compile(r"bewirb dich|bewerben sie sich|bewerbung (an|per|bitte|richten|senden|schicken|mit)|unterlagen (an|per|bitte)|"
                          r"per e-?mail|per mail|send (your|us)|apply (by|via) e-?mail|e-?mail an|jetzt unter|bewirb dich jetzt|"
                          r"application to|applications to|by e-?mail to|anytime, as long|by e-mail")
QUESTION_NEAR = re.compile(r"\bfragen\b|questions|ruckfragen|bei fragen|beantwortet|queries|weitere informationen")
PORTAL = re.compile(r"online-bewerbungstool|bewerbungstool|bewerbungsportal|bewerbungsformular|online bewerben|karriereportal|"
                    r"karriereseite|uber den button|uber unser portal|application form|apply (now|via|through) (our|the)|"
                    r"bevorzugt (uber|unser)|ausschlie(ss|ß)lich (uber|online)")
NO_EMAIL = re.compile(r"bewerbungen (per|via|uber|ueber) (e-?mail|mail)[^.]{0,40}(nicht|keine)|"
                      r"(keine|no) (bewerbungen|applications) (per|via|by) (e-?mail|mail)|"
                      r"(e-?mail|mail)[- ]?bewerbungen[^.]{0,30}(nicht|keine)|"
                      r"(do not|don't|cannot|can't) (accept|consider) (applications )?(by|via) (e-?mail|mail)|"
                      r"applications (by|via) (e-?mail|mail)[^.]{0,30}(not|cannot)")
PREFERRED_LOCAL = re.compile(r"^(bewerbung|bewerbungen|jobs?|karriere|career|careers|hr|personal|recruiting|application|apply|talent)")

CONTACT_RE = re.compile(r"\b(Frau|Herrn?|Mr\.?|Ms\.?|Mrs\.?)\s+((?:Dr\.\s+)?[A-ZÄÖÜ][\wäöüß\-]+(?:\s+(?:von der |von |van |de |zu )?[A-ZÄÖÜ][\wäöüß\-]+)?)")

DATE_RE = re.compile(r"(?:bewerbungsfrist|bewerbung bis|bewerbungen bis|bewirb dich bis|bewerbungsschluss|bis spatestens|deadline|apply by)\D{0,15}"
                     r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")


@dataclass
class Analysis:
    ok: bool
    reason: str = ""
    tier: str = "low"            # close | adjacent | low
    score: float = 0.0
    themes: list[str] = field(default_factory=list)
    reqs: list[dict] = field(default_factory=list)   # {"t": текст, "s": ok|gap|unk}
    gaps: list[str] = field(default_factory=list)
    channel: str = "portal"      # email | portal | external
    email: str = ""
    contact: dict = field(default_factory=dict)      # {"salutation": "Frau", "name": "Müller"}
    language: str = "de"
    deadline: str = ""
    location_note: str = ""


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text))


def score_themes(title: str, text: str) -> tuple[dict[str, float], float]:
    t, d = fold(title), fold(text)
    per: dict[str, float] = {}
    for name, kw in THEMES.items():
        th = min(_hits(kw["title"], t), 2)
        dh = min(_hits(kw["text"], d), 4)
        val = th * 3 + dh
        if name == "finance":
            val *= 0.5
        if val:
            per[name] = val
    total = sum(per.values())
    for pat, pen in SOFT_NEG_TITLE.items():
        if re.search(pat, t):
            total -= pen
    return per, total


def title_has_core(title: str) -> bool:
    t = fold(title)
    return any(_hits(THEMES[n]["title"], t) for n in CORE)


def rejected_title(title: str) -> str | None:
    t = fold(title)
    for pat in REJECT_TITLE:
        if re.search(pat, t):
            return pat.replace("\\b", "")
    return None


# ---------------------------------------------------------------- требования
SOFT = re.compile(r"von vorteil|wunschenswert|idealerweise|nice to have|is a plus|\bplus\b|hilfreich|optional|kein muss|"
                  r"nicht zwingend|bringen wir dir bei|zeigen wir dir|lernst du|lernbereitschaft|interesse an|first experience|erste")
HARD = re.compile(r"zwingend|voraussetzung|erforderlich|setzen .{0,40} voraus|\bmust\b|required|mehrjahrig|fundierte|sehr gute|"
                  r"fortgeschritten|expert|solide|strong|proficien|ausgepragte|umfangreiche|verfugst uber")
TOOLS = {r"power ?bi": "Power BI", r"\bvba\b": "VBA", r"power ?apps": "Power Apps", r"copilot studio": "Copilot Studio",
         r"power automate": "Power Automate", r"\bjava\b": "Java", r"c#|\.net\b": "C#/.NET", r"javascript|typescript": "JavaScript/TypeScript",
         r"\babap\b": "ABAP", r"tableau": "Tableau", r"databricks": "Databricks", r"\bazure\b": "Azure", r"\bsap\b": "SAP",
         r"\bn8n\b": "n8n", r"\brpa\b|uipath|automation anywhere": "RPA-Tools"}


def requirements(text: str) -> tuple[list[dict], list[str]]:
    reqs: list[dict] = []
    gaps: list[str] = []
    seen: set[str] = set()

    def add(t: str, s: str) -> None:
        if t in seen:
            return
        seen.add(t)
        reqs.append({"t": t, "s": s})
        if s == "gap":
            gaps.append(t)

    for raw in sentences(text):
        f = fold(raw)
        if len(f) > 400:
            f = f[:400]
        soft = bool(SOFT.search(f))
        hard = bool(HARD.search(f))
        # Немецкий: подтверждено B2 (TestDaF)
        if re.search(r"deutsch(?!land)|german(?!y)", f):
            if re.search(r"\bc2\b|muttersprach|native|verhandlungssicher|business fluent", f) or (re.search(r"\bc1\b", f) and not re.search(r"\bb2\b", f)):
                add("Немецкий выше B2 (" + raw[:90] + ")", "unk" if soft else "gap")
            elif re.search(r"flie(ss|ß)end|sehr gute|fluent|excellent|ausgezeichnete", f):
                add("Немецкий «fließend/sehr gut» — у тебя B2", "unk")
            elif re.search(r"\bb1\b|\bb2\b|gute deutsch|good german|deutschkenntnisse", f):
                add("Немецкий на уровне B2", "ok")
        # Инструменты, которые нельзя приписывать
        for pat, name in TOOLS.items():
            if re.search(pat, f):
                if hard and not soft:
                    add(f"Требуется {name}", "gap")
                else:
                    add(f"{name} — желательно", "unk")
        if re.search(r"python", f):
            if re.search(r"sehr gute|gute|fundierte|fortgeschrittene|solide|good|solid|strong|advanced|proficient|sicher", f) and not re.search(r"erste|grund|basic", f):
                add("Python на хорошем уровне", "gap" if not soft else "unk")
            else:
                add("Python — базовый уровень (свои проекты)", "ok")
        if re.search(r"\bsql\b", f):
            if re.search(r"sehr gute|fundierte|fortgeschrittene|strong|advanced|proficient", f) and not soft:
                add("SQL на продвинутом уровне", "gap")
            else:
                add("SQL — основы", "ok")
        if re.search(r"studium|studierst|studierende|eingeschrieben|enrolled|student", f) and re.search(
                r"informatik|computer science|data science|elektrotechnik|maschinenbau|ingenieur|mathematik|physik", f):
            if re.search(r"wirtschaft|bwl|betriebswirt|business|management|vergleichbar|ahnlich|related|comparable|z\. ?b\.|e\.g\.|o\. ?a\.|oder", f):
                add("Направление учёбы — «или сравнимое»", "unk")
            elif hard:
                add("Требуется техническое направление учёбы", "gap")
            else:
                add("Направление учёбы: техническое", "unk")
        if re.search(r"it[- ]background|it-hintergrund|technischen? hintergrund|technical background|informatischen hintergrund", f):
            add("Нужен IT-бэкграунд — у тебя свои проекты, не профильное образование", "unk")
        if re.search(r"englisch|english", f) and not re.search(r"deutsch(?!land)|german(?!y)", f):
            add("Английский (IELTS 7,0 / C1)", "ok")
    return reqs, gaps


def hours_problem(title: str, text: str) -> str | None:
    t, f = fold(title), fold(text)
    if re.search(r"vollzeit|full[- ]time", t):
        return "полная занятость"
    for lo, hi in re.findall(r"(\d{1,2})\s*(?:-|–|bis)\s*(\d{1,2})\s*(?:std|stunden|wochenstunden|h\b|hours)", f):
        if int(lo) > 20:
            return f"{lo}–{hi} ч/нед."
    m = re.search(r"(?:mindestens|min\.|mind\.|at least)\s*(\d{2})\s*(?:std|stunden|h\b|hours)", f)
    if m and int(m.group(1)) > 20:
        return f"минимум {m.group(1)} ч/нед."
    return None


def find_deadline(text: str) -> str:
    for d, m, y in DATE_RE.findall(fold(text)):
        y = int(y)
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m), int(d)).isoformat()
        except ValueError:
            continue
    return ""


def find_contact(text: str) -> dict:
    best = None
    for m in CONTACT_RE.finditer(text or ""):
        before = fold(text[max(0, m.start() - 160):m.start()])
        score = 2 if re.search(r"ansprechpartner|kontakt|fragen|bewerbung|recruit|contact", before) else 1
        if not best or score > best[0]:
            best = (score, m.group(1), m.group(2))
    if not best or best[0] < 2:
        return {}
    sal = best[1].rstrip(".")
    sal = {"Herrn": "Herr", "Mr": "Herr", "Ms": "Frau", "Mrs": "Frau"}.get(sal, sal)
    full = best[2]
    particle = re.search(r"((?:von der|von|van|de|zu) [A-ZÄÖÜ][\wäöüß\-]+)$", full)
    name = particle.group(1) if particle else full.split()[-1]
    if full.startswith("Dr.") and not name.startswith("Dr."):
        name = "Dr. " + name
    return {"salutation": sal, "name": name, "full_name": full}


def find_channel(job: Job, text: str) -> tuple[str, str]:
    if job.email_hint:
        return "email", job.email_hint
    folded = fold(text)
    candidates: list[tuple[int, str]] = []
    for m in EMAIL_RE.finditer(text or ""):
        addr = m.group(0).strip(".").lower()
        if BAD_EMAIL.search(addr):
            continue
        ctx_before = folded[max(0, m.start() - 260):m.start()]
        ctx_after = folded[m.end():m.end() + 60]
        ctx = ctx_before + " " + ctx_after
        if not APPLY_NEAR.search(ctx):
            continue
        strong = bool(APPLY_STRONG.search(ctx_before[-180:]))
        question = bool(QUESTION_NEAR.search(ctx_before[-120:]))
        if question and not strong:
            continue
        rank = (2 if strong else 1) + (1 if PREFERRED_LOCAL.search(addr) else 0)
        candidates.append((rank, addr))
    portal_text = bool(PORTAL.search(folded))
    if NO_EMAIL.search(folded):   # «Bewerbungen per E-Mail können leider nicht berücksichtigt werden»
        return ("external" if job.external_url else "portal"), ""
    if candidates:
        candidates.sort(key=lambda x: -x[0])
        rank, addr = candidates[0]
        if portal_text and rank < 2:
            return ("external" if job.external_url else "portal"), ""
        return "email", addr
    if job.external_url:
        return "external", ""
    return "portal", ""


def location_ok(job: Job, text: str, geo: dict) -> tuple[bool, str]:
    if job.source == "manual":
        return True, job.location or "добавлено вручную"
    home = tuple(geo.get("home", (53.5503, 9.9930)))
    hh = tuple(geo.get("hamburg", (53.5503, 9.9930)))
    remote = bool(FULL_REMOTE.search(fold(text + " " + job.location))) or bool(
        re.search(r"remote|home ?office|100\s*% mobil", fold(job.title)))
    if job.lat is not None and job.lon is not None:
        dh = haversine_km((job.lat, job.lon), hh)
        dm = haversine_km((job.lat, job.lon), home)
        if dh <= geo.get("max_km_from_hamburg", 35):
            return True, f"{job.location} · {dh:.0f} км от центра Гамбурга"
        if dm <= geo.get("max_km_from_home", 40):
            return True, f"{job.location} · {dm:.0f} км от дома"
        if remote:
            return True, f"удалённо ({job.location})"
        return False, f"{job.location}: {dm:.0f} км от дома"
    loc = fold(job.location)
    if any(p in loc for p in OK_PLACES):
        return True, job.location
    if remote or (job.homeoffice and "remote" in loc):
        return True, f"удалённо ({job.location or 'DE'})"
    return False, job.location or "место не указано"


def core_text_hits(text: str) -> int:
    d = fold(text)
    return sum(min(_hits(THEMES[n]["text"], d), 4) for n in CORE)


def analyze(job: Job, geo: dict, today: date | None = None, skip_location: bool = False) -> Analysis:
    today = today or date.today()
    text = job.description or ""
    full = f"{job.title}\n{text}"
    a = Analysis(ok=False, language=detect_language(full))

    if not WERKSTUDENT.search(fold(job.title + " " + text[:2500])):
        a.reason = "не Werkstudent"
        return a
    if re.search(r"pflichtprakti", fold(job.title)) and "werkstudent" not in fold(job.title):
        a.reason = "только Pflichtpraktikum"
        return a
    bad = rejected_title(job.title)
    if bad:
        a.reason = f"не тот профиль ({bad})"
        return a
    hp = hours_problem(job.title, text)
    if hp:
        a.reason = f"часы: {hp}"
        return a
    ok_loc, note = (True, job.location) if skip_location else location_ok(job, text, geo)
    a.location_note = note
    if not ok_loc:
        a.reason = f"далеко: {note}"
        return a
    a.deadline = job.deadline or find_deadline(text)
    if a.deadline and a.deadline < today.isoformat():
        a.reason = f"срок истёк {a.deadline}"
        return a

    per, total = score_themes(job.title, text)
    a.score = round(total, 1)
    ranked = sorted(((v, k) for k, v in per.items() if k != "finance"), reverse=True)
    a.themes = [k for _, k in ranked[:2]] or ["process"]
    if per.get("finance", 0) >= 2:
        a.themes.append("finance")
    if total >= 8 and title_has_core(job.title):
        a.tier = "close"
    elif total >= 6 and (title_has_core(job.title) or core_text_hits(text) >= 3):
        a.tier = "adjacent"
    else:
        a.tier = "low"
        a.reason = f"мало совпадений по задачам (балл {a.score})"
        return a

    a.reqs, a.gaps = requirements(text)
    a.channel, a.email = find_channel(job, text)
    a.contact = find_contact(text)
    a.ok = True
    return a
