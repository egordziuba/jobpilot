"""Синтетические ответы: только вымышленные адресаты и домены example.test."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from email.message import EmailMessage
from email.policy import default
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import FakeIMAP
from jobpilot import mailer, report
from jobpilot.models import Job
from jobpilot.pipeline import RunResult
from jobpilot.store import Store


def message(body, subject="Ihre Bewerbung", *, cte=None, charset="utf-8", html=False, number=1,
            sender="Mira Beispiel <hr@nebelwerk.example.test>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "Toni Muster <toni@applicant.example.test>"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 20 Oct 2026 10:30:00 +0200"
    msg["Message-ID"] = f"<reply-{number}@nebelwerk.example.test>"
    msg.set_content(body, subtype="html" if html else "plain", charset=charset, cte=cte)
    return msg


# Each case is a separately reported unittest and is also used for the PR's result table.
CASES = [
    ("thanks_then_invitation", "Vielen Dank für Ihre Bewerbung. Wir möchten Sie zu einem Vorstellungsgespräch einladen.", "invite"),
    ("unrelated_leider_invitation", "Leider kann ich am Montag nicht. Wir laden Sie zum Kennenlerngespräch ein.", "invite"),
    ("laden_sie_ein", "Wir laden Sie gern zu einem Kennenlernen ein.", "invite"),
    ("kennenlerngespraech", "Wir planen ein Kennenlerngespräch mit Ihnen.", "invite"),
    ("telefon_interview", "Wir möchten ein Telefon-Interview mit Ihnen führen.", "invite"),
    ("video_interview", "We would like to invite you to a Video Interview.", "invite"),
    ("english_interview", "Thank you for your application. We invite you to an interview.", "invite"),
    ("terminvorschlag", "Unser Terminvorschlag: Dienstag um 10 Uhr.", "invite"),
    ("wann_zeit", "Wann hätten Sie Zeit für uns?", "invite"),
    ("calendly_invitation", "Please choose a time: https://calendly.example.test/nebelwerk", "invite"),
    ("teams_invitation", "Bitte buchen Sie einen Termin: https://teams.example.test/nebelwerk", "invite"),
    ("zoom_invitation", "We would like to meet you: https://zoom.example.test/nebelwerk", "invite"),
    ("meet_invitation", "Please book a meeting: https://meet.example.test/nebelwerk", "invite"),
    ("leider_nicht_rejection", "Leider können wir Ihre Bewerbung nicht berücksichtigen.", "reject"),
    ("leider_keine_rejection", "Leider haben wir keine passende Stelle.", "reject"),
    ("leider_anderen_rejection", "Leider haben wir uns für einen anderen Bewerber entschieden.", "reject"),
    ("leider_andere_kandidaten", "Vielen Dank für Ihre Bewerbung. Leider haben wir uns für andere Kandidaten entschieden.", "reject"),
    ("leider_absagen_rejection", "Wir müssen Ihnen leider absagen.", "reject"),
    ("nicht_weiter_rejection", "Wir werden Ihre Bewerbung nicht weiter berücksichtigen.", "reject"),
    ("andere_person_rejection", "Wir haben uns für eine andere Person entschieden.", "reject"),
    ("anderer_kandidat_rejection", "Wir haben uns für einen anderen Kandidaten entschieden.", "reject"),
    ("english_not_forward", "We regret our decision not to move forward.", "reject"),
    ("english_other_candidates", "Thank you for your application. We decided to proceed with other candidates.", "reject"),
    ("german_confirmation", "Vielen Dank für Ihre Bewerbung. Wir haben sie erhalten.", "confirm"),
    ("english_confirmation", "We have received your application.", "confirm"),
    ("auto_reply_confirmation", "Automatic reply: Thank you for your application.", "confirm"),
    ("confirmation_bare_termin", "Vielen Dank für Ihre Bewerbung. Ein Termin steht noch nicht fest.", "confirm"),
    ("confirmation_bare_gespraech", "Ihre Bewerbung ist bei uns eingegangen. Nach dem Gespräch im Team melden wir uns.", "confirm"),
    ("documents_request", "Vielen Dank für Ihre Bewerbung. Bitte senden Sie uns noch Ihre Unterlagen.", "request_info"),
    ("zeugnisse_request", "Bitte senden Sie uns die fehlenden Zeugnisse.", "request_info"),
    ("enrollment_request", "Bitte senden Sie uns Ihre Immatrikulationsbescheinigung.", "request_info"),
    ("rueckfrage", "Wir haben eine Rückfrage zu Ihrer Bewerbung.", "request_info"),
    ("english_request", "Thank you for your application. Could you send your transcript?", "request_info"),
    ("german_absence", "Abwesenheitsnotiz: Ich bin derzeit abwesend. Bei Fragen zum Vorstellungsgespräch wenden Sie sich an das Team.", "ooo"),
    ("english_absence", "I am out of office. Thank you for your application.", "ooo"),
    ("vacation", "I am currently on vacation.", "ooo"),
    ("bare_leider", "Leider dauert unsere Prüfung etwas länger.", "other"),
    ("leider_different_sentence", "Leider dauert es länger. Keine Sorge, wir melden uns.", "other"),
    ("bare_termin", "Der Termin steht noch nicht fest.", "other"),
    ("bare_gespraech", "Nach dem Gespräch im Team melden wir uns.", "other"),
    ("bare_scheduling_link", "Unser Team nutzt https://zoom.example.test/nebelwerk", "other"),
    ("ordinary_human_reply", "Danke, wir melden uns nächste Woche.", "other"),
]


# Manager regression cases: sentence context, realistic German/English HR wording.
CONTEXT_CASES = [
    ("negated_formal_invitation", "Leider können wir Sie nicht zu einem Vorstellungsgespräch einladen, da wir uns für eine andere Kandidatin entschieden haben.", "reject"),
    ("provisional_review", "Wir prüfen Ihre Unterlagen und melden uns, um ggf. einen Termin für ein Vorstellungsgespräch zu vereinbaren.", "confirm"),
    ("shortlisted_english", "We have received your application. If you are shortlisted, we will invite you to an interview.", "confirm"),
    ("english_move_forward_other_candidates", "Unfortunately, we have decided to move forward with other candidates whose experience more closely matches.", "reject"),
    ("formal_absage", "Nach sorgfältiger Prüfung müssen wir Ihnen eine Absage erteilen.", "reject"),
    ("shortlisted_formal", "Sollten Sie in die engere Auswahl kommen, laden wir Sie zu einem Interview ein.", "confirm"),
    ("friday_unavailable_thursday_invite", "Leider ist Freitag nicht möglich, wir laden Sie aber gern am Donnerstag zum Vorstellungsgespräch ein.", "invite"),
    ("personio_du_rejection", "Vielen Dank für deine Bewerbung. Leider können wir dich nicht zu einem Interview einladen.", "reject"),
    ("personio_du_invitation", "Vielen Dank für deine Bewerbung. Wir laden dich zu einem Kennenlerngespräch mit unserem Team ein.", "invite"),
    ("gegen_sie", "Nach sorgfältiger Prüfung haben wir uns gegen Sie entschieden.", "reject"),
    ("gegen_bewerbung", "Wir haben uns gegen Ihre Bewerbung entschieden.", "reject"),
    ("anderweitig_besetzt_conditional_interview", "Die Stelle ist anderweitig besetzt. Falls sich etwas ändert, laden wir Sie zu einem Interview ein.", "reject"),
    ("bereits_besetzt", "Vielen Dank für deine Bewerbung. Die Position ist bereits besetzt.", "reject"),
    ("keine_stelle_anbieten", "Wir können Ihnen zurzeit keine Stelle anbieten.", "reject"),
    ("english_position_filled", "The position has been filled. If another role opens, we will invite you to an interview.", "reject"),
    ("english_not_be_moving_forward", "After reviewing your application, we will not be moving forward.", "reject"),
    ("english_regret_to_inform", "We regret to inform you that your application was unsuccessful.", "reject"),
    ("english_unfortunately_unable", "Unfortunately, we are unable to invite you to an interview.", "reject"),
    ("english_unfortunately_not", "Unfortunately, your application was not successful.", "reject"),
    ("english_unfortunately_other", "Unfortunately, other applicants better match our requirements.", "reject"),
    ("absage_unrelated_event", "Vielen Dank für Ihre Bewerbung. Die Absage unseres Sommerfests betrifft nur die Veranstaltung.", "confirm"),
    ("personio_gegebenenfalls", "Wir prüfen deine Unterlagen und laden dich gegebenenfalls zu einem Interview ein.", "confirm"),
    ("conditional_sofern", "Sofern Ihr Profil passt, laden wir Sie zu einem Vorstellungsgespräch ein.", "confirm"),
    ("conditional_wenn", "Wenn deine Unterlagen vollständig sind, laden wir dich zu einem Interview ein.", "confirm"),
    ("conditional_bei_interesse", "Bei Interesse laden wir Sie zu einem Vorstellungsgespräch ein.", "confirm"),
    ("conditional_should_you", "Should you be shortlisted, we will invite you to an interview.", "confirm"),
    ("conditional_once", "Once we have reviewed your application, we may invite you to an interview.", "confirm"),
    ("conditional_in_case", "In case you are shortlisted, we will invite you to an interview.", "confirm"),
    # Real-world patterns (anonymised): rejection without «leider», confirmation via «Unterlagen eingegangen».
    ("other_candidates_without_leider", "Wir haben uns intensiv mit Ihrem Profil beschäftigt. Nach sorgfältiger Abwägung haben wir uns jedoch dazu entschieden, den weiteren Prozess mit anderen Kandidaten fortzuführen. Vielleicht ergibt sich später die Gelegenheit, miteinander ins Gespräch zu kommen.", "reject"),
    ("other_applicants_continue", "Wir haben uns entschieden, den Bewerbungsprozess mit anderen Bewerberinnen und Bewerbern fortzusetzen.", "reject"),
    ("documents_arrived_confirmation", "Wir bedanken uns für die Zusendung Ihrer Bewerbungsunterlagen. Ihre Unterlagen sind sicher bei uns eingegangen. Wir geben Ihnen so schnell wie möglich ein Feedback.", "confirm"),
    ("negation_keine", "Vielen Dank für Ihre Bewerbung. Aktuell sind keine Vorstellungsgespräche geplant.", "confirm"),
    ("negation_cannot", "We have received your application. We cannot arrange an interview at this stage.", "confirm"),
    ("negation_wont", "We have received your application. We won't schedule an interview until our review is complete.", "confirm"),
    ("negation_relative_clause", "Leider können wir Sie nicht, wie ursprünglich geplant, zum Interview einladen.", "reject"),
    ("conditional_scheduling_link", "If you are shortlisted, please choose a time at https://calendly.example.test/nebelwerk", "confirm"),
    ("english_contrast_invitation", "Unfortunately Friday is not possible, but we invite you to an interview on Thursday.", "invite"),
]


class ClassifierTests(unittest.TestCase):
    pass


def classification_case(body, expected):
    def test(self):
        msg = message(body)
        imap = FakeIMAP([msg], text_first=True)
        subject, sender, text = mailer._fetch(imap, b"1")
        actual, evidence = mailer.classify_reply(subject, text)
        self.assertEqual(actual, expected)
        self.assertEqual(imap.fetches[0][1], "(BODY.PEEK[]<0.200000>)")
        if expected != "other":
            self.assertTrue(evidence)
            self.assertIn(evidence, subject + ". " + " ".join(text.split()))
        else:
            self.assertEqual(evidence, "")
    return test


for name, body, expected in CASES + CONTEXT_CASES:
    setattr(ClassifierTests, f"test_{name}", classification_case(body, expected))


class MIMEReplyTests(unittest.TestCase):
    def classify(self, msg, expected="invite", text_first=False):
        parsed = mailer._fetch(FakeIMAP([msg], text_first=text_first), b"1")
        self.assertEqual(mailer.classify_reply(parsed[0], parsed[2])[0], expected)
        return parsed

    def test_base64(self):
        msg = message("Wir laden Sie zum Vorstellungsgespräch ein.", cte="base64")
        self.assertNotIn(b"Vorstellungsgespr", msg.as_bytes())
        self.classify(msg)

    def test_quoted_printable(self):
        msg = message("Vielen Dank für Ihre Bewerbung. Einladung zum Vorstellungsgespräch.", cte="quoted-printable")
        self.assertIn(b"Vorstellungsgespr=C3=A4ch", msg.as_bytes())
        self.classify(msg)

    def test_latin1_charset(self):
        self.classify(message("Einladung zum Vorstellungsgespräch.", charset="iso-8859-1", cte="quoted-printable"))

    def test_windows_charset(self):
        self.classify(message("Einladung zum Kennenlerngespräch.", charset="windows-1252", cte="base64"))

    def test_html_only(self):
        parsed = self.classify(message("<p>Vielen Dank für Ihre Bewerbung.</p><p>Vorstellungsgespr&auml;ch</p>", html=True))
        self.assertIn("Vorstellungsgespräch", parsed[2])
        self.assertNotIn("<p>", parsed[2])

    def test_html_scheduling_href(self):
        self.classify(message('<p>Please choose a time.</p><a href="https://calendly.example.test/nebelwerk">Hier</a>', html=True))

    def test_multipart_alternative_prefers_plain_in_both_orders(self):
        msg = message("Wir laden Sie zum Vorstellungsgespräch ein.")
        msg.add_alternative("<p>Vielen Dank für Ihre Bewerbung</p>", subtype="html")
        for order in (False, True):
            with self.subTest(reverse=order):
                if order:
                    msg.set_payload(list(reversed(msg.get_payload())))
                parsed = self.classify(msg)
                self.assertNotIn("Vielen Dank", parsed[2])

    def test_html_fallback_for_empty_plain(self):
        msg = message(" ")
        msg.add_alternative("<p>Vorstellungsgespräch</p>", subtype="html")
        self.classify(msg)

    def test_pdf_attachment_ignored(self):
        msg = message("Vielen Dank für Ihre Bewerbung.")
        msg.add_attachment(b"%PDF-1.4 Vorstellungsgespr\xc3\xa4ch", maintype="application", subtype="pdf", filename="Einladung.pdf")
        self.assertNotIn("Vorstellung", self.classify(msg, "confirm")[2])

    def test_text_attachment_ignored(self):
        msg = message("Vielen Dank für Ihre Bewerbung.")
        msg.add_attachment("Vorstellungsgespräch", filename="einladung.txt")
        self.classify(msg, "confirm")

    def test_attached_message_ignored(self):
        msg = message("Vielen Dank für Ihre Bewerbung.")
        msg.add_attachment(message("Vorstellungsgespräch", number=2))
        self.classify(msg, "confirm")

    def test_all_plain_parts_collected(self):
        msg = message("Vielen Dank für Ihre Bewerbung.")
        msg.make_mixed()
        msg.attach(message("Wir laden Sie zum Vorstellungsgespräch ein.", number=2))
        self.classify(msg)

    def test_invitation_after_3000_characters(self):
        self.classify(message("Vielen Dank für Ihre Bewerbung. " + "Weitere Informationen. " * 300 + "Vorstellungsgespräch"))

    def test_full_fetch_independent_of_tuple_order(self):
        msg = message("Vorstellungsgespräch", subject="Einladung für Toni")
        for reverse in (False, True):
            self.classify(msg, text_first=reverse)
        # Старый формат ответа сервера: заголовок и тело в отдельных literal.
        raw = msg.as_bytes(policy=default)
        head, body = raw.split(b"\n\n", 1)
        for reverse in (False, True):
            parts = [(b"BODY[HEADER]", head + b"\n\n"), (b"BODY[TEXT]<0>", body)]
            if reverse:
                parts.reverse()
            imap = FakeIMAP([])
            with patch.object(imap, "fetch", return_value=("OK", [b")", *parts])):
                subject, sender, text = mailer._fetch(imap, b"1")
            self.assertEqual(subject, "Einladung für Toni")
            self.assertIn("Vorstellungsgespräch", text)

    def test_unknown_charset_falls_back_without_aborting_inbox(self):
        msg = message("Vorstellungsgespräch")
        imap = FakeIMAP([msg])
        imap.inbox[0]["raw"] = msg.as_bytes().replace(b'charset="utf-8"', b'charset="unknown-test-charset"')
        subject, sender, text = mailer._fetch(imap, b"1")
        self.assertEqual(mailer.classify_reply(subject, text)[0], "invite")

    def test_fetch_failure_not_recorded_as_real_reply(self):
        imap = FakeIMAP([])
        for result in (("NO", [b"unavailable"]), ("OK", [b")"])):
            with patch.object(imap, "fetch", return_value=result), self.assertRaises(mailer.MailError):
                mailer._fetch(imap, b"1")


class InboxReplyTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parent.parent / "work"
        work.mkdir(exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="reply-tests-", dir=work))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.settings = SimpleNamespace(data_dir=self.tmp, secret=lambda key, default="": {
            "IMAP_HOST": "imap.example.test", "SMTP_USER": "toni@applicant.example.test", "SMTP_PASSWORD": "synthetic-test"
        }.get(key, default))
        self.add_job("one")

    def add_job(self, jid, status="submitted", *, company="Nebelwerk GmbH", title="Werkstudent Data"):
        self.store.insert(Job(id=jid, source="manual", company=company, title=title), None, status)
        self.store.update(jid, None, channel="portal", sent_at="2026-10-19T09:00:00", email="hr@nebelwerk.example.test")

    def check(self, messages):
        imap = FakeIMAP(messages)
        with patch("jobpilot.mailer.imaplib.IMAP4_SSL", side_effect=AssertionError("real mailbox forbidden")), \
                patch("jobpilot.mailer.deliver", side_effect=AssertionError("sending forbidden")):
            return mailer.check_inbox(self.settings, self.store, imap)

    def test_confirmation_then_invitation_records_once_each(self):
        messages = [message("Nebelwerk: Vielen Dank für Ihre Bewerbung."),
                    message("Nebelwerk: Wir laden Sie zum Vorstellungsgespräch ein.", number=2)]
        before = len(self.store.events("one"))
        self.assertEqual(len(self.check(messages)), 2)
        row = self.store.get("one")
        self.assertEqual(row["status"], "response")
        self.assertIn("приглашение: «laden Sie zum Vorstellungsgespräch ein»", row["response_note"])
        self.assertEqual(row["response_at"], "2026-10-20T10:30:00+02:00")
        self.assertEqual(len(self.store.events("one")), before + 2)
        self.assertEqual(self.check(messages), [])
        self.assertEqual(len(self.store.events("one")), before + 2)

    def test_repeated_confirmation_deduplicated_across_checks(self):
        msg = message("Nebelwerk: Vielen Dank für Ihre Bewerbung.")
        before = len(self.store.events("one"))
        self.assertTrue(self.check([msg]))
        self.assertEqual(self.check([msg]), [])
        self.assertEqual(len(self.store.events("one")), before + 1)
        self.assertEqual(self.store.get("one")["status"], "submitted")

    def test_two_distinct_confirmation_messages_each_have_an_event(self):
        before = len(self.store.events("one"))
        self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung.", number=i) for i in (1, 2)])
        self.assertEqual(len(self.store.events("one")), before + 2)

    def test_no_message_id_deduplicated(self):
        msg = message("Nebelwerk: Vielen Dank für Ihre Bewerbung.")
        del msg["Message-ID"]
        self.check([msg])
        self.assertEqual(self.check([msg]), [])

    def test_same_message_found_by_both_criteria(self):
        self.store.update("one", None, apply_url="https://careers.nebelwerk.example.test/apply")
        before = len(self.store.events("one"))
        self.assertEqual(len(self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung.")])), 1)
        self.assertEqual(len(self.store.events("one")), before + 1)

    def test_unknown_form_upgraded_by_ats_confirmation(self):
        self.store.update("one", "send_unknown", "ошибка портала после submit", message_id=None)
        before = len(self.store.events("one"))
        msg = message("Nebelwerk: We have received your application.", sender="Nebelwerk <noreply@personio.example.test>")
        self.check([msg])
        row = self.store.get("one")
        self.assertEqual(row["status"], "submitted")
        self.assertIsNone(row["message_id"])
        self.assertEqual(len(self.store.events("one")), before + 1)
        self.assertIn("Получение подтверждено", self.store.events("one")[-1]["detail"])
        self.assertEqual(self.check([msg]), [])

    def test_unknown_form_company_confirmation(self):
        self.store.update("one", "send_unknown", message_id=None)
        self.check([message("Nebelwerk: Ihre Bewerbung ist bei uns eingegangen.")])
        self.assertEqual(self.store.get("one")["status"], "submitted")

    def test_unknown_form_real_reply(self):
        self.store.update("one", "send_unknown", message_id=None)
        self.check([message("Nebelwerk: Wir laden Sie zum Vorstellungsgespräch ein.")])
        self.assertEqual(self.store.get("one")["status"], "response")

    def test_unknown_form_confirmation_does_not_hide_request(self):
        self.store.update("one", "send_unknown", message_id=None)
        self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung."),
                    message("Nebelwerk Bewerbung: Bitte senden Sie uns Ihre Unterlagen.", number=2)])
        self.assertEqual(self.store.get("one")["status"], "response")
        self.assertIn("запрос документов", self.store.get("one")["response_note"])

    def test_unknown_email_not_treated_as_form(self):
        self.store.update("one", "send_unknown", message_id="<outgoing@applicant.example.test>", channel="email")
        self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung.")])
        self.assertEqual(self.store.get("one")["status"], "send_unknown")

    def test_unrelated_sender_cannot_confirm_unknown_form(self):
        self.store.update("one", "send_unknown", message_id=None)
        self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung.", sender="News <news@unrelated.example.test>")])
        self.assertEqual(self.store.get("one")["status"], "send_unknown")

    def test_job_board_cannot_confirm_form(self):
        self.check([message("Nebelwerk: Vielen Dank für Ihre Bewerbung.", sender="News <news@stepstone.example.test>")])
        self.assertIsNone(self.store.get("one")["response_note"])

    def test_ooo_does_not_hide_invitation(self):
        self.check([message("Nebelwerk Bewerbung: I am out of office."),
                    message("Nebelwerk: Vorstellungsgespräch", number=2)])
        self.assertEqual(self.store.get("one")["status"], "response")
        self.assertIn("приглашение", self.store.get("one")["response_note"])

    def test_sent_email_reply_records_once(self):
        self.store.update("one", "sent", channel="email")
        self.check([message("Vielen Dank für Ihre Bewerbung."), message("Vorstellungsgespräch", number=2)])
        self.assertEqual(self.store.get("one")["status"], "response")

    def test_real_replies_remain_response_for_human_decision(self):
        for body in ("Leider können wir Ihre Bewerbung nicht berücksichtigen.",
                     "Bitte senden Sie uns Ihre Unterlagen.", "Wir melden uns zu Ihrer Bewerbung."):
            with self.subTest(body=body):
                self.store.update("one", "submitted")
                self.check([message("Nebelwerk: " + body, number=len(self.store.events("one")))])
                self.assertEqual(self.store.get("one")["status"], "response")

    def test_older_invitation_not_hidden_by_three_message_limit(self):
        messages = [message("Nebelwerk: Vorstellungsgespräch", number=1)]
        messages += [message("Nebelwerk: Vielen Dank für Ihre Bewerbung.", number=i) for i in range(2, 6)]
        self.check(messages)
        self.assertEqual(self.store.get("one")["status"], "response")

    def test_reports_prioritize_invites_then_requests_and_escape_evidence(self):
        self.store.update("one", "response", response_note="отказ: «nicht weiter berücksichtigen»", response_at="2026-10-20")
        self.add_job("two", "response", company="Wolkenfaden GmbH", title="Werkstudent KI")
        self.store.update("two", None, response_note="запрос документов / уточнение: «could you send»", response_at="2026-10-21")
        self.add_job("three", "response", company="Sternmoos GmbH", title="Werkstudent Python")
        self.store.update("three", None, response_note="приглашение: «Vorstellungsgespräch <Toni>»", response_at="2026-10-22")
        self.add_job("four", "send_unknown", company="Dunstlabor GmbH", title="Werkstudent Cloud")
        result = RunResult()
        subject, body = report.summary_email(self.settings, self.store, result)
        doc = Path(report.write_html(self.settings, self.store, result)).read_text(encoding="utf-8")
        for text in (body, doc):
            self.assertLess(text.index("Sternmoos GmbH"), text.index("Wolkenfaden GmbH"))
            self.assertLess(text.index("Wolkenfaden GmbH"), text.index("Nebelwerk GmbH"))
            self.assertLess(text.index("Nebelwerk GmbH"), text.index("Dunstlabor GmbH"))
            for phrase in ("Werkstudent Python", "2026-10-22", "Vorstellungsgespräch", "could you send"):
                self.assertIn(phrase, text)
        self.assertIn("&lt;Toni&gt;", doc)
        self.assertNotIn("<Toni>", doc)


if __name__ == "__main__":
    unittest.main()
