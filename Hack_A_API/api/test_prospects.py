import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import patch

from fastapi import HTTPException

import prospects
from firm_research import RECORD_KINDS


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


PAGES = [{"id": 1, "url": "https://firm.example/team", "title": "Team",
          "text": "Jane Smith is a Partner in Family law."}]
RECORDS = {
    "facts": [{"kind": "overview", "title": "Family firm", "detail": "A family law firm.",
               "page_id": 1, "evidence": "Jane Smith is a Partner"}],
    "practice_areas": [{"name": name, "description": "Separation and parenting.", "services": ["Parenting"],
                        "page_id": 1, "evidence": "Partner in Family law"} for name in ("Family law", "family law")],
    "people": [{"full_name": "Jane Smith", "position": "Partner", "biography": None, "qualifications": [],
                "practice_areas": ["FAMILY LAW", "Tax"], "public_work_email": None, "public_work_phone": None,
                "page_id": 1, "evidence": "Jane Smith is a Partner"}],
    "offices": [{"name": "Gosford", "address": None, "phone": None, "email": None,
                 "page_id": 1, "evidence": "Jane Smith is a Partner"}],
}


class ResearchRecordTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prospects, "DB_PATH", Path(folder) / "test.sqlite3"))
        prospects.initialize()
        self.firm = prospects.add_firm(prospects.Intake(website="firm.example"))

    def research(self, **kwargs):
        with patch("prospects.crawl_site", return_value=(PAGES, [])) as crawl, patch(
            "prospects.extract_profile", return_value=(RECORDS, 0)
        ) as extract, patch("prospects.openai.OpenAI"):
            prospects.research(self.firm["id"], "firm.example", **kwargs)
        return crawl, extract

    def test_research_is_stored_as_linked_records_and_approval_freezes_them(self):
        self.research()
        detail = prospects.get_firm(self.firm["id"])
        records = detail["records"]
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(len(records["practice_areas"]), 1)
        jane = records["people"][0]
        self.assertEqual(jane["practice_area_ids"], [records["practice_areas"][0]["id"]])
        self.assertEqual(jane["source"]["url"], "https://firm.example/team")
        self.assertEqual(records["practice_areas"][0]["services"], ["Parenting"])
        approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=detail["brief_token"], people=[jane["id"]], practice_areas=[records["practice_areas"][0]["id"]]))
        snapshot = prospects.get_firm(self.firm["id"])["proposals"][0]["snapshot"]
        self.assertEqual(snapshot["people"][0]["practice_areas"], ["Family law"])
        self.assertEqual((snapshot["facts"], snapshot["people"][0]["rep_edited"]), ([], False))
        export = json.loads(prospects.demo_export(self.firm["id"], approval["id"]).body)
        self.assertEqual([p["full_name"] for p in export["staff"]], ["Jane Smith"])
        self.assertFalse(export["send_invitations"])
        with self.assertRaises(HTTPException) as empty:
            prospects.approve(self.firm["id"], prospects.Approval(brief_token=detail["brief_token"]))
        self.assertEqual(empty.exception.status_code, 422)

    def test_rep_edits_keep_original_values_and_leave_approvals_unchanged(self):
        self.research()
        before = prospects.get_firm(self.firm["id"])
        approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=before["brief_token"], people=[p["id"] for p in before["records"]["people"]]))
        body = {kind: before["records"][kind] for kind in RECORD_KINDS}
        body["people"] = [{**body["people"][0], "position": "Managing Partner", "practice_area_ids": []}]
        edited = prospects.edit_brief(self.firm["id"], prospects.BriefEdit(
            brief_token=before["brief_token"], display_name="Test Legal", **body))
        jane = edited["records"]["people"][0]
        self.assertEqual((edited["name"], jane["position"], jane["practice_area_ids"]),
                         ("Test Legal", "Managing Partner", []))
        self.assertEqual(jane["original"]["position"], "Partner")
        self.assertIsNone(edited["records"]["offices"][0]["original"])
        self.assertEqual(edited["proposals"][0]["snapshot"]["people"][0]["position"], "Partner")
        with self.assertRaises(HTTPException) as stale:
            prospects.approve(self.firm["id"], prospects.Approval(brief_token=before["brief_token"], people=[jane["id"]]))
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(prospects.demo_export(self.firm["id"], approval["id"]).status_code, 200)

    def test_reextraction_reuses_saved_pages_and_keeps_logos(self):
        self.research()
        with prospects.database() as db:
            db.execute("INSERT INTO brand_assets(firm_id,source_url,mime_type,content) VALUES(?,?,?,?)",
                       (self.firm["id"], "https://firm.example/logo.png", "image/png", b"png"))
        crawl, extract = self.research(reuse_pages=True)
        crawl.assert_not_called()
        self.assertEqual(extract.call_args.args[2], PAGES)
        detail = prospects.get_firm(self.firm["id"])
        self.assertEqual((detail["status"], detail["pages_saved"], len(detail["brand_assets"])), ("ready", 1, 1))

    def test_pages_saved_by_earlier_versions_move_to_the_pages_table(self):
        with prospects.database() as db:
            db.execute("UPDATE firms SET research_pages=? WHERE id=?", (json.dumps(
                [{"id": 1, "url": "https://firm.example/", "title": "Home", "text": "Welcome", "logos": []}]),
                self.firm["id"]))
        prospects.initialize()
        prospects.initialize()
        self.assertEqual(prospects.get_firm(self.firm["id"])["pages_saved"], 1)
