import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import find_firms
import prospects
from test_apollo import FakeApollo

# Obviously fake organizations; shaped like Apollo firm-search results (no headcount/industry/location).
PAGE = {
    "organizations": [
        {"id": "o1", "name": "Example Lawyers", "primary_domain": "lawyers.example",
         "website_url": "http://www.lawyers.example", "phone": "+61 0 0000 0000",
         "naics_codes": ["54111"], "sic_codes": ["8111"], "founded_year": 1999,
         "organization_revenue_printed": "1M"},
        {"id": "o2", "name": "Legal Talent Partners", "primary_domain": "legaltalent.example",
         "naics_codes": ["54111"]},
        {"id": "o3", "name": "Example Lawyers (duplicate)", "primary_domain": "www.lawyers.example"},
        {"id": "o4", "name": "Already Saved Legal", "primary_domain": "saved.example", "naics_codes": ["56131"]},
        {"id": "o5", "name": "No Website Legal"},
    ],
    "accounts": [{"id": "a1", "name": "Acme Recruitment", "website_url": "https://acme-recruitment.example/",
                  "primary_phone": {"number": "+64 0 000 0000"}}],
    "pagination": {"page": 1, "per_page": 100, "total_entries": 11900, "total_pages": 119},
}


class FindFirmsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_patch = patch.object(prospects, "DB_PATH", Path(folder.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        prospects.initialize()
        prospects.add_firm(prospects.Intake(website="https://saved.example"))
        app = FastAPI()
        app.include_router(prospects.router)
        app.include_router(find_firms.router)
        self.client = TestClient(app)
        self.url = "/api/v1/apollo/firm-search"

    def test_defaults_are_free_and_report_configuration(self):
        with FakeApollo() as fake:
            data = self.client.get(self.url).json()
            with patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
                self.assertFalse(self.client.get(self.url).json()["configured"])
        self.assertTrue(data["configured"])
        self.assertEqual(data["defaults"], {"keywords": ["law firm", "legal", "lawyers"], "naics": ["5411"],
                                            "locations": ["New Zealand", "Australia"], "employees": ["1,19", "20,199"]})
        self.assertEqual(fake.requests, [])

    def test_one_page_per_call_with_default_filters(self):
        with FakeApollo(payload=PAGE) as fake:
            response = self.client.post(self.url)
            self.assertEqual(response.status_code, 200)
            self.client.post(self.url, json={"page": 2})
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[0].url.path, "/api/v1/mixed_companies/search")
        self.assertEqual(fake.body(0), {"per_page": 100, "q_organization_keyword_tags": ["law firm", "legal", "lawyers"],
                                        "organization_locations": ["New Zealand", "Australia"],
                                        "organization_num_employees_ranges": ["1,19", "20,199"], "page": 1})
        self.assertEqual(fake.body(1)["page"], 2)
        data = response.json()
        self.assertEqual((data["page"], data["total_entries"], data["total_pages"], data["credits"]), (1, 11900, 119, 1))
        self.assertEqual(data["missing_fields"], ["employee count", "industry", "location"])
        self.assertNotIn("test-key", response.text)

    def test_review_rows_dedupe_and_flag(self):
        with FakeApollo(payload=PAGE):
            rows = {r["apollo_id"]: r for r in self.client.post(self.url).json()["results"]}
        self.assertNotIn("o3", rows)  # same domain as o1
        self.assertEqual(list(rows), ["o1", "o2", "o4", "o5", "a1"])
        law = rows["o1"]
        self.assertEqual((law["domain"], law["phone"], law["revenue"], law["founded_year"]),
                         ("lawyers.example", "+61 0 0000 0000", "1M", 1999))
        self.assertEqual((law["already_added"], law["likely_recruiter"], law["naics_match"]), (False, False, True))
        self.assertTrue(rows["o2"]["likely_recruiter"])
        self.assertTrue(rows["a1"]["likely_recruiter"])
        self.assertEqual((rows["a1"]["domain"], rows["a1"]["phone"]), ("acme-recruitment.example", "+64 0 000 0000"))
        self.assertTrue(rows["o4"]["already_added"])
        self.assertFalse(rows["o4"]["naics_match"])
        self.assertIsNone(rows["o5"]["domain"])
        self.assertIsNone(rows["o5"]["naics_match"])

    def test_results_are_not_stored_and_intake_adds_as_new(self):
        with FakeApollo(payload=PAGE):
            self.client.post(self.url)
        self.assertEqual([f["domain"] for f in prospects.list_firms()], ["saved.example"])
        added = self.client.post("/api/v1/firms", json={"website": "lawyers.example", "display_name": "Example Lawyers"})
        self.assertEqual(added.json()["status"], "new")
        with FakeApollo(payload=PAGE):
            rows = {r["apollo_id"]: r for r in self.client.post(self.url).json()["results"]}
        self.assertTrue(rows["o1"]["already_added"])

    def test_invalid_form_spends_nothing(self):
        for body in ({"keywords": [" "]}, {"naics": ["54x"]}, {"employees": ["5-200"]}, {"page": 0},
                     {"employees": ["200,500"]}, {"employees": ["1,19", "50,1000"]}, {"employees": ["500,"]},
                     {"employees": ["0,19"]}, {"employees": ["50,20"]}):
            with self.subTest(body=body), FakeApollo(payload=PAGE) as fake:
                self.assertEqual(self.client.post(self.url, json=body).status_code, 422)
                self.assertEqual(fake.requests, [])

    def test_naics_is_checked_on_results_not_sent_to_apollo(self):
        with FakeApollo(payload=PAGE) as fake:
            rows = self.client.post(self.url, json={"naics": ["5613"], "locations": [], "employees": []}).json()["results"]
        # A blank size filter still searches small and medium firms only.
        self.assertEqual(fake.body(), {"per_page": 100, "q_organization_keyword_tags": ["law firm", "legal", "lawyers"],
                                       "organization_num_employees_ranges": ["1,19", "20,199"], "page": 1})
        self.assertEqual({r["apollo_id"]: r["naics_match"] for r in rows if r["naics_match"] is not None},
                         {"o1": False, "o2": False, "o4": True})

    def test_not_configured_and_errors(self):
        with FakeApollo() as fake, patch.dict(os.environ, {"APOLLO_API_KEY": ""}):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Apollo not configured", response.json()["detail"])
        self.assertEqual(fake.requests, [])
        for status, code in [(403, 502), (429, 429)]:
            with self.subTest(status=status), FakeApollo(status=status) as fake:
                self.assertEqual(self.client.post(self.url).status_code, code)
                self.assertEqual(len(fake.requests), 1)


if __name__ == "__main__":
    unittest.main()
