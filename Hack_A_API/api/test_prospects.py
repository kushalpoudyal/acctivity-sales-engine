import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import patch

from fastapi import HTTPException

import prospects


class DirectoryTests(unittest.TestCase):
    def test_domain_deduplication_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="https://www.firm.example/about"))
            prospects.initialize()
            self.assertEqual(prospects.list_firms()[0]["id"], firm["id"])
            with self.assertRaises(HTTPException) as duplicate:
                prospects.add_firm(prospects.Intake(website="firm.example"))
            self.assertEqual(duplicate.exception.status_code, 409)

    def test_only_first_party_cited_lines_are_saved(self):
        part = Record(type="output_text", text="Offices\nGuess\nOther",
                      annotations=[Record(type="url_citation", start_index=1,
                                          url="https://firm.example/about", title="Team"),
                                   Record(type="url_citation", start_index=15,
                                          url="https://notfirm.example/", title="Other")])
        response = Record(status="completed", output=[
            Record(type="web_search_call", status="completed"),
            Record(type="message", content=[part])])
        result = prospects.cited_passages(response, "firm.example")
        self.assertEqual(result, [{"text": "Offices", "sources": [
            {"url": "https://firm.example/about", "title": "Team"}]}])

    def test_research_persists_staff_and_export_uses_only_approved_people(self):
        person = {"full_name": "Jane Smith", "position": "Partner", "public_work_email": None,
                  "public_work_phone": None, "biography": "Family lawyer", "qualifications": [],
                  "practice_areas": ["Family law"]}
        findings = [{"text": "Jane Smith is a Partner", "person": person,
                     "sources": [{"url": "https://firm.example/team"}], "evidence": "Jane Smith is a Partner"},
                    {"text": "Other finding", "sources": [], "evidence": "Other"}]
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ), patch("prospects.crawl_site", return_value=(
            [{"url": "https://firm.example/team", "title": "Team", "text": "Jane Smith is a Partner"}], []
        )), patch("prospects.extract_profile", return_value=(findings, 0)), patch("prospects.openai.OpenAI"):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            prospects.research(firm["id"], "firm.example", prospects.ResearchOptions(web_search=False, reputation=False))
            detail = prospects.get_firm(firm["id"])
            self.assertEqual(detail["staff"][0]["position"], "Partner")
            approval = prospects.approve(firm["id"], prospects.Approval(
                brief_token=detail["brief_token"], findings=[0]))
            export = json.loads(prospects.demo_export(firm["id"], approval["id"]).body)
            self.assertEqual([p["full_name"] for p in export["staff"]], ["Jane Smith"])
            self.assertEqual(len(export["findings"]), 1)
            self.assertFalse(export["send_invitations"])
            excluded = prospects.approve(firm["id"], prospects.Approval(
                brief_token=detail["brief_token"], findings=[1]))
            self.assertEqual(json.loads(prospects.demo_export(firm["id"], excluded["id"]).body)["staff"], [])

    def test_rep_edits_update_staff_but_preserve_sources_and_approved_snapshot(self):
        person = {"full_name": "Jane Smith", "position": "Partner", "public_work_email": None,
                  "public_work_phone": None, "biography": None, "qualifications": [], "practice_areas": []}
        finding = {"title": "Jane", "text": "Jane is a Partner", "person": person,
                   "sources": [{"url": "https://firm.example/jane", "title": "Jane"}],
                   "evidence": "Jane is a Partner"}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            with prospects.database() as db:
                db.execute("UPDATE firms SET brief=?,status='ready' WHERE id=?",
                           (json.dumps([finding]), firm["id"]))
            before = prospects.get_firm(firm["id"])
            approval = prospects.approve(firm["id"], prospects.Approval(
                brief_token=before["brief_token"], findings=[0]))
            edited_person = {**person, "position": "Managing Partner"}
            edited = prospects.edit_brief(firm["id"], prospects.BriefEdit(
                brief_token=before["brief_token"], display_name="Test Legal",
                findings=[prospects.FindingEdit(index=0, title="Jane",
                          text="Jane is Managing Partner", person=edited_person)]))
            self.assertEqual(edited["name"], "Test Legal")
            self.assertEqual(edited["staff"][0]["position"], "Managing Partner")
            self.assertEqual(edited["brief"][0]["sources"], finding["sources"])
            self.assertEqual(edited["brief"][0]["original"]["person"]["position"], "Partner")
            self.assertTrue(edited["brief"][0]["rep_edited"])
            self.assertEqual(edited["proposals"][0]["snapshot"]["findings"][0]["person"]["position"], "Partner")
            with self.assertRaises(HTTPException) as stale:
                prospects.approve(firm["id"], prospects.Approval(
                    brief_token=before["brief_token"], findings=[0]))
            self.assertEqual(stale.exception.status_code, 409)
            self.assertEqual(prospects.demo_export(firm["id"], approval["id"]).status_code, 200)
