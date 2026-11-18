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

ORG = {"name": "Firm Ltd", "estimated_num_employees": 42, "industries": ["legal services"],
       "technology_names": ["Microsoft Office 365", "Xero"]}


class FakeApollo:
    """Stands in for api.apollo.io: records requests, never touches the network."""

    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.requests = status, payload if payload is not None else {}, []

    def __enter__(self):
        transport = httpx.MockTransport(self.handle)
        self.patches = [patch.object(apollo, "_transport", transport),
                        patch.dict(os.environ, {"APOLLO_API_KEY": "test-key"})]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()

    def handle(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json=self.payload)

    def body(self, index=-1):
        return json.loads(self.requests[index].content)


class ClientTests(unittest.TestCase):
    def test_enrich_sends_key_header_and_domain(self):
        with FakeApollo(payload={"organization": ORG}) as fake:
            self.assertEqual(apollo.enrich_org("firm.example"), ORG)
        request = fake.requests[0]
        self.assertEqual(str(request.url), "https://api.apollo.io/api/v1/organizations/enrich?domain=firm.example")
        self.assertEqual(request.headers["x-api-key"], "test-key")

    def test_enrich_without_match_returns_none(self):
        with FakeApollo(payload={}):
            self.assertIsNone(apollo.enrich_org("firm.example"))

    def test_errors_are_clear_and_never_retried(self):
        for status, words, code in [(401, "rejected the API key", 502), (403, "refused", 502),
                                    (429, "rate limit", 429), (500, "HTTP 500", 502)]:
            with self.subTest(status=status), FakeApollo(status=status) as fake:
                with self.assertRaises(apollo.ApolloError) as error:
                    apollo.enrich_org("firm.example")
                self.assertIn(words, str(error.exception))
                self.assertNotIn("test-key", str(error.exception))
                self.assertEqual(error.exception.status_code, code)
                self.assertEqual(len(fake.requests), 1)

    def test_not_configured_makes_no_request(self):
        with FakeApollo() as fake, patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
            self.assertFalse(apollo.configured())
            with self.assertRaises(apollo.ApolloError) as error:
                apollo.enrich_org("firm.example")
        self.assertIn("Apollo not configured", str(error.exception))
        self.assertEqual(fake.requests, [])

    def test_search_people_payload(self):
        with FakeApollo(payload={"people": [{"id": "p1"}], "total_entries": 1}) as fake:
            result = apollo.search_people("firm.example", ["Partner"])
        self.assertEqual(result["people"], [{"id": "p1"}])
        self.assertEqual(fake.requests[0].url.path, "/api/v1/mixed_people/api_search")
        self.assertEqual(fake.body(), {"q_organization_domains_list": ["firm.example"], "page": 1,
                                       "per_page": 100, "person_titles": ["Partner"]})

    def test_reveal_asks_for_work_emails_only_and_caps_at_ten(self):
        with FakeApollo(payload={"matches": [{"id": "p1"}, None]}) as fake:
            self.assertEqual(apollo.reveal(["p1", "p2", "p1"]), [{"id": "p1"}])
            self.assertEqual(fake.requests[0].url.path, "/api/v1/people/bulk_match")
            self.assertEqual(fake.body(), {"details": [{"id": "p1"}, {"id": "p2"}],
                                           "reveal_personal_emails": False, "reveal_phone_number": False})
            for ids in ([], [str(n) for n in range(11)]):
                with self.assertRaises(apollo.ApolloError):
                    apollo.reveal(ids)
            self.assertEqual(len(fake.requests), 1)

    def test_search_orgs_merges_accounts_and_pages(self):
        with FakeApollo(payload={"organizations": [{"id": "o1"}], "accounts": [{"id": "a1"}],
                                 "pagination": {"page": 2}}) as fake:
            result = apollo.search_orgs({"q_organization_keyword_tags": ["law firm"]}, page=2)
        self.assertEqual([o["id"] for o in result["organizations"]], ["o1", "a1"])
        self.assertEqual(fake.requests[0].url.path, "/api/v1/mixed_companies/search")
        self.assertEqual(fake.body(), {"per_page": 100, "q_organization_keyword_tags": ["law firm"], "page": 2})


class EnrichEndpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(prospects, "DB_PATH", Path(folder.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def test_migration_adds_apollo_columns_to_existing_table(self):
        with sqlite3.connect(prospects.DB_PATH) as db:
            db.execute("""CREATE TABLE firms (id INTEGER PRIMARY KEY, domain TEXT UNIQUE NOT NULL,
                          name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
                          brief TEXT NOT NULL DEFAULT '[]', error TEXT, researched_at TEXT)""")
            db.execute("INSERT INTO firms(domain,name) VALUES('old.example','Old')")
        db.close()
        prospects.initialize()
        with FakeApollo():
            firm = prospects.get_firm(1)
        self.assertIsNone(firm["apollo_org"])
        self.assertIsNone(firm["apollo_enriched_at"])
        self.assertTrue(firm["apollo_configured"])

    def test_enrich_stores_org_and_refresh_must_be_explicit(self):
        prospects.initialize()
        firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        with FakeApollo(payload={"organization": ORG}) as fake:
            detail = prospects.apollo_enrich(firm["id"])
            self.assertEqual(detail["apollo_org"], ORG)
            self.assertTrue(detail["apollo_enriched_at"])
            with self.assertRaises(HTTPException) as again:
                prospects.apollo_enrich(firm["id"], prospects.ApolloEnrich())
            self.assertEqual(again.exception.status_code, 409)
            self.assertIn("refresh", again.exception.detail)
            self.assertEqual(len(fake.requests), 1)
            prospects.apollo_enrich(firm["id"], prospects.ApolloEnrich(refresh=True))
            self.assertEqual(len(fake.requests), 2)

    def test_http_route_accepts_empty_body_and_refresh_flag(self):
        prospects.initialize()
        firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        app = FastAPI()
        app.include_router(prospects.router)
        url = f"/api/v1/firms/{firm['id']}/apollo/enrich"
        with FakeApollo(payload={"organization": ORG}) as fake, TestClient(app) as client:
            self.assertEqual(client.post(url).status_code, 200)
            self.assertEqual(client.post(url, json={}).status_code, 409)
            response = client.post(url, json={"refresh": True})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("test-key", response.text)
            self.assertEqual(client.get(f"/api/v1/firms/{firm['id']}").json()["apollo_org"], ORG)
        self.assertEqual(len(fake.requests), 2)

    def test_not_configured_is_reported_and_spends_nothing(self):
        prospects.initialize()
        firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        with FakeApollo() as fake, patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
            self.assertFalse(prospects.get_firm(firm["id"])["apollo_configured"])
            with self.assertRaises(HTTPException) as error:
                prospects.apollo_enrich(firm["id"])
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("Apollo not configured", error.exception.detail)
        self.assertEqual(fake.requests, [])

    def test_apollo_errors_and_no_match_leave_firm_unenriched(self):
        prospects.initialize()
        firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        for status, payload, code in [(429, {}, 429), (403, {}, 502), (200, {}, 404)]:
            with self.subTest(status=status), FakeApollo(status=status, payload=payload):
                with self.assertRaises(HTTPException) as error:
                    prospects.apollo_enrich(firm["id"])
                self.assertEqual(error.exception.status_code, code)
                self.assertIsNone(prospects.get_firm(firm["id"])["apollo_org"])
