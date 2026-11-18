import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import apollo
import prospects
from test_apollo import FakeApollo

# Obviously fake people and addresses only: revealed emails are personal data.
JANE = {"id": "ap-jane", "name": "Jane Smith", "first_name": "Jane", "last_name": "Smith", "title": "Director",
        "email": "jane@firm.example", "email_status": "verified", "linkedin_url": "https://linkedin.example/in/jane",
        "match_confidence": "high"}
PERSON = {"full_name": "Jane Smith", "position": "Partner", "public_work_email": None, "public_work_phone": None,
          "biography": "Family lawyer", "qualifications": [], "practice_areas": ["Family law"]}


class ScriptedApollo(FakeApollo):
    """FakeApollo that answers each request with the next (status, payload) in `replies`."""

    def __init__(self, *replies):
        super().__init__()
        self.replies = list(replies)

    def handle(self, request):
        self.requests.append(request)
        status, payload = self.replies.pop(0) if self.replies else (200, {})
        return httpx.Response(status, json=payload)


PAGE = {"id": 1, "url": "https://firm.example/team", "title": "Team", "text": "team"}


def records_with(*people):
    """Website research records naming these people on one page, with Family law as the practice area."""
    return {"facts": [], "offices": [],
            "practice_areas": [{"name": "Family law", "description": "Family law advice.", "services": [],
                                "page_id": 1, "evidence": "team"}],
            "people": [{**p, "page_id": 1, "evidence": f"{p['full_name']} works here"} for p in people]}


class ContactTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(prospects, "DB_PATH", Path(folder.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def firm(self):
        prospects.initialize()
        return prospects.add_firm(prospects.Intake(website="firm.example"))["id"]

    def research(self, firm_id, *people):
        with patch("prospects.crawl_site", return_value=([PAGE], [])), \
                patch("prospects.extract_profile", return_value=(records_with(*people), 0)), \
                patch("prospects.openai.OpenAI"):
            prospects.research(firm_id, "firm.example",
                               prospects.ResearchOptions(web_search=False, reputation=False))

    def contacts(self, firm_id):
        return prospects.get_firm(firm_id)["apollo_contacts"]

    def people(self, firm_id):
        return prospects.get_firm(firm_id)["records"]["people"]

    def reveal(self, firm_id, *matches):
        with ScriptedApollo((200, {"matches": list(matches)})):
            return prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=[m["id"] for m in matches]))

    def test_contacts_revealed_into_the_earlier_staff_table_are_migrated(self):
        firm_id = self.firm()
        with prospects.database() as db:
            db.execute("""CREATE TABLE staff (id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL,
                          full_name TEXT NOT NULL, position TEXT, profile TEXT NOT NULL, source_url TEXT NOT NULL,
                          evidence TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'website', apollo_id TEXT,
                          linkedin_url TEXT, email_status TEXT, match_confidence TEXT, apollo_email TEXT,
                          apollo_revealed_at TEXT)""")
            db.execute("""INSERT INTO staff(firm_id,full_name,profile,source_url,evidence)
                          VALUES(?,'Website Only','{}','https://firm.example','x')""", (firm_id,))
            db.execute("""INSERT INTO staff(firm_id,full_name,position,profile,source_url,evidence,source,apollo_id,
                          apollo_email,email_status,apollo_revealed_at)
                          VALUES(?,'Jane Smith','Director','{}','','','apollo','ap-jane','jane@firm.example',
                          'verified','2026-11-18 09:30:00')""", (firm_id,))
        prospects.initialize()
        prospects.initialize()
        [jane] = self.contacts(firm_id)
        self.assertEqual((jane["apollo_id"], jane["full_name"], jane["apollo_email"], jane["apollo_revealed_at"]),
                         ("ap-jane", "Jane Smith", "jane@firm.example", "2026-11-18 09:30:00"))
    def test_candidates_by_title_mark_already_revealed_people(self):
        firm_id = self.firm()
        self.reveal(firm_id, JANE)
        people = [{"id": "ap-jane", "first_name": "Jane", "last_name_obfuscated": "Sm***h", "title": "Director",
                   "has_email": True}, {"id": "ap-tom", "first_name": "Tom", "title": "Partner", "has_email": False}]
        with ScriptedApollo((200, {"people": people, "total_entries": 2})) as fake:
            result = prospects.apollo_candidates(firm_id, titles=None, seniorities=None)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.body()["person_titles"], prospects.DEFAULT_TITLES)
        self.assertNotIn("person_seniorities", fake.body())
        self.assertEqual((result["search"], result["fallback"], result["credits"]), ("titles", False, 0))
        self.assertEqual([(c["apollo_id"], c["revealed"]) for c in result["candidates"]],
                         [("ap-jane", True), ("ap-tom", False)])
        self.assertNotIn("jane@firm.example", json.dumps(result))

    def test_empty_title_search_falls_back_to_seniorities(self):
        firm_id = self.firm()
        with ScriptedApollo((200, {"people": [], "total_entries": 0}),
                            (200, {"people": [{"id": "ap-1", "first_name": "Ann"}], "total_entries": 1})) as fake:
            result = prospects.apollo_candidates(firm_id, titles=["Partner"], seniorities=None)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.body(0)["person_titles"], ["Partner"])
        self.assertEqual(fake.body(1)["person_seniorities"], prospects.FALLBACK_SENIORITIES)
        self.assertNotIn("person_titles", fake.body(1))
        self.assertEqual((result["search"], result["fallback"]), ("seniorities", True))
        self.assertEqual(result["seniorities"], prospects.FALLBACK_SENIORITIES)
        self.assertEqual([c["apollo_id"] for c in result["candidates"]], ["ap-1"])

    def test_explicit_seniorities_or_no_fallback_search_once(self):
        firm_id = self.firm()
        for kwargs, search in [({"titles": None, "seniorities": ["head,director"]}, "seniorities"),
                               ({"titles": ["CFO"], "seniorities": None, "fallback": False}, "titles")]:
            with self.subTest(**kwargs), ScriptedApollo((200, {"people": []})) as fake:
                result = prospects.apollo_candidates(firm_id, **kwargs)
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual((result["search"], result["fallback"], result["candidates"]), (search, False, []))
        self.assertEqual(fake.body()["person_titles"], ["CFO"])

    def test_candidates_http_route_parses_query_lists(self):
        firm_id = self.firm()
        app = FastAPI()
        app.include_router(prospects.router)
        with ScriptedApollo((200, {"people": [{"id": "ap-1"}]})) as fake, TestClient(app) as client:
            response = client.get(f"/api/v1/firms/{firm_id}/apollo/candidates",
                                  params=[("titles", "Partner, Practice Manager"), ("titles", "CFO")])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.body()["person_titles"], ["Partner", "Practice Manager", "CFO"])
        self.assertNotIn("test-key", response.text)

    def test_reveal_batches_of_ten_and_stores_apollo_contacts(self):
        firm_id = self.firm()
        ids = [f"ap-{n}" for n in range(12)]
        matches = [{"id": i, "name": f"Person {i}", "email": f"{i}@firm.example", "email_status": "verified"}
                   for i in ids]
        with ScriptedApollo((200, {"matches": matches[:10]}), (200, {"matches": matches[10:11] + [None]})) as fake:
            result = prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=ids))
        self.assertEqual([len(fake.body(n)["details"]) for n in range(2)], [10, 2])
        self.assertFalse(fake.body(0)["reveal_personal_emails"])
        self.assertFalse(fake.body(0)["reveal_phone_number"])
        self.assertEqual((result["requested"], result["matched"], result["with_email"]), (12, 11, 11))
        self.assertEqual(result["unmatched_ids"], ["ap-11"])
        contacts = self.contacts(firm_id)
        self.assertEqual(len(contacts), 11)
        self.assertTrue(all(c["apollo_email"] and c["person_id"] is None for c in contacts))

    def test_failed_batch_keeps_earlier_paid_batches(self):
        firm_id = self.firm()
        ids = [f"ap-{n}" for n in range(11)]
        with ScriptedApollo((200, {"matches": [{"id": "ap-0", "name": "Ann Example", "email": "ann@firm.example"}]}),
                            (429, {})):
            with self.assertRaises(HTTPException) as error:
                prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=ids))
        self.assertEqual(error.exception.status_code, 429)
        self.assertIn("1 people from earlier batches were saved", error.exception.detail)
        self.assertEqual([c["full_name"] for c in self.contacts(firm_id)], ["Ann Example"])

    def test_double_reveal_is_refused_without_a_request(self):
        firm_id = self.firm()
        self.reveal(firm_id, JANE)
        with ScriptedApollo() as fake:
            with self.assertRaises(HTTPException) as error:
                prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=["ap-new", "ap-jane"]))
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("Jane Smith", error.exception.detail)
        self.assertEqual(fake.requests, [])

    def test_reveal_links_to_the_website_person_case_insensitively(self):
        firm_id = self.firm()
        self.research(firm_id, PERSON)
        self.reveal(firm_id, {**JANE, "name": "JANE SMITH"})
        [contact], [person] = self.contacts(firm_id), self.people(firm_id)
        self.assertEqual(contact["person_id"], person["id"])
        self.assertEqual((contact["apollo_email"], contact["linkedin_url"], contact["match_confidence"]),
                         ("jane@firm.example", JANE["linkedin_url"], "high"))
        self.assertEqual((person["full_name"], person["position"], person["biography"]),
                         ("Jane Smith", "Partner", "Family lawyer"))
    def test_research_links_to_an_earlier_contact_case_insensitively(self):
        firm_id = self.firm()
        self.reveal(firm_id, JANE)
        self.research(firm_id, {**PERSON, "full_name": "jane smith"})
        [contact], [person] = self.contacts(firm_id), self.people(firm_id)
        self.assertEqual((contact["person_id"], contact["apollo_email"]), (person["id"], "jane@firm.example"))
        self.assertEqual((person["position"], person["biography"]), ("Partner", "Family lawyer"))
    def test_re_research_keeps_apollo_contacts_and_relinks_them(self):
        firm_id = self.firm()
        tom = {**PERSON, "full_name": "Tom Old", "biography": "Left the firm"}
        self.research(firm_id, PERSON, tom)
        self.reveal(firm_id, JANE, {"id": "ap-ann", "name": "Ann Example", "email": "ann@firm.example"})
        self.research(firm_id, {**PERSON, "full_name": "Kim New"})
        self.assertEqual([p["full_name"] for p in self.people(firm_id)], ["Kim New"])
        contacts = {c["full_name"]: c for c in self.contacts(firm_id)}
        self.assertEqual(sorted(contacts), ["Ann Example", "Jane Smith"])
        self.assertEqual((contacts["Jane Smith"]["apollo_email"], contacts["Jane Smith"]["person_id"]),
                         ("jane@firm.example", None))
    def test_not_configured_spends_nothing(self):
        firm_id = self.firm()
        with ScriptedApollo() as fake, patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
            for call in (lambda: prospects.apollo_candidates(firm_id, titles=None, seniorities=None),
                         lambda: prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=["ap-1"]))):
                with self.assertRaises(HTTPException) as error:
                    call()
                self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
