import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import FakeIMAP, make_project  # noqa: E402

from jobpilot import mailer  # noqa: E402
from jobpilot.forms import documents, runner  # noqa: E402
from jobpilot.forms.fieldmap import (build_profile, classify, classify_file, detect_ats, outcome_from,  # noqa: E402
                                     pick_option, plan_fields, SOURCE_OPTIONS)
from jobpilot.models import Job  # noqa: E402
from jobpilot.sources import ba  # noqa: E402
from jobpilot.store import Store  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "forms"


def D(idx, tag, typ, id_="", name="", label="", placeholder="", autocomplete="", required=False, visible=True,
      multiple=False, options=None, contexts=None, in_form=True, siblings=None):
    return {"idx": idx, "tag": tag, "type": typ, "id": id_, "name": name, "label": label, "placeholder": placeholder,
            "autocomplete": autocomplete, "required": required, "visible": visible, "multiple": multiple,
            "options": options or [], "contexts": contexts or [], "context": (contexts or [""])[0] if contexts else "",
            "value": "", "checked": False, "in_form": in_form, "siblings": siblings or []}


# Поля реальной формы concludis (обезличено)
CONCLUDIS = [
    D(4, "select", "select-one", "ipt-sex", "sex", "ANREDE", autocomplete="sex", visible=False, options=["Herr", "Frau", "Neutrale Anrede"]),
    D(5, "input", "text", "ipt-title", "title", "AKADEMISCHER TITEL", "Akademischer Titel", "honorific-prefix"),
    D(6, "input", "text", "ipt-firstname", "firstname", "VORNAME *", "Vorname", "given-name", True),
    D(7, "input", "text", "ipt-lastname", "lastname", "NACHNAME *", "Nachname", "family-name", True),
    D(8, "input", "text", "ipt-street", "street", "STRASSE, HAUSNR. *", "Straße, Hausnr.", "street-address", True),
    D(9, "input", "text", "ipt-zip", "zip", "PLZ *", "PLZ", "postal-code", True),
    D(10, "input", "text", "ipt-city", "city", "ORT *", "Ort", "address-level2", True),
    D(11, "select", "select-one", "ipt-country", "country", "LAND *", required=True, visible=False, options=["Deutschland", "Österreich", "Schweiz"]),
    D(12, "input", "search", autocomplete="off", visible=False),
    D(13, "input", "text", "country-autofill-helper", "", "Land", autocomplete="country-name"),
    D(14, "input", "text", "ipt-email", "email", "E-MAIL *", "E-Mail", "email", True),
    D(15, "input", "tel", "ipt-phone-private", "phone_private", "TELEFON, PRIVAT", "Telefon privat", "home tel"),
    D(16, "input", "checkbox", "ipt-disability-flag", "disability_flag", "Ja, ich möchte eine Schwerbehinderung oder Gleichstellung angeben."),
    D(17, "textarea", "textarea", "ipt-disablility-note", "disability_note", "FREIWILLIGE ANGABE ZUR SCHWERBEHINDERUNG ODER GLEICHSTELLUNG", visible=False),
    D(18, "select", "select-one", "ipt-source", "source", "ICH WURDE AUF DIE STELLE AUFMERKSAM ÜBER *", required=True, visible=False,
      options=["Indeed", "StepStone DE", "XING.de", "LinkedIn", "Unternehmenshomepage", "Sonstige", "Bundesagentur für Arbeit"]),
    D(21, "input", "file", multiple=True, visible=False, contexts=["", "Ziehe deine Datei einfach via Drag & Drop in diesen Bereich oder … wähle hier eine Datei aus"]),
    D(22, "input", "file", visible=False, contexts=["Automatisches Ausfüllen mit deinem Lebenslauf Spare Zeit. Lade deinen Lebenslauf hoch und wir füllen das Bewerbungsformular mit deinen Angaben automatisch aus."]),
]

# Поля формы ADS (TYPO3 + Personio, ads-steuer.de), считанные 19.10.2026
ADS = [
    D(0, "input", "checkbox", "CybotCookiebotDialogBodyLevelButtonNecessary", "", "Notwendig", visible=False),
    D(29, "input", "text", "", "tx_indexedsearch_pi2[search][sword]", placeholder="Suchbegriff eingeben...", required=True, visible=False),
    D(38, "select", "select-one", "gender", "tx_personio_show[gender]", "Anrede *", required=True, options=["Anrede *", "Herr", "Frau", "divers"]),
    D(39, "input", "text", "firstName", "tx_personio_show[firstName]", "Vorname *", "Vorname *", required=True),
    D(40, "input", "text", "lastName", "tx_personio_show[lastName]", "Nachname *", "Nachname *", required=True),
    D(41, "input", "text", "birthday", "tx_personio_show[birthday]", "Geburtsdatum", "Geburtsdatum"),
    D(42, "input", "email", "email", "tx_personio_show[email]", "E-Mail *", "E-Mail *", required=True),
    D(43, "input", "text", "phone", "tx_personio_show[phone]", "Telefon *", "Telefon *", required=True),
    D(44, "select", "select-one", "findus", "tx_personio_show[findus]", "Wie bist Du auf uns aufmerksam geworden? *", required=True,
      options=["Wie bist Du auf uns aufmerksam geworden? *", "ADS-Karriereseite", "Agentur für Arbeit", "Google", "Indeed", "LinkedIn", "Online-Stellenbörse"]),
    D(45, "input", "file", "", "tx_personio_show[coverLetterDocuments]", "Anschreiben (max. 20 MB)"),
    D(46, "input", "file", "", "tx_personio_show[cvDocuments]", "Lebenslauf (max. 20 MB) *", required=True),
    D(47, "input", "file", "", "tx_personio_show[erDocuments]", "Arbeitszeugnisse (max. 20 MB)"),
    D(48, "input", "file", "", "tx_personio_show[otherDocuments]", "Sonstige Unterlagen (max. 20 MB)"),
    D(50, "input", "checkbox", "dataProtection", "tx_personio_show[dataProtection]",
      "Ich willige in die Speicherung meiner personenbezogenen Daten im Rahmen der Datenschutzerklärung ein.*", required=True),
]

DOCS = {"cv": "/x/Lebenslauf_Max_Mustermann.pdf", "letter": "/x/Anschreiben_Max_Mustermann.pdf", "certificates": ["/x/Zeugnis_Praktikum.pdf"]}


class FieldMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.settings = make_project()
        self.profile = build_profile(self.settings)

    def test_profile_from_config(self):
        p = self.profile
        self.assertEqual((p.first_name, p.last_name), ("Max", "Mustermann"))
        self.assertEqual((p.street, p.zip, p.city), ("Musterstraße 12", "20095", "Hamburg"))
        self.assertEqual((p.street_name, p.house_no), ("Musterstraße", "12"))
        self.assertFalse(p.accept_privacy)
        self.assertEqual(p.birth_date, "")

    def test_classify_concludis(self):
        got = {d["idx"]: classify(d) for d in CONCLUDIS}
        self.assertEqual(got[4], "salutation")
        self.assertEqual(got[5], "academic_title")
        self.assertEqual([got[i] for i in (6, 7, 8, 9, 10, 11)], ["first_name", "last_name", "street", "zip", "city", "country"])
        self.assertEqual(got[12], "skip")
        self.assertEqual(got[13], "skip")  # вспомогательное поле страны
        self.assertEqual((got[14], got[15], got[16], got[17], got[18]), ("email", "phone", "disability", "disability", "source"))
        self.assertEqual(classify_file(CONCLUDIS[-2]), "other")
        self.assertEqual(classify_file(CONCLUDIS[-1]), "autofill")

    def test_plan_concludis(self):
        plan = plan_fields(CONCLUDIS, self.profile, {"source": "ba"}, "Text", DOCS)
        acts = {a.idx: a for a in plan.actions}
        self.assertEqual(acts[4].value, "Herr")
        self.assertEqual(acts[11].value, "Deutschland")
        self.assertEqual(acts[18].value, "Bundesagentur für Arbeit")
        self.assertEqual(acts[8].value, "Musterstraße 12")
        self.assertEqual(acts[21].value, [DOCS["cv"], DOCS["letter"], *DOCS["certificates"]])
        self.assertNotIn(22, acts)       # поле «Автоматически заполнить по резюме» не трогаю
        self.assertNotIn(16, acts)       # Schwerbehinderung не отмечаю
        self.assertNotIn(5, acts)
        self.assertEqual(plan.todo, [])

    def test_plan_ads(self):
        plan = plan_fields(ADS, self.profile, {"source": "ba"}, "Text", DOCS)
        acts = {a.idx: a for a in plan.actions}
        self.assertEqual(acts[38].value, "Herr")
        self.assertEqual(acts[44].value, "Agentur für Arbeit")
        self.assertEqual(acts[42].value, "max.mustermann@example.com")
        self.assertEqual(acts[43].value, "+49 151 23456789")
        self.assertEqual(acts[45].value, [DOCS["letter"]])
        self.assertEqual(acts[46].value, [DOCS["cv"]])
        self.assertEqual(acts[47].value, DOCS["certificates"][:1])
        self.assertNotIn(48, acts)
        self.assertNotIn(41, acts)       # дату рождения не вписываю
        self.assertNotIn(29, acts)       # поиск по сайту
        self.assertNotIn(50, acts)       # согласие — только ты
        self.assertTrue(any("галочку согласия" in t for t in plan.todo))

    def test_privacy_only_when_enabled(self):
        self.settings.cfg.setdefault("forms", {})["accept_privacy"] = True
        plan = plan_fields(ADS, build_profile(self.settings), {"source": "ba"}, "Text", DOCS)
        self.assertIn(50, {a.idx for a in plan.actions if a.kind == "check"})

    def test_salary_and_permit_left_to_user(self):
        descs = [D(1, "input", "text", name="gehalt", label="Gehaltsvorstellung *", required=True),
                 D(2, "select", "select-one", name="permit", label="Arbeitserlaubnis vorhanden? *", required=True, options=["", "ja", "nein"]),
                 D(3, "input", "text", name="start", label="Frühestmöglicher Eintritt *", required=True),
                 D(4, "input", "text", name="x", label="Was reizt Sie an der Stelle? *", required=True)]
        plan = plan_fields(descs, self.profile, {"source": "ba"}, "Text", DOCS)
        self.assertEqual(plan.actions, [])
        self.assertEqual(len(plan.todo), 4)

    def test_start_ab_sofort(self):
        self.settings.cfg.setdefault("forms", {})["earliest_start"] = "ab sofort"
        prof = build_profile(self.settings)
        descs = [D(1, "input", "text", name="start", label="Frühestmöglicher Eintritt *", required=True),
                 D(2, "input", "date", name="d", label="Eintrittsdatum *", required=True),
                 D(3, "input", "text", name="t", label="Verfügbar ab *", placeholder="TT.MM.JJJJ", required=True)]
        plan = plan_fields(descs, prof, {"source": "ba"}, "Text", DOCS)
        vals = {a.idx: a.value for a in plan.actions}
        today = datetime.now().date()
        self.assertEqual(vals, {1: "ab sofort", 2: today.isoformat(), 3: today.strftime("%d.%m.%Y")})
        self.assertEqual(plan.todo, [])

    def test_available_until(self):
        self.settings.cfg.setdefault("forms", {})["available_until"] = "30.09.2027"
        prof = build_profile(self.settings)
        descs = [D(1, "input", "text", label="Bis wann sind Sie (mindestens) verfügbar?", required=True),
                 D(2, "input", "date", name="start", label="Verfügbar bis", required=True),
                 D(3, "input", "text", label="Available until", required=True)]
        self.assertEqual([classify(d) for d in descs], ["available_until"] * 3)
        plan = plan_fields(descs, prof, {}, "", {})
        self.assertEqual({a.idx: a.value for a in plan.actions},
                         {1: "30.09.2027", 2: "2027-09-30", 3: "30.09.2027"})
        self.assertEqual(plan.todo, [])

    def test_available_until_empty_config(self):
        for forms in ({}, {"available_until": ""}):
            with self.subTest(forms=forms):
                self.settings.cfg["forms"] = forms
                prof = build_profile(self.settings)
                self.assertEqual(prof.available_until, "")
                descs = [D(1, "input", "date", label="Available until", required=True)]
                plan = plan_fields(descs, prof, {}, "", {})
                self.assertEqual(plan.actions, [])
                self.assertEqual(len(plan.todo), 1)
                self.assertIn("available_until", plan.todo[0])

    def test_salary_hourly(self):
        for amount, number in ((20, "20"), (20.5, "20.5")):
            with self.subTest(amount=amount):
                self.settings.cfg.setdefault("forms", {})["salary_hourly"] = amount
                prof = build_profile(self.settings)
                descs = [D(1, "input", "number", label="Gehaltsvorstellung", required=True),
                         D(2, "input", "text", label="Gehaltswunsch", required=True),
                         D(3, "input", "number", label="Vorstellung Bruttostundenlohn", required=True),
                         D(4, "input", "text", label="Salary expectation", required=True)]
                plan = plan_fields(descs, prof, {}, "", {})
                self.assertEqual({a.idx: a.value for a in plan.actions},
                                 {1: number, 2: f"{number} € brutto pro Stunde",
                                  3: number, 4: f"{number} € brutto pro Stunde"})
                self.assertEqual(plan.todo, [])

    def test_salary_hourly_empty_or_nonpositive_config(self):
        for forms in ({}, {"salary_hourly": 0}, {"salary_hourly": -10}):
            with self.subTest(forms=forms):
                self.settings.cfg["forms"] = forms
                descs = [D(1, "input", "number", label="Gehaltswunsch", required=True),
                         D(2, "select", "select-one", label="Zeitraum", required=True,
                           options=["pro Jahr", "pro Stunde"], siblings=[1])]
                plan = plan_fields(descs, build_profile(self.settings), {}, "", {})
                self.assertEqual(plan.actions, [])
                self.assertEqual(len(plan.todo), 2)

    def test_salary_hourly_with_weekly_hours(self):
        self.settings.cfg.setdefault("forms", {})["salary_hourly"] = 20.5
        for section in ("application", "applicant"):
            for minimum, maximum, expected in ((15, 20, "15–20"), (20, 20, "20"),
                                               (15, None, "15"), (None, 20, "20")):
                with self.subTest(section=section, minimum=minimum, maximum=maximum):
                    for name in ("application", "applicant"):
                        values = self.settings.cfg.setdefault(name, {})
                        values.pop("hours_min", None)
                        values.pop("hours_max", None)
                    configured = self.settings.cfg[section]
                    if minimum is not None:
                        configured["hours_min"] = minimum
                    if maximum is not None:
                        configured["hours_max"] = maximum
                    prof = build_profile(self.settings)
                    labels = ["Bitte nenne uns Deine Gehaltsvorstellung (Stundenlohn) unter Angabe der wöchentlichen Stundenzahl.",
                              "Gehaltswunsch und Wochenstunden", "Gehaltsvorstellung und Stunden pro Woche",
                              "Salary expectation and hours per week"]
                    descs = [D(i, "input", "text", label=label, required=True) for i, label in enumerate(labels)]
                    descs += [D(4, "input", "number", label=labels[0], required=True),
                              D(5, "input", "text", label="Gehaltsvorstellung", name="hours_per_week", required=True)]
                    plan = plan_fields(descs, prof, {}, "", {})
                    text = f"20.5 € brutto pro Stunde bei {expected} Stunden pro Woche"
                    self.assertEqual({a.idx: a.value for a in plan.actions},
                                     {0: text, 1: text, 2: text, 3: text, 4: "20.5", 5: "20.5 € brutto pro Stunde"})
                    self.assertEqual(plan.todo, [])

    def test_salary_hourly_weekly_hours_not_configured(self):
        self.settings.cfg.setdefault("forms", {})["salary_hourly"] = 20
        for configured in ({}, {"hours_min": 0, "hours_max": 0}):
            with self.subTest(configured=configured):
                for section in ("application", "applicant"):
                    values = self.settings.cfg.setdefault(section, {})
                    values.pop("hours_min", None)
                    values.pop("hours_max", None)
                    values.update(configured)
                descs = [D(1, "input", "text", label="Gehaltsvorstellung und Wochenstunden", required=True)]
                plan = plan_fields(descs, build_profile(self.settings), {}, "", {})
                self.assertEqual({a.idx: a.value for a in plan.actions}, {1: "20 € brutto pro Stunde"})
                self.assertEqual(plan.todo, [])

    def test_salary_hourly_period_select(self):
        self.profile.salary_hourly = 20
        for hourly in ("pro Stunde", "per hour"):
            with self.subTest(hourly=hourly):
                descs = [D(1, "input", "number", label="Gehaltsvorstellung", required=True),
                         D(2, "select", "select-one", label="Zeitraum", required=True,
                           options=["Bitte wählen", "pro Jahr", hourly], siblings=[1]),
                         D(3, "select", "select-one", label="Anderer Zeitraum",
                           options=["pro Jahr", hourly])]
                plan = plan_fields(descs, self.profile, {}, "", {})
                self.assertEqual({a.idx: (a.kind, a.value) for a in plan.actions},
                                 {1: ("fill", "20"), 2: ("select", hourly)})
                self.assertEqual(plan.todo, [])

    def test_salary_annual_left_to_user(self):
        self.profile.salary_hourly = 20
        for annual in ("p.a.", "pro Jahr", "jährlich", "annual"):
            for typ in ("number", "text"):
                with self.subTest(annual=annual, typ=typ):
                    descs = [D(1, "input", typ, label=f"Gehaltsvorstellung ({annual})", required=True),
                             D(2, "select", "select-one", label="Zeitraum", required=True,
                               options=["pro Jahr", "pro Stunde"], siblings=[1])]
                    plan = plan_fields(descs, self.profile, {}, "", {})
                    self.assertEqual(plan.actions, [])
                    self.assertEqual(len(plan.todo), 2)

    def test_configured_fields_preserve_existing_values(self):
        self.profile.available_until = "30.09.2027"
        self.profile.salary_hourly = 20
        descs = [D(1, "input", "text", label="Verfügbar bis", required=True),
                 D(2, "input", "number", label="Gehaltswunsch", required=True)]
        descs[0]["value"] = "31.12.2027"
        descs[1]["value"] = "25"
        plan = plan_fields(descs, self.profile, {}, "", {})
        self.assertEqual(plan.actions, [])
        self.assertEqual(plan.todo, [])

    def test_source_options(self):
        self.assertEqual(pick_option(["Bitte wählen", "Website", "LinkedIn"], SOURCE_OPTIONS["linkedin"]), "LinkedIn")
        self.assertIsNone(pick_option(["Bitte wählen", "Website", "Messe"], SOURCE_OPTIONS["ba"]))
        self.assertEqual(pick_option(["Bitte wählen", "Jobbörse", "Messe"], SOURCE_OPTIONS["ba"]), "Jobbörse")

    def test_detect_ats(self):
        self.assertEqual(detect_ats("https://jobs.hafen-karriere.de/_/apply/form?prj=1"), "concludis")
        self.assertEqual(detect_ats("https://www.ads-steuer.de/x", '<input name="tx_personio_show[firstName]">'), "personio")
        self.assertEqual(detect_ats("https://example.wd3.myworkdayjobs.com/x"), "workday")

    def test_outcome(self):
        ok = "Vielen Dank für deine Bewerbung auf die Stelle Werkstudent*in Data Management"
        self.assertEqual(outcome_from(ok, "https://jobs.hafen-karriere.de/_/apply/success?prj=1", False, []), "success")
        self.assertEqual(outcome_from("Formular", "https://x.de/apply", True,
                                      ["Es wurden nicht alle erforderlichen Dokumente hochgeladen"]), "invalid")
        self.assertEqual(outcome_from("Formular", "https://x.de/apply", True, []), "unknown")
        self.assertEqual(outcome_from("Your application has been submitted.", "https://x", False, []), "success")


class DocumentsTests(unittest.TestCase):
    def test_prepare_letter_and_cv(self):
        tmp, settings = make_project()
        (tmp / "documents" / "zeugnisse").mkdir()
        (tmp / "documents" / "zeugnisse" / "Zeugnis.pdf").write_bytes(b"%PDF-1.4 z")
        store = Store(settings.db_path)
        job = Job(id="ba:10001-1", source="ba", company="Muster Steuerberatung GmbH", title="Werkstudent RPA", source_url="https://x",
                  raw={"extra": {"address": {"street": "Beispielweg 5", "zip": "22297", "city": "Hamburg"}}})
        store.insert(job, None, "prepared", "")
        store.update(job.id, None, subject="Bewerbung als Werkstudent RPA (Ref. 10001-1)",
                     body="Sehr geehrte Frau Beispiel,\n\nText.\n\nMit freundlichen Grüßen\nMax Mustermann\nMusterstraße 12, 20095 Hamburg\n+49",
                     contact=json.dumps({"salutation": "Frau", "name": "Beispiel", "full_name": "Carolin Beispiel"}))
        row = store.get(job.id)
        self.assertEqual(documents.recipient_lines(row),
                         ["Muster Steuerberatung GmbH", "Personalabteilung", "Frau Carolin Beispiel", "Beispielweg 5", "22297 Hamburg"])
        docs = documents.prepare(settings, row)
        self.assertTrue(Path(docs["letter"]).read_bytes().startswith(b"%PDF"))
        self.assertTrue(docs["letter"].endswith("Anschreiben_Max_Mustermann.pdf"))
        self.assertTrue(docs["cv"].endswith("Lebenslauf_Max_Mustermann.pdf"))
        self.assertEqual(len(docs["certificates"]), 1)
        self.assertEqual(documents.strip_ref(row["subject"]), "Bewerbung als Werkstudent RPA")
        self.assertEqual(documents.letter_text_plain(row["body"]).splitlines()[-1], "Max Mustermann")
        store.close()

    def test_best_url_prefers_specific_job_link(self):
        row = {"apply_url": "", "external_url": "", "source_url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/1",
               "description": "Mehr: https://www.ads-steuer.de/ und https://www.ads-steuer.de/karriere/stellenboerse und "
                              "https://www.ads-steuer.de/karriere/stellenboerse/werkstudent-rpa-m-w-d-2799466/#"}
        self.assertEqual(runner.best_url(row), "https://www.ads-steuer.de/karriere/stellenboerse/werkstudent-rpa-m-w-d-2799466/")
        row["description"] = "keine Links"
        self.assertEqual(runner.best_url(row), row["source_url"])

    def test_ba_detail_extra(self):
        data = {"stellenangebotsBeschreibung": "x", "arbeitgeberAdresse": {"strasse": "Breite Straße 61", "plz": "22767", "ort": "Hamburg"},
                "allianzpartnerUrl": "https://jobs.hafen-karriere.de/stelle?prj=1875P7278", "logoUrl": "https://cdn.x.de/logo.png",
                "selbst": "https://www.arbeitsagentur.de/x"}
        extra = ba.detail_extra(data)
        self.assertEqual(extra["address"]["zip"], "22767")
        self.assertNotIn("selbst", extra["urls"])
        self.assertEqual(ba.pick_apply_url(extra["urls"]), "https://jobs.hafen-karriere.de/stelle?prj=1875P7278")


class PortalInboxTests(unittest.TestCase):
    def _store(self):
        tmp, settings = make_project()
        store = Store(settings.db_path)
        job = Job(id="ba:15023-1", source="ba", company="Hafen Consulting GmbH", title="Werkstudent Data", source_url="https://x")
        store.insert(job, None, "prepared", "")
        store.update(job.id, "submitted", "форма", channel="portal", sent_at="2026-10-19T21:40:00",
                     extra=json.dumps({"form": {"url": "https://jobs.hafen-karriere.de/_/apply/form?prj=1"}}))
        return settings, store, job.id

    def test_confirmation_from_form_domain(self):
        settings, store, jid = self._store()
        imap = FakeIMAP([{"from": "Hafen Karriere <noreply@hafen-karriere.de>", "subject": "Eingangsbestätigung Ihrer Bewerbung",
                          "text": "Vielen Dank für Ihre Bewerbung als Werkstudent*in."}])
        notes = mailer.check_inbox(settings, store, imap_factory=imap)
        row = store.get(jid)
        self.assertEqual(row["status"], "submitted")
        self.assertTrue(row["response_note"].startswith("Получение подтверждено"))
        self.assertTrue(notes)
        self.assertEqual(mailer.check_inbox(settings, store, imap_factory=imap), [])  # повторно не шумит
        store.close()

    def test_confirmation_when_server_returns_text_first(self):
        settings, store, jid = self._store()
        imap = FakeIMAP([{"from": "Hafen Karriere <noreply@hafen-karriere.de>", "subject": "=?UTF-8?Q?Eingangsbest=C3=A4tigung_Ihrer_Bewerbung?=",
                          "text": "Vielen Dank für Ihre Bewerbung als Werkstudent*in."}], text_first=True)
        mailer.check_inbox(settings, store, imap_factory=imap)
        row = store.get(jid)
        self.assertEqual(row["status"], "submitted")
        self.assertIn("Eingangsbestätigung", row["response_note"])
        store.close()

    def test_blank_subject_response_is_reread(self):
        settings, store, jid = self._store()
        store.update(jid, "response", "прочитай письмо", response_note=" — прочитай письмо", response_at="2026-10-20T21:03:35")
        imap = FakeIMAP([{"from": "Hafen Karriere <noreply@hafen-karriere.de>", "subject": "Eingangsbestätigung Ihrer Bewerbung",
                          "text": "Vielen Dank für Ihre Bewerbung als Werkstudent*in."}], text_first=True)
        mailer.check_inbox(settings, store, imap_factory=imap)
        row = store.get(jid)
        self.assertEqual(row["status"], "submitted")
        self.assertTrue(row["response_note"].startswith("Получение подтверждено"))
        store.close()

    def test_rejection_is_response(self):
        settings, store, jid = self._store()
        imap = FakeIMAP([{"from": "noreply@hafen-karriere.de", "subject": "Ihre Bewerbung",
                          "text": "Vielen Dank für Ihre Bewerbung. Leider haben wir uns für andere Kandidaten entschieden."}])
        mailer.check_inbox(settings, store, imap_factory=imap)
        self.assertEqual(store.get(jid)["status"], "response")
        store.close()

    def test_job_alerts_ignored(self):
        settings, store, jid = self._store()
        imap = FakeIMAP([{"from": "jobagent@arbeitsagentur.de", "subject": "Neue Stellen: Bewerbung Hafen Consulting",
                          "text": "Werkstudent bei Hafen Consulting – jetzt Bewerbung senden"}])
        mailer.check_inbox(settings, store, imap_factory=imap)
        self.assertEqual(store.get(jid)["status"], "submitted")
        store.close()

    def test_portal_attempts_do_not_count_as_emails(self):
        settings, store, jid = self._store()
        self.assertEqual(store.sent_since(datetime(2026, 10, 19)), 0)
        store.close()


# --------------------------------------------------------------------------- настоящий браузер на локальных копиях форм

def _browser_ok() -> bool:
    if not runner.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        b = p.chromium.launch(headless=True)
        b.close()
        p.stop()
        return True
    except Exception:
        return False


BROWSER = _browser_ok()


class ScriptedIO:
    def __init__(self, holder, answers):
        self.holder, self.answers, self.said = holder, list(answers), []

    def say(self, text):
        self.said.append(text)

    def ask(self, prompt):
        self.said.append(prompt)
        a = self.answers.pop(0) if self.answers else "q"
        return a(self.holder["s"]) if callable(a) else a


class RecordingSession(runner.FormSession):
    def close(self):
        try:
            p = self.page()
            self.final = {"name": p.evaluate("window.name"), "consent": p.evaluate("window.consentChoice || ''"),
                          "files": p.evaluate("document.querySelectorAll('#files li').length")}
        except Exception as exc:
            self.final = {"error": str(exc)}
        super().close()


@unittest.skipUnless(BROWSER, "нет Playwright/Chromium — браузерные тесты пропущены")
class BrowserFillTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.settings = make_project()
        (self.tmp / "documents" / "zeugnisse").mkdir()
        (self.tmp / "documents" / "zeugnisse" / "Zeugnis.pdf").write_bytes(b"%PDF-1.4 z")
        self.store = Store(self.settings.db_path)
        job = Job(id="ba:TEST-1", source="ba", company="Test GmbH", title="Werkstudent Data", source_url="https://example.org")
        self.store.insert(job, None, "prepared", "")
        self.store.update(job.id, "prepared", "", channel="portal", subject="Bewerbung als Werkstudent Data (Ref. TEST-1)",
                          body="Sehr geehrte Damen und Herren,\n\nich bewerbe mich.\n\nMit freundlichen Grüßen\nMax Mustermann\nMusterstraße 12, 20095 Hamburg\n+49 151")
        self.jid = job.id
        self.holder = {}

    def tearDown(self):
        self.store.close()

    def run_fill(self, page, answers):
        def factory():
            s = RecordingSession(self.settings, headless=True, channel="chromium", profile_dir=self.tmp / "profile")
            self.holder["s"] = s
            return s
        io = ScriptedIO(self.holder, answers)
        status = runner.fill_job(self.settings, self.store, self.jid, url=(FIX / page).as_uri(), io=io, session_factory=factory)
        return status, io, self.holder["s"].final

    def test_concludis_fill_submit_and_doc_types(self):
        status, io, final = self.run_fill("concludis.html", ["f", ""])
        self.assertEqual(status, "submitted", "\n".join(io.said))
        data = json.loads(final["name"])
        self.assertEqual(data["sex"], "m")
        self.assertEqual(data["source"], "Bundesagentur für Arbeit")
        self.assertEqual(sorted(data["docs"]), ["Anschreiben", "Lebenslauf", "Zeugnis"])  # повторное «f» не загрузило файлы второй раз
        self.assertEqual(data["street"], "Musterstraße 12")
        self.assertEqual(data["helper"], "")
        self.assertEqual(data["parse"], 0)
        self.assertEqual(self.store.get(self.jid)["status"], "submitted")
        events = [e["status"] for e in self.store.events(self.jid)]
        self.assertIn("attempted", events)

    def test_ads_job_page_cookie_captcha_consent(self):
        def user_ticks_consent(session):
            session.page().check("#dataProtection")
            return ""
        status, io, final = self.run_fill("ads.html", ["", user_ticks_consent])
        text = "\n".join(io.said)
        self.assertEqual(status, "submitted", text)
        self.assertEqual(final["consent"], "necessary")           # cookies: только необходимые
        self.assertIn("FriendlyCaptcha", text)
        self.assertIn("галочку согласия", text)
        self.assertIn("Сайт не принял форму", text)               # первая попытка без согласия
        events = [e["detail"] for e in self.store.events(self.jid)]
        self.assertTrue(any(d.startswith("форма не принята") for d in events))

    def test_generic_new_tab_and_user_questions(self):
        def user_answers(session):
            p = session.page()
            p.fill("input[name=gehalt]", "nach Vereinbarung")
            p.check("input[name=permit][value=ja]")
            p.check("input[name=privacy]")
            return ""
        status, io, final = self.run_fill("generic_job.html", [user_answers])
        text = "\n".join(io.said)
        self.assertEqual(status, "submitted", text)
        self.assertIn("Нажал «Jetzt online bewerben»", text)
        self.assertIn("зарплатные ожидания", text)
        self.assertIn("Arbeitserlaubnis", text)
        data = json.loads(final["name"])
        self.assertEqual(data["fullname"], "Max Mustermann")
        self.assertEqual(data["mail2"], "max.mustermann@example.com")
        self.assertEqual((data["str"], data["hnr"]), ("Musterstraße", "12"))
        self.assertEqual(data["quelle"], "Bundesagentur für Arbeit")
        self.assertTrue(data["msg"].startswith("Sehr geehrte"))
        self.assertFalse(data["talent"])

    def test_already_submitted_not_reopened(self):
        self.store.update(self.jid, "submitted", "уже")
        status = runner.fill_job(self.settings, self.store, self.jid, url="file:///nothing", io=ScriptedIO({}, []),
                                 session_factory=lambda: self.fail("браузер не должен открываться"))
        self.assertEqual(status, "submitted")


if __name__ == "__main__":
    unittest.main()
