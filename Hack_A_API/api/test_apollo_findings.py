import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

import prospects
from apollo_findings import apollo_findings
from test_apollo_contacts import JANE, PERSON, ScriptedApollo, brief_with

# Fake organisation only. Tech mirrors the probe: web and IT tools, no practice-management software.
ORG = {"name": "Firm Ltd", "industries": ["Legal Services"], "keywords": ["family law", "Family Law", "wills"],
       "technology_names": ["Cloudflare", "Microsoft Office 365", "Google Tag Manager"],
       "current_technologies": [{"uid": "cloudflare", "name": "Cloudflare", "category": "CDN"}]}
BOB = {"id": "ap-bob", "name": "Bob Example", "title": "Practice Manager", "email": "bob@firm.example",
       "email_status": "verified", "linkedin_url": "https://linkedin.example/in/bob", "match_confidence": "high"}


class ApolloFindingTests(unittest.TestCase):
    """Temporary database, faked research and faked Apollo reveals: no network, no credits."""

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
        with patch("prospects.crawl_site", return_value=([{"url": "https://firm.example/team", "title": "Team",
                                                            "text": "team"}], [])),                 patch("prospects.extract_profile", return_value=(brief_with(*people), 0)),                 patch("prospects.openai.OpenAI"):
            prospects.research(firm_id, "firm.example")

    def staff(self, firm_id):
        return prospects.get_firm(firm_id)["staff"]

    def reveal(self, firm_id, *matches):
        with ScriptedApollo((200, {"matches": list(matches)})):
            return prospects.apollo_reveal(firm_id, prospects.ApolloReveal(ids=[m["id"] for m in matches]))

    def enrich(self, firm_id, org=ORG):
        with prospects.database() as db:
            db.execute("UPDATE firms SET apollo_org=?, apollo_enriched_at='2026-11-18 09:30:00' WHERE id=?",
                       (json.dumps(org), firm_id))

    def approve(self, firm_id, findings=(), apollo_keys=()):
        detail = prospects.get_firm(firm_id)
        approval = prospects.approve(firm_id, prospects.Approval(
            brief_token=detail["brief_token"], findings=list(findings), apollo_findings=list(apollo_keys)))
        with prospects.database() as db:
            snapshot = db.execute("SELECT snapshot FROM proposals WHERE id=?", (approval["id"],)).fetchone()[0]
        export = json.loads(prospects.demo_export(firm_id, approval["id"]).body)
        return approval, snapshot, export

    def test_findings_are_labelled_apollo_with_retrieved_date_and_no_evidence(self):
        firm_id = self.firm()
        self.enrich(firm_id)
        self.reveal(firm_id, BOB)
        found = {f["key"]: f for f in prospects.get_firm(firm_id)["apollo_findings"]}
        self.assertEqual(sorted(found), ["apollo:contact:ap-bob", "apollo:industry:legal services",
                                         "apollo:keyword:family law", "apollo:keyword:wills",
                                         "apollo:tech:cloudflare", "apollo:tech:google tag manager",
                                         "apollo:tech:microsoft office 365"])
        tech = found["apollo:tech:cloudflare"]
        self.assertEqual((tech["source"], tech["evidence"], tech["retrieved_at"]), ("Apollo", "", "2026-11-18"))
        self.assertEqual(tech["sources"], [{"url": "", "title": "Source: Apollo, retrieved 2026-11-18"}])
        self.assertIn("(CDN)", tech["text"])
        contact = found["apollo:contact:ap-bob"]
        self.assertEqual(contact["person"]["apollo_email"], "bob@firm.example")
        self.assertEqual(contact["sources"][0]["url"], "https://linkedin.example/in/bob")
        self.assertTrue(contact["retrieved_at"])  # reveal date

    def test_approved_snapshot_contains_only_selected_apollo_findings(self):
        firm_id = self.firm()
        self.research(firm_id, PERSON)
        self.enrich(firm_id)
        self.reveal(firm_id, BOB)
        chosen = ["apollo:industry:legal services", "apollo:tech:microsoft office 365", "apollo:contact:ap-bob"]
        approval, snapshot, export = self.approve(firm_id, [0], chosen)
        keys = [f.get("key") for f in json.loads(snapshot)["findings"]]
        self.assertEqual(keys, [None, *chosen])
        for dropped in ("Cloudflare", "Google Tag Manager", "wills"):
            self.assertNotIn(dropped, snapshot)
        self.assertEqual([f.get("key") for f in export["findings"]], keys)
        bob = next(p for p in export["staff"] if p["full_name"] == "Bob Example")
        self.assertEqual((bob["apollo_email"], bob["linkedin_url"]),
                         ("bob@firm.example", "https://linkedin.example/in/bob"))
        # The deck build receives exactly this snapshot.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
                patch("prospects.generate_deck", return_value=b"pptx") as build:
            tasks = BackgroundTasks()
            prospects.start_build(firm_id, approval["id"], tasks, {})
            for task in tasks.tasks:
                task.func(*task.args, **task.kwargs)
        self.assertEqual(build.call_args.args[0], snapshot)

    def test_deselected_contact_stays_out_of_snapshot_export_and_deck(self):
        firm_id = self.firm()
        self.research(firm_id, PERSON)
        self.reveal(firm_id, JANE)  # merges into the website row: source 'both'
        self.assertEqual(self.staff(firm_id)[0]["source"], "both")
        _, snapshot, export = self.approve(firm_id, [0], [])
        self.assertNotIn(JANE["email"], snapshot)
        self.assertNotIn("linkedin.example", snapshot)
        jane = export["staff"][0]
        self.assertEqual(jane["position"], "Partner")
        self.assertNotIn("apollo_email", jane)
        self.assertNotIn("linkedin_url", jane)
        _, snapshot, export = self.approve(firm_id, [0], ["apollo:contact:ap-jane"])
        self.assertIn(JANE["email"], snapshot)
        jane = export["staff"][0]
        self.assertEqual((jane["apollo_email"], jane["position"], jane["biography"]),
                         (JANE["email"], "Partner", "Family lawyer"))
        self.assertEqual(len(jane["sources"]), 2)

    def test_firm_without_apollo_data_behaves_as_before(self):
        firm_id = self.firm()
        self.research(firm_id, PERSON)
        detail = prospects.get_firm(firm_id)
        self.assertEqual(detail["apollo_findings"], [])
        with prospects.database() as db:
            brief = db.execute("SELECT brief FROM firms WHERE id=?", (firm_id,)).fetchone()[0]
        self.assertEqual(detail["brief_token"], prospects.digest(brief))
        approval = prospects.approve(firm_id, prospects.Approval(brief_token=detail["brief_token"], findings=[0]))
        with prospects.database() as db:
            snapshot = db.execute("SELECT snapshot FROM proposals WHERE id=?", (approval["id"],)).fetchone()[0]
        self.assertEqual(snapshot, json.dumps({"firm": "firm.example", "domain": "firm.example",
                                               "findings": json.loads(brief)}))
        with self.assertRaises(HTTPException) as empty:
            prospects.approve(firm_id, prospects.Approval(brief_token=detail["brief_token"], findings=[]))
        self.assertEqual(empty.exception.status_code, 422)

    def test_apollo_only_firm_can_be_approved(self):
        firm_id = self.firm()
        self.enrich(firm_id)
        _, snapshot, export = self.approve(firm_id, [], ["apollo:industry:legal services"])
        self.assertEqual([f["key"] for f in json.loads(snapshot)["findings"]], ["apollo:industry:legal services"])
        self.assertEqual(export["staff"], [])

    def test_stale_review_and_unknown_keys_are_refused(self):
        firm_id = self.firm()
        self.research(firm_id, PERSON)
        token = prospects.get_firm(firm_id)["brief_token"]
        self.enrich(firm_id)  # new Apollo findings appear: the reviewed set changed
        with self.assertRaises(HTTPException) as stale:
            prospects.approve(firm_id, prospects.Approval(brief_token=token, findings=[0]))
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as unknown:
            self.approve(firm_id, [0], ["apollo:tech:clio"])
        self.assertEqual(unknown.exception.status_code, 422)

    def test_org_without_lists_and_contact_without_email(self):
        found = apollo_findings({"industry": "law practice", "keywords": None}, None,
                                [{"apollo_id": "x", "full_name": "No Email", "position": None}])
        self.assertEqual([f["key"] for f in found], ["apollo:industry:law practice", "apollo:contact:x"])
        self.assertEqual(found[0]["sources"][0]["title"], "Source: Apollo")
        self.assertIn("no work email found", found[1]["text"])


if __name__ == "__main__":
    unittest.main()
