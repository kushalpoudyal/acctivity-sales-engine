import tempfile
import os
import unittest
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace as Record
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

import prospects
import openai
from firm_research import ResearchError
from test_prospects import PAGES, RECORDS
from web_research import cited_search_findings


class ParallelResearchTests(unittest.TestCase):
    def test_all_four_sources_run_concurrently_including_apollo(self):
        barrier = Barrier(4)

        def website(*args):
            barrier.wait(timeout=3)
            return RECORDS, PAGES, [], []

        def search(*args, **kwargs):
            barrier.wait(timeout=3)
            return []

        def enrich(domain):
            barrier.wait(timeout=3)
            return {"name": "Firm", "industries": ["law practice"]}

        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch.dict(os.environ, {"APOLLO_API_KEY": "test-key"}), \
                patch("prospects.crawl_research", side_effect=website), \
                patch("prospects.search_web", side_effect=search), \
                patch("prospects.apollo.enrich_org", side_effect=enrich) as organization, \
                patch("prospects.apollo.search_people", return_value={"people": [
                    {"id": "p1", "first_name": "Jane", "title": "Partner", "has_email": True}
                ]}) as people, patch("prospects.apollo.reveal") as reveal:
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions(apollo=True))
            detail = prospects.get_firm(firm["id"])
        organization.assert_called_once_with("firm.example")
        people.assert_called_once()
        reveal.assert_not_called()
        self.assertEqual(detail["apollo_org"]["name"], "Firm")
        source = detail["research_meta"]["sources"]["apollo"]
        self.assertEqual(source["status"], "completed")
        self.assertEqual(source["contact_search"]["candidates"][0]["apollo_id"], "p1")
        self.assertEqual(set(detail["research_meta"]["sources"]), {"website", "web_search", "reputation", "apollo"})

    def test_apollo_only_needs_no_openai_and_preserves_website_records(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "", "APOLLO_API_KEY": "test-key"}), \
                patch("prospects.apollo.enrich_org", return_value={"name": "Firm"}), \
                patch("prospects.apollo.search_people", return_value={"people": []}), \
                patch("prospects.crawl_research") as crawl, patch("prospects.search_web") as search:
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with prospects.database() as db:
                prospects.store_research(db, firm["id"], PAGES, RECORDS)
            tasks = BackgroundTasks()
            options = prospects.ResearchOptions(website=False, web_search=False, reputation=False, apollo=True)
            self.assertEqual(prospects.start_research(firm["id"], tasks, options)["status"], "researching")
            prospects.research(firm["id"], "firm.example", options)
            detail = prospects.get_firm(firm["id"])
            self.assertEqual(detail["status"], "ready")
            self.assertEqual(detail["records"]["people"][0]["full_name"], "Jane Smith")
            self.assertEqual(set(detail["research_meta"]["sources"]), {"apollo"})
            self.assertFalse(detail["research_meta"]["selection"]["website"])
            crawl.assert_not_called()
            search.assert_not_called()

    def test_disabled_apollo_makes_no_calls_and_empty_selection_is_rejected(self):
        with self.assertRaises(ValidationError):
            prospects.ResearchOptions(website=False, web_search=False, reputation=False, apollo=False)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", return_value=(RECORDS, PAGES, [], [])), \
                patch("prospects.apollo.enrich_org") as enrich, patch("prospects.apollo.search_people") as people:
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions(web_search=False, reputation=False))
            enrich.assert_not_called()
            people.assert_not_called()
            self.assertNotIn("apollo", prospects.get_firm(firm["id"])["research_meta"]["sources"])

    def test_missing_apollo_configuration_rejects_before_any_job_starts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with self.assertRaises(HTTPException) as unavailable:
                prospects.start_research(firm["id"], BackgroundTasks(), prospects.ResearchOptions(apollo=True))
            self.assertEqual(unavailable.exception.status_code, 503)
            self.assertEqual(prospects.get_firm(firm["id"])["status"], "new")

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
            return RECORDS, PAGES, [], []

        def search(domain, name, model, reputation=False, progress=None):
            barrier.wait(timeout=3)
            return [{"title": "Observation", "text": "Search observation",
                     "sources": [{"url": "https://directory.example/firm", "title": "Listing"}],
                     "source_kind": "reputation_search" if reputation else "web_search"}]

        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", side_effect=website), patch("prospects.search_web", side_effect=search):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions())
            detail = prospects.get_firm(firm["id"])
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(detail["records"]["people"][0]["full_name"], "Jane Smith")
        self.assertEqual(sorted(f["kind"] for f in detail["web_findings"]), ["reputation_search", "web_search"])
        self.assertTrue(next(f for f in detail["web_findings"] if f["kind"] == "reputation_search")
                        ["not_verified_review_sample"])
        self.assertEqual(set(detail["research_meta"]["sources"]), {"website", "web_search", "reputation"})
        self.assertTrue(all(source["status"] == "completed" for source in detail["research_meta"]["sources"].values()))

    def test_search_failure_keeps_successful_crawl_and_disabled_sources_make_no_calls(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", return_value=(RECORDS, PAGES, [], [])), \
                patch("prospects.search_web", side_effect=ResearchError("Search unavailable")) as search:
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with self.assertLogs(level="ERROR"):
                prospects.research(firm["id"], "firm.example",
                                   prospects.ResearchOptions(web_search=True, reputation=False))
            detail = prospects.get_firm(firm["id"])
            self.assertEqual(detail["status"], "needs_review")
            self.assertEqual(detail["records"]["facts"][0]["detail"], "A family law firm.")
            self.assertEqual(detail["web_findings"], [])
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

    def test_web_findings_are_approved_by_id_and_reach_the_snapshot(self):
        finding = {"title": "Observation", "text": "Listed in a legal directory.",
                   "sources": [{"url": "https://directory.example/firm", "title": "Listing"}], "source_kind": "web_search"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_research", return_value=(RECORDS, PAGES, [], [])), \
                patch("prospects.search_web", return_value=[finding]):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions(reputation=False))
            detail = prospects.get_firm(firm["id"])
            [web] = detail["web_findings"]
            approval = prospects.approve(firm["id"], prospects.Approval(
                brief_token=detail["brief_token"], web_findings=[web["id"]]))
            snapshot = next(p for p in prospects.get_firm(firm["id"])["proposals"] if p["id"] == approval["id"])["snapshot"]
            with self.assertRaises(prospects.HTTPException) as unknown:
                prospects.approve(firm["id"], prospects.Approval(brief_token=detail["brief_token"],
                                                                 web_findings=[web["id"] + 1]))
        self.assertEqual([w["text"] for w in snapshot["web"]], ["Listed in a legal directory."])
        self.assertEqual(unknown.exception.status_code, 422)
