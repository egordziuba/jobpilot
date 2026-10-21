"""Общие заглушки для тестов: сеть, SMTP, IMAP и временный проект."""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
from pathlib import Path

from jobpilot.config import load_settings

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"

HH = (53.55, 9.99)


def ba_item(ref, title, firma, lat=53.55, lon=10.0, ort="Hamburg", plz="20095", external=None, homeoffice=False):
    item = {
        "stellenangebotsart": "PRAKTIKUM_TRAINEE",
        "stellenangebotsTitel": title,
        "stellenlokationen": [{"adresse": {"plz": plz, "ort": ort, "land": "DEUTSCHLAND"}, "breite": lat, "laenge": lon}],
        "homeofficemoeglich": homeoffice,
        "datumErsteVeroeffentlichung": "2026-10-15",
        "firma": firma,
        "referenznummer": ref,
        "entfernung": 3,
    }
    if external:
        item["externeURL"] = external
    return item


# Синтетические объявления в формате реального API (форма полей проверена на живых данных 19.10.2026)
BA_JOBS = [
    (ba_item("10001-1-S", "Werkstudent KI & Prozessautomatisierung (m/w/d)", "Alpha Logistik GmbH"),
     "**Deine Aufgaben**\n- Wiederkehrende Aufgaben automatisieren und Workflows aufbauen\n- KI-Anwendungen im Team einführen und testen\n"
     "- Prozesse analysieren und dokumentieren\n\n**Dein Profil**\n- Laufendes Studium der Wirtschaftswissenschaften oder vergleichbar\n"
     "- Gute Deutschkenntnisse\n- Erste Erfahrung mit Python von Vorteil\n- 15–20 Stunden pro Woche\n\n"
     "**Haben wir dein Interesse geweckt?**\nDann bewirb dich per E-Mail mit Lebenslauf.\nDeine Ansprechpartnerin: Frau Anna Schulz\n"
     "[**bewerbung@alpha-logistik.de**](https://mailto:bewerbung@alpha-logistik.de)"),
    (ba_item("10001-2-S", "Werkstudent Digitalisierung (m/w/d)", "Beta Treuhand GmbH"),
     "Du unterstützt bei der Digitalisierung und Prozessoptimierung unserer Geschäftsprozesse und beim Reporting.\n"
     "Bitte nutzen Sie dazu bevorzugt unser Online-Bewerbungstool unter www.beta.de/karriere.\n"
     "Ihre Fragen beantwortet unsere Recruiterin (Mail: jobs@beta-treuhand.de) sehr gerne."),
    (ba_item("10001-3-S", "Werkstudent Vertrieb Innendienst (m/w/d)", "Gamma Handel AG"), "Kaltakquise und Vertrieb."),
    (ba_item("10001-4-S", "Werkstudent Business Intelligence & Reporting (m/w/d)", "Delta Versicherung AG"),
     "Aufgaben: Aufbau von Dashboards, Reporting und Datenanalyse, Automatisierung von Reports.\n"
     "Profil: Sehr gute Kenntnisse in Power BI sind zwingend erforderlich. Gute Deutschkenntnisse.\n"
     "Bewirb dich jetzt unter: karriere@delta-versicherung.de"),
    (ba_item("10001-5-S", "Werkstudent KI Automatisierung (m/w/d)", "Epsilon München GmbH", lat=48.14, lon=11.58, ort="München", plz="80331"),
     "Prozesse automatisieren mit KI. Bewerbung per E-Mail an jobs@epsilon.de"),
    (ba_item("10001-6-S", "Werkstudent Prozessautomatisierung & KI (m/w/d)", "Zeta Remote GmbH", lat=52.52, lon=13.40, ort="Berlin", plz="10115", homeoffice=True),
     "100 % remote aus ganz Deutschland. Du automatisierst Workflows mit n8n und KI-Tools, analysierst Prozesse und dokumentierst Abläufe.\n"
     "Bewirb dich per Mail an hr@zeta-remote.de"),
    (ba_item("10001-7-S", "Werkstudent Prozessmanagement & Digitalisierung (m/w/d)", "Eta Industrie GmbH"),
     "Prozessanalyse, Prozessoptimierung, BPMN, Digitalisierung. Umfang 30–40 Stunden pro Woche. Bewerbung an jobs@eta.de"),
    (ba_item("10001-8-S", "Werkstudent Automatisierung & KI-Workflows (m/w/d)", "Theta Digital GmbH", external="https://jobs.example-board.de/123"),
     "Workflows automatisieren, KI-Agenten bauen, Prozesse analysieren."),
    (ba_item("10001-9-S", "Werkstudent KI & Prozessautomatisierung (w/m/d)", "Alpha Logistik GmbH"),
     "Dublette derselben Stelle über eine andere Referenznummer. Bewerbung per E-Mail an bewerbung@alpha-logistik.de"),
    (ba_item("10001-10-S", "Werkstudent AI Enablement & Automation (m/w/d)", "Iota Services GmbH"),
     "Aufgaben: KI-Tools einführen, Schulungen und Community-Formate, Workflows automatisieren, Prozesse analysieren.\n"
     "Profil: Deutsch auf C1-Niveau. In ganz Deutschland bekannt.\nBewirb dich per E-Mail an jobs@iota-services.de"),
]


class FakeFetcher:
    def __init__(self, ba_jobs=None, personio_xml: str | None = None, gone: set[str] | None = None):
        self.ba_jobs = BA_JOBS if ba_jobs is None else ba_jobs
        self.personio_xml = personio_xml
        self.gone = gone or set()
        self.calls: list[str] = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        if "/pc/v6/jobs" in url:
            return {"ergebnisliste": [item for item, _ in self.ba_jobs], "maxErgebnisse": len(self.ba_jobs)}
        if "/jobdetails/" in url:
            ref = base64.b64decode(url.rsplit("/", 1)[1]).decode()
            if ref in self.gone:
                return None
            for item, desc in self.ba_jobs:
                if item["referenznummer"] == ref:
                    return {"stellenangebotsTitel": item["stellenangebotsTitel"], "firma": item["firma"],
                            "stellenangebotsBeschreibung": desc, "referenznummer": ref}
            return None
        return None

    def get_text(self, url, params=None, headers=None):
        self.calls.append(url)
        if url.endswith("/xml"):
            return self.personio_xml
        return None

    def post_json(self, url, payload, headers=None):
        raise RuntimeError("LLM в тестах не используется")


class FakeSMTP:
    """mode: ok | auth | refuse | disconnect | down"""
    instances: list["FakeSMTP"] = []

    def __init__(self, mode="ok"):
        self.mode = mode
        self.sent: list = []

    def factory(self, host, port):
        if self.mode == "down":
            raise ConnectionRefusedError("down")
        FakeSMTP.instances.append(self)
        return self

    def login(self, user, pw):
        import smtplib
        if self.mode == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        import smtplib
        if self.mode == "refuse":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        if self.mode == "disconnect":
            raise smtplib.SMTPServerDisconnected("connection lost during DATA")
        self.sent.append(msg)
        return {}

    def quit(self):
        pass


class FakeIMAP:
    def __init__(self, inbox: list[dict], text_first: bool = False):
        self.inbox = inbox     # [{"from": "...", "subject": "...", "text": "..."}]
        self.box = None
        self.text_first = text_first   # как Gmail: части ответа FETCH могут прийти в другом порядке

    def __call__(self, host):
        return self

    def login(self, u, p):
        pass

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"']

    def select(self, box, readonly=True):
        self.box = box
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        crit = [c.lower() if isinstance(c, str) else c for c in criteria]
        if self.box != "INBOX":
            return "OK", [b""]
        if "from" in crit:
            who = crit[crit.index("from") + 1]
            ids = [str(i + 1).encode() for i, m in enumerate(self.inbox) if who in m["from"].lower()]
            if "body" in crit:
                body = crit[crit.index("body") + 1]
                ids = [str(i + 1).encode() for i, m in enumerate(self.inbox) if who in m["from"].lower() and body in m["text"].lower()]
            return "OK", [b" ".join(ids)]
        if "text" in crit:
            word = crit[crit.index("text") + 1]
            ids = [str(i + 1).encode() for i, m in enumerate(self.inbox)
                   if word in (m["from"] + " " + m["subject"] + " " + m["text"]).lower()]
            return "OK", [b" ".join(ids)]
        return "OK", [b""]

    def fetch(self, mid, what):
        m = self.inbox[int(mid) - 1]
        head = f"Subject: {m['subject']}\r\nFrom: {m['from']}\r\n\r\n".encode()
        parts = [(b"1 (BODY[HEADER.FIELDS (SUBJECT FROM DATE)] {%d}" % len(head), head), (b" BODY[TEXT]<0> {%d}" % len(m["text"].encode()), m["text"].encode())]
        if self.text_first:
            parts.reverse()
        return "OK", parts + [b")"]

    def logout(self):
        pass


def make_project(send_mode="dry_run", smtp=True, with_personio=False, extra_run=None) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix="jobpilot-test-"))
    shutil.copy(ROOT / "config.example.toml", tmp / "config.toml")
    (tmp / "profile").mkdir()
    shutil.copy(ROOT / "profile" / "facts.example.toml", tmp / "profile" / "facts.toml")
    (tmp / "documents").mkdir()
    (tmp / "documents" / "Lebenslauf.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp / "data" / "manual").mkdir(parents=True)
    (tmp / "data" / "already_applied.csv").write_text("company,title,email,date,channel,note\nBeispiel Agentur,,,2026-10,,\n", encoding="utf-8")
    if smtp:
        (tmp / ".env").write_text("SMTP_USER=test@gmail.com\nSMTP_PASSWORD=x\n", encoding="utf-8")
    settings = load_settings(tmp)
    settings.cfg["run"]["send_mode"] = send_mode
    settings.cfg["run"]["seconds_between_emails"] = [0, 0]
    settings.cfg["run"].update(extra_run or {})
    settings.cfg["search"]["ba"] = [{"where": "Hamburg", "radius_km": 35, "angebotsart": [34], "queries": ["Werkstudent KI"]}]
    settings.cfg["search"]["personio"] = [{"slug": "acme", "company": "ACME GmbH"}] if with_personio else []
    # тесты не зависят от личных настроек: осторожные значения по умолчанию
    settings.cfg.setdefault("forms", {}).update({"accept_privacy": False, "earliest_start": "", "auto_submit": False})
    return tmp, settings


PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>555</id>
    <office>Hamburg</office>
    <name>Werkstudent Prozessautomatisierung (Low-Code) m/w/d</name>
    <schedule>part-time</schedule>
    <createdAt>2026-09-01T10:00:00+00:00</createdAt>
    <jobDescriptions>
      <jobDescription><name>Deine Aufgaben</name><value><![CDATA[<ul><li>Automatisierung von Geschäftsprozessen mit Workflows</li><li>Prozesse analysieren und dokumentieren</li></ul>]]></value></jobDescription>
      <jobDescription><name>Profil</name><value><![CDATA[<p>Erste Erfahrung mit n8n von Vorteil. Werkstudent mit Interesse an Prozessoptimierung.</p>]]></value></jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>"""
