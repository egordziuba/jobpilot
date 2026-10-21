import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import BA_JOBS, PERSONIO_XML, FakeFetcher, FakeIMAP, FakeSMTP, make_project  # noqa: E402

from jobpilot import mailer, pipeline  # noqa: E402
from jobpilot.analyze import analyze, find_channel, requirements  # noqa: E402
from jobpilot.letters import LetterError, compose  # noqa: E402
from jobpilot.models import Job  # noqa: E402
from jobpilot.sources import ats, ba, manual  # noqa: E402
from jobpilot.store import Store  # noqa: E402
from jobpilot.textutil import clean_text, company_key, title_key  # noqa: E402

TODAY = date(2026, 10, 19)
NOSLEEP = lambda s: None  # noqa: E731


def job_from(idx):
    item, desc = BA_JOBS[idx]
    j = ba.parse_item(item)
    j.description = clean_text(desc)
    return j


class TextTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(title_key("Werkstudent:in KI (m/w/d)"), title_key("Werkstudentin KI (w/m/d)"))
        self.assertEqual(company_key("Kaffeehaus GmbH Abteilung HRO/HRO-P"), "kaffeehaus")
        self.assertEqual(company_key("Gebr. Heinemann SE & Co. KG"), "gebr heinemann")

    def test_markdown_mailto(self):
        self.assertIn("bewerbung@x.de", clean_text("[**bewerbung@x.de**](https://mailto:bewerbung@x.de)"))
        self.assertNotIn("**", clean_text("**Deine Aufgaben**"))


class AnalyzeTests(unittest.TestCase):
    geo = {"home": [53.2464, 10.4115], "hamburg": [53.5503, 9.993], "max_km_from_hamburg": 35, "max_km_from_home": 40}

    def test_close_email_with_contact(self):
        a = analyze(job_from(0), self.geo, TODAY)
        self.assertTrue(a.ok, a.reason)
        self.assertEqual(a.tier, "close")
        self.assertEqual((a.channel, a.email), ("email", "bewerbung@alpha-logistik.de"))
        self.assertEqual(a.contact, {"salutation": "Frau", "name": "Schulz", "full_name": "Anna Schulz"})
        self.assertEqual(a.gaps, [])

    def test_question_mail_with_portal_is_not_email(self):
        a = analyze(job_from(1), self.geo, TODAY)
        self.assertTrue(a.ok, a.reason)
        self.assertEqual(a.channel, "portal")

    def test_rejects_sales(self):
        a = analyze(job_from(2), self.geo, TODAY)
        self.assertFalse(a.ok)
        self.assertIn("профиль", a.reason)

    def test_power_bi_required_is_gap(self):
        a = analyze(job_from(3), self.geo, TODAY)
        self.assertTrue(a.ok, a.reason)
        self.assertTrue(any("Power BI" in g for g in a.gaps))
        self.assertEqual(a.channel, "email")

    def test_far_away_rejected_remote_accepted(self):
        self.assertIn("далеко", analyze(job_from(4), self.geo, TODAY).reason)
        remote = analyze(job_from(5), self.geo, TODAY)
        self.assertTrue(remote.ok, remote.reason)
        self.assertIn("удалённо", remote.location_note)

    def test_hours_over_20_rejected(self):
        self.assertIn("часы", analyze(job_from(6), self.geo, TODAY).reason)

    def test_external_listing(self):
        a = analyze(job_from(7), self.geo, TODAY)
        self.assertTrue(a.ok, a.reason)
        self.assertEqual(a.channel, "external")

    def test_german_c1_gap_but_deutschland_ignored(self):
        a = analyze(job_from(9), self.geo, TODAY)
        self.assertTrue(any("Немецкий" in g for g in a.gaps))
        reqs, gaps = requirements("Wir sind in ganz Deutschland tätig.")
        self.assertEqual(gaps, [])

    def test_soft_tool_is_not_gap(self):
        reqs, gaps = requirements("Erste Erfahrung mit Power BI ist von Vorteil.")
        self.assertEqual(gaps, [])
        self.assertEqual(reqs[0]["s"], "unk")

    def test_good_python_is_gap(self):
        _, gaps = requirements("Du hast gute Python-Kenntnisse.")
        self.assertTrue(gaps)

    def test_expired_deadline(self):
        j = job_from(0)
        j.description += "\nBewerbungsfrist: 01.10.2026"
        self.assertIn("срок", analyze(j, self.geo, TODAY).reason)

    def test_not_werkstudent(self):
        j = Job(id="x", source="manual", company="A", title="Sachbearbeiter Automatisierung (m/w/d)", description="Vollzeitstelle")
        self.assertFalse(analyze(j, self.geo, TODAY).ok)

    def test_noreply_ignored(self):
        j = Job(id="x", source="ba", company="A", title="t")
        self.assertEqual(find_channel(j, "Bewerbung bitte an noreply@firma.de"), ("portal", ""))

    def test_email_explicitly_not_accepted(self):
        j = Job(id="x", source="ba", company="A", title="t")
        text = ("Deine Bewerbung richtest du bitte an karriere@firma.de, Fragen beantwortet Frau X. "
                "Bewerbungen per E-Mail können leider nicht berücksichtigt werden.")
        self.assertEqual(find_channel(j, text), ("portal", ""))
        self.assertEqual(find_channel(j, "Bitte sende deine Bewerbung an bewerbung@firma.de"), ("email", "bewerbung@firma.de"))


class LetterTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.s = make_project()

    def test_german_letter_only_confirmed_facts(self):
        j = job_from(0)
        a = analyze(j, self.s.geo, TODAY)
        letter = compose(j, a, self.s.facts, self.s.applicant)
        self.assertTrue(letter.body.startswith("Sehr geehrte Frau Schulz,"))
        self.assertIn("15–20 Stunden", letter.body)
        self.assertIn("(Ref. 10001-1-S)", letter.subject)
        self.assertTrue(letter.subject.startswith("Bewerbung als Werkstudent KI"))
        for word in ("Power BI", "VBA", "Power Apps", "Ausbildung abgeschlossen"):
            self.assertNotIn(word, letter.body)

    def test_forbidden_note_blocks_letter(self):
        j = job_from(0)
        j.note = "Ich habe fortgeschrittene Python Kenntnisse und Power BI."
        a = analyze(j, self.s.geo, TODAY)
        with self.assertRaises(LetterError):
            compose(j, a, self.s.facts, self.s.applicant)

    def test_english_letter(self):
        j = Job(id="manual:l", source="manual", company="L", title="Working Student – Applied AI & Process Automation (m/f/x)",
                description="We are looking for a working student. You will build AI agents and automate workflows and processes. "
                            "Application by e-mail to career@l.com", email_hint="career@l.com")
        a = analyze(j, self.s.geo, TODAY)
        letter = compose(j, a, self.s.facts, self.s.applicant)
        self.assertEqual(letter.language, "en")
        self.assertTrue(letter.body.startswith("Dear Hiring Team,"))


class SourceTests(unittest.TestCase):
    def test_personio(self):
        jobs = ats.personio(FakeFetcher(personio_xml=PERSONIO_XML), {"slug": "acme", "company": "ACME GmbH"})
        self.assertEqual(len(jobs), 1)
        self.assertIn("Automatisierung", jobs[0].description)
        self.assertEqual(jobs[0].apply_url, "https://acme.jobs.personio.de/job/555")

    def test_manual_leads_example(self):
        root = Path(__file__).resolve().parent.parent
        jobs = manual.load(root / "examples")
        ids = {j.id for j in jobs}
        self.assertIn("manual:beispiel-ki-werkstudent", ids)


class PipelineTests(unittest.TestCase):
    def test_dry_run_prepares_but_never_sends(self):
        tmp, s = make_project("dry_run")
        smtp = FakeSMTP("ok")
        res = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        queued = {r["id"] for r in store.by_status("queued")}
        self.assertIn("ba:10001-1-S", queued)          # близкая, e-mail, без пробелов
        self.assertIn("ba:10001-6-S", queued)          # удалённая
        self.assertNotIn("ba:10001-4-S", queued)       # Power BI обязателен → ждёт решения
        self.assertEqual(store.get("ba:10001-4-S")["status"], "prepared")
        self.assertEqual(store.get("ba:10001-2-S")["status"], "verified")  # смежная, только портал → по желанию
        self.assertIsNone(store.get("ba:10001-9-S"))   # дубль не заведён
        # работодателям не ушло ничего; ушла только сводка себе
        self.assertTrue(all(m["To"] == s.applicant["email"] for m in smtp.sent))
        self.assertEqual(res.sent, 0)
        self.assertTrue((s.data_dir / "reports" / "latest.html").exists())
        store.close()

    def test_live_sends_with_limits_and_never_resends(self):
        tmp, s = make_project("live", extra_run={"max_emails_per_run": 1, "report_email_to_self": False})
        smtp = FakeSMTP("ok")
        res = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        self.assertEqual(res.sent, 1)
        self.assertEqual(len(smtp.sent), 1)
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "multipart/mixed")
        self.assertTrue(any(p.get_filename() == "Lebenslauf_Max_Mustermann.pdf" for p in msg.iter_attachments()))
        # второй запуск отправляет следующее, первое не повторяет
        res2 = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        self.assertEqual(res2.sent, 1)
        tos = [m["To"] for m in smtp.sent]
        self.assertEqual(len(tos), len(set(tos)))

    def test_daily_limit(self):
        tmp, s = make_project("live", extra_run={"max_emails_per_day": 1, "max_emails_per_run": 5, "report_email_to_self": False})
        smtp = FakeSMTP("ok")
        pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        res2 = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        self.assertEqual(len(smtp.sent), 1)
        self.assertEqual(res2.sent, 0)

    def test_auth_error_keeps_queue(self):
        tmp, s = make_project("live", extra_run={"report_email_to_self": False})
        res = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=FakeSMTP("auth").factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-1-S")["status"], "queued")
        self.assertTrue(any("SMTP_PASSWORD" in e for e in res.errors))
        store.close()

    def test_disconnect_marks_unknown_and_is_not_retried(self):
        tmp, s = make_project("live", extra_run={"max_emails_per_run": 1, "report_email_to_self": False, "check_inbox": False})
        pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=FakeSMTP("disconnect").factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        unknown = store.by_status("send_unknown")
        self.assertEqual(len(unknown), 1)
        uid = unknown[0]["id"]
        store.close()
        smtp = FakeSMTP("ok")
        pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get(uid)["status"], "send_unknown")
        self.assertNotIn(store.get(uid)["email"], [m["To"] for m in smtp.sent])
        store.close()

    def test_closed_posting_not_sent(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        s.cfg["run"]["send_mode"] = "live"
        smtp = FakeSMTP("ok")
        res = pipeline.run(s, fetcher=FakeFetcher(gone={"10001-1-S"}), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-1-S")["status"], "closed")
        self.assertNotIn("bewerbung@alpha-logistik.de", [m["To"] for m in smtp.sent])
        store.close()

    def test_marks_csv_updates_status_and_blocks_resend(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-1-S")["status"], "queued")
        store.close()
        (s.data_dir / "marks.csv").write_text(
            "id,status,date,company,title,channel,note\n"
            "ba:10001-1-S,submitted,2026-10-19,Alpha Logistik GmbH,Werkstudent,form,über das Formular\n"
            "ba:unknown-1,closed,2026-10-19,Irgendwer GmbH,Werkstudent,form,\n"
            "ba:10001-6-S,quatsch,2026-10-19,X,Y,form,\n", encoding="utf-8")
        s.cfg["run"]["send_mode"] = "live"
        smtp = FakeSMTP("ok")
        res = pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        row = store.get("ba:10001-1-S")
        self.assertEqual((row["status"], row["sent_at"]), ("submitted", "2026-10-19"))
        self.assertNotIn("bewerbung@alpha-logistik.de", [m["To"] for m in smtp.sent])
        self.assertTrue(store.in_history("Alpha Logistik GmbH", "Werkstudent"))
        self.assertTrue(any("marks.csv" in n for n in res.notes))
        # повторный импорт ничего не ломает и не откатывает дальнейшие статусы
        store.update("ba:10001-1-S", "interview", "приглашение")
        self.assertEqual(store.import_marks_csv(s.data_dir / "marks.csv"), 0)
        self.assertEqual(store.get("ba:10001-1-S")["status"], "interview")
        store.close()

    def test_history_blocks_company(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        (s.data_dir / "already_applied.csv").write_text("company,title,email,date,channel,note\nAlpha Logistik GmbH,,,2026-09-01,email,\n", encoding="utf-8")
        pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-1-S")["status"], "skipped")
        store.close()

    def test_company_cooldown(self):
        tmp, s = make_project("live", extra_run={"report_email_to_self": False, "max_emails_per_run": 5, "max_emails_per_day": 9})
        jobs = [BA_JOBS[0], (dict(BA_JOBS[5][0], firma="Alpha Logistik GmbH", referenznummer="10001-11-S",
                                  stellenangebotsTitel="Werkstudent Prozessautomatisierung KI Workflows (m/w/d)"),
                             BA_JOBS[5][1].replace("hr@zeta-remote.de", "jobs@alpha-logistik.de"))]
        smtp = FakeSMTP("ok")
        pipeline.run(s, fetcher=FakeFetcher(ba_jobs=jobs), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        self.assertEqual(len(smtp.sent), 1)

    def test_inbox_auto_reply_and_answer(self):
        tmp, s = make_project("live", extra_run={"report_email_to_self": False, "max_emails_per_run": 5})
        smtp = FakeSMTP("ok")
        pipeline.run(s, fetcher=FakeFetcher(), smtp_factory=smtp.factory, imap_factory=FakeIMAP([]), today=TODAY, sleep=NOSLEEP)
        inbox = [
            {"from": "noreply@alpha-logistik.de", "subject": "Eingangsbestätigung Ihrer Bewerbung", "text": "Vielen Dank"},
            {"from": "anna@zeta-remote.de", "subject": "Ihre Bewerbung", "text": "Wir laden Sie gern zu einem Kennenlernen ein."},
        ]
        store = Store(s.db_path)
        notes = mailer.check_inbox(s, store, FakeIMAP(inbox))
        self.assertIn("Получение подтверждено", store.get("ba:10001-1-S")["response_note"])
        self.assertEqual(store.get("ba:10001-6-S")["status"], "response")
        self.assertIn("приглашение", store.get("ba:10001-6-S")["response_note"])
        self.assertTrue(notes)
        store.close()

    def test_personio_job_goes_to_portal_list(self):
        tmp, s = make_project("dry_run", with_personio=True, extra_run={"report_email_to_self": False})
        pipeline.run(s, fetcher=FakeFetcher(ba_jobs=[], personio_xml=PERSONIO_XML), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        row = store.get("personio:acme:555")
        self.assertEqual(row["status"], "prepared")
        self.assertEqual(row["channel"], "portal")
        self.assertTrue(row["body"])
        store.close()

    def test_lock_prevents_parallel_runs(self):
        tmp, s = make_project("dry_run")
        (s.data_dir / "run.lock").write_text("123", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)


class RulesTests(unittest.TestCase):
    def test_nationwide_non_remote_skipped_without_detail(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        s.cfg["search"]["ba"] = [{"where": "", "radius_km": 0, "angebotsart": [34], "queries": ["Werkstudent KI"]}]
        far = (BA_JOBS[4][0], BA_JOBS[4][1])          # München, без удалёнки
        remote = (BA_JOBS[5][0], BA_JOBS[5][1])       # Berlin, 100 % remote
        f = FakeFetcher(ba_jobs=[far, remote])
        pipeline.run(s, fetcher=f, today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-5-S")["status"], "filtered")
        self.assertEqual(store.get("ba:10001-6-S")["status"], "queued")
        self.assertFalse(any("MTAwMDEtNS1T" in c for c in f.calls))   # детали дальней вакансии не грузились
        store.close()

    def test_reclassify_after_rules_change(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        store.update("ba:10001-2-S", "prepared", "старые правила", reason="старые правила")
        store.set_meta("rules_version", "1")
        store.close()
        res = pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        self.assertEqual(store.get("ba:10001-2-S")["status"], "verified")
        self.assertTrue(any("пересмотрено" in n for n in res.notes))
        store.close()


class CliTests(unittest.TestCase):
    def test_every_module_compiles_and_cli_starts(self):
        import py_compile
        import subprocess
        root = Path(__file__).resolve().parent.parent
        for f in sorted((root / "jobpilot").rglob("*.py")):
            py_compile.compile(str(f), doraise=True)      # синтаксическая ошибка в любом файле = провал
        r = subprocess.run([sys.executable, "-m", "jobpilot", "--help"], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", env={**__import__("os").environ, "PYTHONUTF8": "1"}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_approve_moves_prepared_to_queue(self):
        tmp, s = make_project("dry_run", extra_run={"report_email_to_self": False})
        pipeline.run(s, fetcher=FakeFetcher(), today=TODAY, sleep=NOSLEEP)
        store = Store(s.db_path)
        row = store.get("ba:10001-4-S")
        self.assertEqual(row["status"], "prepared")
        store.update(row["id"], "queued", "одобрено вручную", approved=1)
        self.assertEqual(store.get(row["id"])["status"], "queued")
        hist = [e["status"] for e in store.events(row["id"])]
        self.assertEqual(hist[-1], "queued")
        store.close()


if __name__ == "__main__":
    unittest.main()
