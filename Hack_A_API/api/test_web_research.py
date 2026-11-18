import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace as Record
from unittest.mock import patch

import prospects
import openai
from firm_research import ResearchError
from web_research import cited_search_findings


class ParallelResearchTests(unittest.TestCase):
    def test_search_citations_are_saved_with_reputation_label(self):
        response = Record(status="completed", output=[
            Record(type="web_search_call", status="completed"),
            Record(type="message", content=[Record(
                type="output_text", text="Clients mention communication. [1]\nUnsupported claim\n",
                annotations=[Record(type="url_citation", start_index=31, end_index=34,
                                    url="https://directory.example/firm", title="Public listing")])])])
        findings = cited_search_findings(response, reputation=True)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["source_kind"], "reputation_search")
        self.assertTrue(findings[0]["not_verified_review_sample"])
        self.assertEqual(findings[0]["sources"][0]["title"], "Public listing")
        self.assertNotIn("Unsupported", findings[0]["text"])

    def test_incomplete_search_is_not_a_success(self):
        with self.assertRaises(ResearchError):
            cited_search_findings(Record(status="incomplete", output=[]), False)

    def test_three_sources_actually_run_concurrently(self):
        barrier = Barrier(3)

        def website(*args):
            barrier.wait(timeout=3)
            return ([{"text": "Firm fact", "sources": [], "source_kind": "website_crawl"}],
                    [{"url": "https://firm.example", "title": "Firm"}], [], [])

        def search(domain, name, model, reputation=False):
            barrier.wait(timeout=3)
            return [{"text": "Search observation", "sources": [],
                     "source_kind": "reputation_search" if reputation else "web_search"}]

        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", side_effect=website), patch("prospects.search_web", side_effect=search):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions())
            detail = prospects.get_firm(firm["id"])
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(len(detail["brief"]), 3)
        self.assertEqual(set(detail["research_meta"]["sources"]), {"website", "web_search", "reputation"})
        self.assertTrue(all(source["status"] == "completed" for source in detail["research_meta"]["sources"].values()))

    def test_search_failure_keeps_successful_crawl_and_disabled_sources_make_no_calls(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", return_value=(
            [{"text": "Website fact", "sources": []}], [], [], []
        )), patch("prospects.search_web", side_effect=ResearchError("Search unavailable")) as search:
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with self.assertLogs(level="ERROR"):
                prospects.research(firm["id"], "firm.example",
                                   prospects.ResearchOptions(web_search=True, reputation=False))
            detail = prospects.get_firm(firm["id"])
            self.assertEqual(detail["status"], "needs_review")
            self.assertEqual(detail["brief"][0]["text"], "Website fact")
            self.assertEqual(detail["research_meta"]["sources"]["web_search"]["status"], "failed")
            search.reset_mock()
            prospects.research(firm["id"], "firm.example",
                               prospects.ResearchOptions(web_search=False, reputation=False))
            search.assert_not_called()

    def test_provider_timeout_is_distinct_and_has_terminal_timestamps(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", side_effect=openai.APITimeoutError(request=Record())):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with self.assertLogs(level="ERROR"):
                prospects.research(firm["id"], "firm.example",
                                   prospects.ResearchOptions(web_search=False, reputation=False))
            detail = prospects.get_firm(firm["id"])
        meta = detail["research_meta"]
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(meta["sources"]["website"]["error_code"], "provider_timeout")
        self.assertIn("timed out", meta["sources"]["website"]["error"])
        self.assertIn("finished_at", meta)
        self.assertIn("finished_at", meta["sources"]["website"])
