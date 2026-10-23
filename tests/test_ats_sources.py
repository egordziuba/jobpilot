"""Public ATS JSON contracts, bounded fetching and pipeline integration (no network)."""
import copy
import json
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import FakeFetcher, make_project
from jobpilot import mailer, pipeline
from jobpilot.analyze import BAD_EMAIL
from jobpilot.net import FetchError, Fetcher
from jobpilot.sources import ats
from jobpilot.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "ats"
COMPANY = "lumen-fictional"
SOURCES = {
    "lever": ({"company": COMPANY}, f"https://api.lever.co/v0/postings/{COMPANY}"),
    "smartrecruiters": ({"company": COMPANY}, f"https://api.smartrecruiters.com/v1/companies/{COMPANY}/postings"),
    "ashby": ({"board": COMPANY}, f"https://api.ashbyhq.com/posting-api/job-board/{COMPANY}"),
    "workable": ({"subdomain": COMPANY}, f"https://apply.workable.com/api/v1/widget/accounts/{COMPANY}"),
}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ATSFetcher(FakeFetcher):
    """Same interface as helpers.FakeFetcher, with explicit URL/parameter routing."""
    def __init__(self, responses=None):
        super().__init__(ba_jobs=[])
        self.responses = responses if responses is not None else {
            url: fixture(kind) for kind, (_, url) in SOURCES.items()
        }
        if responses is None:
            self.responses[SOURCES["smartrecruiters"][1] + "/sr-1"] = fixture("smartrecruiters_detail")
        self.requests = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        self.requests.append((url, dict(params or {})))
        default = {} if "/postings/" in url else None
        data = self.responses.get(url, default)
        if isinstance(data, Exception):
            raise data
        if callable(data):
            data = data(params or {})
        return copy.deepcopy(data)


class ParsingTests(unittest.TestCase):
    def test_lever(self):
        fetcher = ATSFetcher()
        job, minimal = ats.lever(fetcher, {"company": COMPANY, "company_name": "Display GmbH"})
        self.assertEqual(job.id, f"lever:{COMPANY}:lv-1")
        self.assertEqual(job.source, "lever")
        self.assertEqual(job.company, "Display GmbH")
        self.assertEqual(job.title, "Werkstudent KI & Prozessautomatisierung (m/w/d)")
        self.assertEqual(job.source_url, f"https://jobs.lever.co/{COMPANY}/lv-1")
        self.assertEqual(job.apply_url, job.source_url + "/apply")
        self.assertEqual(job.location, "Hamburg")
        self.assertTrue(job.homeoffice)
        self.assertEqual(job.posted, "")  # Public Lever contract has no guaranteed date.
        self.assertIn("Laufendes Studium", job.description)
        self.assertIn("bewerbung@lumen-fictional.de", job.description)
        self.assertNotIn("<li>", job.description)
        self.assertEqual(minimal.company, "Display GmbH")
        self.assertFalse(minimal.homeoffice)
        self.assertEqual(fetcher.requests[0][1], {"mode": "json"})

    def test_smartrecruiters(self):
        fetcher = ATSFetcher()
        job, minimal = ats.smartrecruiters(fetcher, {"company": COMPANY, "company_name": "Display GmbH"})
        self.assertEqual(job.id, f"smartrecruiters:{COMPANY}:sr-1")
        self.assertEqual(job.source, "smartrecruiters")
        self.assertEqual(job.company, "Display GmbH")
        self.assertEqual(job.title, "Werkstudent Prozessautomatisierung")
        self.assertEqual(job.source_url, f"https://jobs.smartrecruiters.com/{COMPANY}/sr-1")
        self.assertEqual(job.apply_url, job.source_url + "/apply")
        self.assertEqual(job.location, "Hamburg, de")
        self.assertTrue(job.homeoffice)
        self.assertEqual(job.posted, "2026-10-15")
        for text in ("Ein fiktives Unternehmen", "Workflows automatisieren & Prozesse", "Laufendes Studium", "15–20"):
            self.assertIn(text, job.description)
        self.assertNotIn("<p>", job.description)
        self.assertFalse(minimal.homeoffice)
        self.assertNotIn(SOURCES["smartrecruiters"][1] + "/sr-2", fetcher.calls)

    def test_ashby(self):
        job, minimal = ats.ashby(ATSFetcher(), {"board": COMPANY, "company": "Display GmbH"})
        self.assertEqual(job.id, f"ashby:{COMPANY}:ab-1")  # jobUrl supplies the ID.
        self.assertEqual(job.source, "ashby")
        self.assertEqual(job.company, "Display GmbH")
        self.assertEqual(job.title, "Working Student Automation")
        self.assertEqual(job.source_url, f"https://jobs.ashbyhq.com/{COMPANY}/ab-1")
        self.assertEqual(job.apply_url, job.source_url + "/application")
        self.assertEqual(job.location, "Hamburg")
        self.assertTrue(job.homeoffice)
        self.assertEqual(job.posted, "2026-10-15")
        self.assertEqual(job.description, "Automate workflows & document processes.")
        self.assertFalse(minimal.homeoffice)

    def test_workable(self):
        fetcher = ATSFetcher()
        job, minimal = ats.workable(fetcher, {"subdomain": COMPANY, "company": "Display GmbH"})
        self.assertEqual(job.id, f"workable:{COMPANY}:WK1")
        self.assertEqual(job.source, "workable")
        self.assertEqual(job.company, "Display GmbH")
        self.assertEqual(job.title, "Werkstudent Digitalisierung")
        self.assertEqual(job.source_url, f"https://apply.workable.com/{COMPANY}/j/WK1/")
        self.assertEqual(job.apply_url, job.source_url + "apply/")
        self.assertEqual(job.location, "Hamburg, Hamburg, Germany")
        self.assertTrue(job.homeoffice)
        self.assertEqual(job.posted, "2026-10-15")
        self.assertEqual(job.description, "Prozesse analysieren & automatisieren.")
        self.assertFalse(minimal.homeoffice)
        self.assertEqual(fetcher.requests[0][1], {"details": "true"})

    def test_default_company_names(self):
        for kind, (entry, _) in SOURCES.items():
            with self.subTest(kind=kind):
                job = getattr(ats, kind)(ATSFetcher(), entry)[0]
                expected = "Lumen Fictional GmbH" if kind in ("smartrecruiters", "workable") else COMPANY
                self.assertEqual(job.company, expected)

    def test_missing_optional_fields_and_url_fallbacks(self):
        items = {
            "lever": [{"id": "1", "text": "Student"}],
            "smartrecruiters": {"content": [{"id": "1", "name": "Accountant"}], "totalFound": 1},
            "ashby": {"jobs": [{"title": "Student", "jobUrl": f"https://jobs.ashbyhq.com/{COMPANY}/1/"}]},
            "workable": {"jobs": [{"shortcode": "1", "title": "Student"}]},
        }
        for kind, (entry, url) in SOURCES.items():
            with self.subTest(kind=kind):
                job = getattr(ats, kind)(ATSFetcher({url: items[kind]}), entry)[0]
                self.assertEqual((job.location, job.description, job.posted), ("", "", ""))
                self.assertFalse(job.homeoffice)
                self.assertTrue(job.source_url.startswith("https://"))
                self.assertEqual(job.apply_url, job.source_url)

    def test_html_fallbacks_and_optional_dates(self):
        data = fixture("lever")
        data[0].pop("descriptionPlain")
        data[0]["description"] = "<p>Workflow &amp; KI</p>"
        data[0]["createdAt"] = 1792022400000
        url = SOURCES["lever"][1]
        job = ats.lever(ATSFetcher({url: data}), SOURCES["lever"][0])[0]
        self.assertIn("Workflow & KI", job.description)
        self.assertEqual(job.posted, "2026-10-15")
        data = fixture("ashby")
        data["jobs"][0].pop("descriptionPlain")
        job = ats.ashby(ATSFetcher({SOURCES["ashby"][1]: data}), SOURCES["ashby"][0])[0]
        self.assertEqual(job.description, "Automate workflows & document processes.")
        data = fixture("workable")
        data["jobs"][0].pop("published_on")
        data["jobs"][0]["created_at"] = "2026-10-12T12:00:00Z"
        job = ats.workable(ATSFetcher({SOURCES["workable"][1]: data}), SOURCES["workable"][0])[0]
        self.assertEqual(job.posted, "2026-10-12")

    def test_remote_and_hybrid_without_location(self):
        variants = {
            "lever": ("workplaceType", "hybrid"),
            "smartrecruiters": ("workplaceType", "remote"),
            "ashby": ("workplaceType", "Hybrid"),
            "workable": ("workplace_type", "hybrid"),
        }
        for kind, (entry, url) in SOURCES.items():
            with self.subTest(kind=kind):
                data = fixture(kind)
                item = data[0] if kind == "lever" else data["content" if kind == "smartrecruiters" else "jobs"][0]
                for key in ("location", "isRemote", "telecommuting", "city", "state", "country"):
                    item.pop(key, None)
                key, value = variants[kind]
                item[key] = value
                job = getattr(ats, kind)(ATSFetcher({url: data}), entry)[0]
                self.assertTrue(job.homeoffice)

    def test_remote_location_and_false_flag(self):
        data = fixture("lever")
        data[0]["workplaceType"] = "unspecified"
        data[0]["categories"]["location"] = "Germany (Remote)"
        job = ats.lever(ATSFetcher({SOURCES["lever"][1]: data}), SOURCES["lever"][0])[0]
        self.assertTrue(job.homeoffice)
        data = fixture("workable")
        data["jobs"][0]["telecommuting"] = False
        data["jobs"][0]["workplace_type"] = "on_site"
        job = ats.workable(ATSFetcher({SOURCES["workable"][1]: data}), SOURCES["workable"][0])[0]
        self.assertFalse(job.homeoffice)

    def test_404_and_unknown_board_each_provider(self):
        for kind, (entry, url) in SOURCES.items():
            for response in (None, FetchError(url, 404, "Not found"), {"error": "Unknown board"}):
                with self.subTest(kind=kind, response=type(response).__name__), self.assertLogs("jobpilot.ats"):
                    self.assertEqual(getattr(ats, kind)(ATSFetcher({url: response}), entry), [])

    def test_real_fetcher_404_contract_each_provider(self):
        for kind, (entry, _) in SOURCES.items():
            with self.subTest(kind=kind), patch.object(Fetcher, "get", return_value=(404, b"")), self.assertLogs("jobpilot.ats"):
                self.assertEqual(getattr(ats, kind)(Fetcher(), entry), [])

    def test_malformed_items_do_not_drop_valid_neighbors(self):
        for kind, (entry, url) in SOURCES.items():
            with self.subTest(kind=kind):
                data = fixture(kind)
                items = data if kind == "lever" else data["content" if kind == "smartrecruiters" else "jobs"]
                bad = copy.deepcopy(items[0])
                bad["id"] = "bad"
                if kind == "lever":
                    bad["categories"] = ["wrong"]
                elif kind == "smartrecruiters":
                    bad["location"] = ["wrong"]
                else:
                    bad["descriptionPlain" if kind == "ashby" else "description"] = {"wrong": "type"}
                items[1:1] = [None, "wrong", bad, {"id": None, "title": None}, {}]
                if kind == "smartrecruiters":
                    data["totalFound"] = len(items)
                fetcher = ATSFetcher({url: data})
                with self.assertLogs("jobpilot.ats", level="WARNING"):
                    jobs = getattr(ats, kind)(fetcher, entry)
                self.assertEqual(len(jobs), 2)
                self.assertNotIn("bad", {j.id.rsplit(":", 1)[-1] for j in jobs})

    def test_ats_mail_addresses_are_excluded(self):
        for domain in ("ashbyhq.com", "workable.com"):
            self.assertTrue(BAD_EMAIL.search("jobs@" + domain))
            self.assertTrue(any(ats_domain in domain for ats_domain in mailer.ATS_DOMAINS))


class SmartRecruitersBoundsTests(unittest.TestCase):
    entry, url = SOURCES["smartrecruiters"]

    @staticmethod
    def page(params, total=400, title="Senior Accountant", page_size=100):
        offset = params["offset"]
        return {"totalFound": total, "content": [
            {"id": str(i), "name": title} for i in range(offset, min(offset + page_size, total))
        ]}

    def test_pagination_cap_300(self):
        fetcher = ATSFetcher({self.url: self.page})
        jobs = ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual(len(jobs), 300)
        self.assertEqual([p for url, p in fetcher.requests if url == self.url], [
            {"limit": 100, "offset": 0}, {"limit": 100, "offset": 100}, {"limit": 100, "offset": 200},
        ])

    def test_stops_at_total_found_even_for_short_pages(self):
        fetcher = ATSFetcher({self.url: lambda p: self.page(p, total=3, page_size=2)})
        self.assertEqual(len(ats.smartrecruiters(fetcher, self.entry)), 3)
        self.assertEqual([p["offset"] for _, p in fetcher.requests], [0, 2])

    def test_empty_page_stops_when_total_is_missing(self):
        fetcher = ATSFetcher({self.url: lambda p: {"content": [{"id": "1", "name": "Accountant"}]} if not p["offset"] else {"content": []}})
        self.assertEqual(len(ats.smartrecruiters(fetcher, self.entry)), 1)
        self.assertEqual(len(fetcher.calls), 2)

    def test_detail_cap_60_across_pages_and_failed_calls(self):
        fetcher = ATSFetcher({self.url: lambda p: self.page(p, total=150, title="Werkstudent KI")})
        # Every attempted detail fails, but the per-company budget still applies.
        for i in range(60):
            fetcher.responses[f"{self.url}/{i}"] = FetchError(self.url, 503, "Unavailable")
        with self.assertLogs("jobpilot.ats", level="WARNING"):
            jobs = ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual(len(jobs), 150)
        self.assertEqual(len([url for url in fetcher.calls if url != self.url]), 60)

    def test_all_prefilter_terms_and_nonmatching_title(self):
        titles = ["Werkstudent", "WORKING STUDENT", "Student", "Praktikum", "Internship", "Trainee", "Senior Engineer"]
        fetcher = ATSFetcher({self.url: {"totalFound": 7, "content": [{"id": str(i), "name": t} for i, t in enumerate(titles)]}})
        ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual(fetcher.calls[1:], [f"{self.url}/{i}" for i in range(6)])

    def test_oversized_page_cannot_exceed_cap(self):
        fetcher = ATSFetcher({self.url: lambda p: self.page(p, page_size=400)})
        self.assertEqual(len(ats.smartrecruiters(fetcher, self.entry)), 300)
        self.assertEqual(len(fetcher.calls), 1)

    def test_detail_404_does_not_retain_stale_posting(self):
        fetcher = ATSFetcher()
        fetcher.responses[self.url + "/sr-1"] = None
        with self.assertLogs("jobpilot.ats"):
            jobs = ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual([j.id for j in jobs], [f"smartrecruiters:{COMPANY}:sr-2"])

    def test_duplicate_pages_cannot_repeat_detail_calls(self):
        fetcher = ATSFetcher({self.url: lambda p: {"totalFound": 3, "content": [{"id": "1", "name": "Student"}]}})
        jobs = ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(fetcher.calls.count(self.url + "/1"), 1)

    def test_inactive_detail_is_not_live(self):
        fetcher = ATSFetcher()
        fetcher.responses[self.url + "/sr-1"]["active"] = False
        jobs = ats.smartrecruiters(fetcher, self.entry)
        self.assertEqual([j.id for j in jobs], [f"smartrecruiters:{COMPANY}:sr-2"])


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.settings = make_project(smtp=False)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.settings.cfg["search"] = {kind: [entry] for kind, (entry, _) in SOURCES.items()}

    def test_collect_and_is_live_each_provider(self):
        fetcher = ATSFetcher()
        result = pipeline.RunResult()
        jobs, live_ids = pipeline.collect(self.settings, fetcher, result)
        self.assertEqual(len(jobs), 8)
        self.assertEqual(result.errors, [])
        for kind, (_, url) in SOURCES.items():
            with self.subTest(kind=kind):
                row = next({"id": j.id, "source": kind} for j in jobs if j.source == kind)
                self.assertIn(url, fetcher.calls)  # Evidence came from the official source in this run.
                self.assertTrue(pipeline.is_live(self.settings, fetcher, row, live_ids))
                removed = {**row, "id": f"{kind}:{COMPANY}:removed"}
                self.assertFalse(pipeline.is_live(self.settings, fetcher, removed, live_ids))
                self.assertFalse(pipeline.is_live(self.settings, fetcher, row, {}))

    def test_removed_and_404_boards_are_not_live_each_provider(self):
        for kind, (entry, url) in SOURCES.items():
            for empty in (None, [] if kind == "lever" else {"content" if kind == "smartrecruiters" else "jobs": []}):
                with self.subTest(kind=kind, response=empty):
                    self.settings.cfg["search"] = {kind: [entry]}
                    jobs, ids = pipeline.collect(self.settings, ATSFetcher({url: empty}), pipeline.RunResult())
                    self.assertEqual(jobs, [])
                    self.assertFalse(pipeline.is_live(self.settings, ATSFetcher(), {"id": f"{kind}:{COMPANY}:old", "source": kind}, ids))

    def test_collect_records_errors_and_continues(self):
        for kind, (_, url) in SOURCES.items():
            with self.subTest(kind=kind):
                fetcher = ATSFetcher()
                fetcher.responses[url] = RuntimeError("broken provider")
                result = pipeline.RunResult()
                jobs, ids = pipeline.collect(self.settings, fetcher, result)
                self.assertEqual(len(jobs), 6)
                self.assertEqual(len(result.errors), 1)
                self.assertIn(f"{kind}:{COMPANY}", result.errors[0])
                self.assertNotIn(f"{kind}:{COMPANY}", ids)

    def test_unavailable_source_defers_queue_instead_of_sending_or_closing(self):
        self.settings.cfg["run"]["report_email_to_self"] = False
        store = Store(self.settings.db_path)
        self.addCleanup(store.close)
        result = pipeline.RunResult()
        for kind, (entry, _) in SOURCES.items():
            job = getattr(ats, kind)(ATSFetcher(), entry)[0]
            store.insert(job, None, "queued", "test")
            store.update(job.id, "queued", "test", email=f"jobs@{kind}-fictional.de")
        with patch.object(mailer, "send_job", side_effect=AssertionError("must not send")):
            pipeline.send_queue(self.settings, store, ATSFetcher(), {}, result, sleep=lambda _: None)
        self.assertEqual(len(store.by_status("queued")), 4)
        self.assertEqual(len(result.errors), 4)
        self.assertEqual(result.closed, 0)

    def test_lever_werkstudent_reaches_queue_in_dry_run(self):
        self.settings.cfg["search"] = {"lever": [{"company": COMPANY}]}
        self.settings.cfg["run"].update({"send_mode": "dry_run", "check_inbox": False, "report_email_to_self": False})
        self.settings.cfg["llm"] = {"enabled": False}
        # Even if credentials are inherited, this run cannot send email or applications.
        with patch.object(mailer, "send_job", side_effect=AssertionError("must not send")), \
             patch.object(mailer, "send_self", side_effect=AssertionError("must not email")):
            result = pipeline.run(self.settings, fetcher=ATSFetcher(), today=date(2026, 10, 19))
        self.assertEqual(result.mode, "dry_run")
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.queued, 1)
        store = Store(self.settings.db_path)
        self.addCleanup(store.close)
        row = store.get(f"lever:{COMPANY}:lv-1")
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["email"], "bewerbung@lumen-fictional.de")
        self.assertIn("Werkstudent", row["subject"])
        self.assertTrue(row["body"])


if __name__ == "__main__":
    unittest.main()
