import copy
import json
import tempfile
import unittest
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import patch

from fastapi import HTTPException

import demo_plan
import prospects
from test_prospects import PAGES, RECORDS

TODAY = date(2026, 11, 19)
SOURCE = {"url": "https://brightwater.example/team", "title": "Team"}


def person(name, position, areas):
    return {"id": len(name), "full_name": name, "position": position, "practice_areas": areas,
            "biography": "Bio.", "qualifications": [], "public_work_email": name.split()[0].lower() + "@brightwater.example",
            "public_work_phone": "02 9999 0000", "source": SOURCE, "rep_edited": False}


# A made-up NSW firm: four practice areas (one with no template), eight people (one not fee-earning)
# and an approved Apollo contact who is not on the website.
SNAPSHOT = {
    "version": 2, "brief_token": "t", "firm": "Brightwater Legal", "domain": "brightwater.example",
    "facts": [],
    "practice_areas": [{"id": i, "name": name, "description": "Advice.", "services": [], "source": SOURCE,
                        "rep_edited": False}
                       for i, name in enumerate(["Family Law", "Property & Conveyancing", "Wills and Estates",
                                                 "Employment Law"], 1)],
    "people": [person("Avery Quill", "Managing Partner", ["Family Law"]),
               person("Morgan Hale", "Partner", ["Property & Conveyancing"]),
               person("Jordan Pike", "Partner", ["Family Law"]),
               person("Riley Fenn", "Partner", ["Wills and Estates"]),
               person("Casey Lowe", "Senior Associate", ["Family Law"]),
               person("Drew Marsh", "Paralegal", ["Property & Conveyancing"]),
               person("Sam Reed", "Practice Manager", []),
               person("Ellis Grove", "Lawyer", ["Family Law"])],
    "offices": [{"id": 1, "name": "Parramatta", "address": "12 Example Street, Parramatta NSW 2150", "phone": None,
                 "email": None, "source": SOURCE, "rep_edited": False}],
    "apollo": [{"key": "apollo:contact:x1", "category": "Apollo: contacts", "title": "Quinn Ashby",
                "retrieved_at": "2026-11-18",
                "person": {"full_name": "Quinn Ashby", "position": "Associate", "apollo_email": "quinn@brightwater.example",
                           "email_status": "verified", "linkedin_url": "https://linkedin.example/in/qa",
                           "match_confidence": "high"}}],
    "web": [],
}


def leaf(country, folder, name):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{country}/{folder}/{name}")), "name": name,
            "path": [folder, name]}


# Fixture matter-type leaves, the shape a provider flattens Docs Core's precedent tree into.
TREE = {
    "AU": [leaf("AU", folder, name) for folder, names in {
        "Family Law": ["Property Settlement", "Parenting", "Divorce", "Consent Orders", "Binding Financial Agreement"],
        "Conveyancing": ["Purchase", "Sale", "Transfer", "Refinance"],
        "Wills & Estates": ["Will", "Power of Attorney", "Probate", "Letters of Administration", "Estate Administration"],
        "Commercial": ["Business Sale", "Business Purchase", "Commercial Lease", "Shareholders Agreement",
                       "General Commercial"],
        "Litigation": ["Debt Recovery", "Local Court", "District Court", "Supreme Court", "General Litigation"],
    }.items() for name in names],
    "NZ": [leaf("NZ", folder, name) for folder, names in {
        "Family": ["Relationship Property", "Care of Children", "Dissolution"],
        "Conveyancing & Property": ["Purchase", "Sale", "Refinance"],
        "Wills & Estates": ["Will", "Enduring Power of Attorney", "Probate", "Estate Administration"],
        "Commercial": ["Business Sale", "Business Purchase", "Commercial Lease", "Shareholders Agreement"],
        "Litigation": ["Debt Recovery", "District Court", "High Court"],
    }.items() for name in names],
}


def matter_types(country, state):
    return TREE[country]


def no_tailoring(request):
    return {}


def generate(snapshot=SNAPSHOT, seed=7, tailor=no_tailoring, **kwargs):
    return demo_plan.generate_plan(snapshot, today=TODAY, seed=seed, matter_types=matter_types,
                                   tailor=tailor, **kwargs)


class GeneratePlanTests(unittest.TestCase):
    def test_fixture_snapshot_gives_the_same_valid_plan_every_time(self):
        plan = generate()
        self.assertEqual(json.dumps(plan, sort_keys=True), json.dumps(generate(), sort_keys=True))
        self.assertEqual(demo_plan.validate_plan(plan, matter_types=matter_types, today=TODAY), [])
        self.assertNotEqual(json.dumps(generate(seed=8)), json.dumps(plan))

    def test_staff_are_real_names_and_titles_in_seniority_order_without_contact_details(self):
        plan = generate()
        self.assertEqual([(s["slot"], s["full_name"], s["title"]) for s in plan["staff"]], [
            (1, "Avery Quill", "Managing Partner"),
            (2, "Jordan Pike", "Partner"),         # covers family law, the biggest area
            (3, "Morgan Hale", "Partner"),         # covers conveyancing, the next one
            (4, "Casey Lowe", "Senior Associate"),
            (5, "Ellis Grove", "Lawyer"),
            (6, "Drew Marsh", "Paralegal"),
        ])
        self.assertEqual((plan["staff"][0]["first_name"], plan["staff"][0]["last_name"]), ("Avery", "Quill"))
        self.assertNotIn("@", json.dumps(plan))
        self.assertNotIn("9999", json.dumps(plan))

    def test_rates_come_from_the_draft_table_for_the_firms_state(self):
        plan = generate()
        self.assertEqual(plan["region"], {"country": "AU", "state": "NSW", "key": "AU-NSW", "currency": "AUD"})
        self.assertEqual({s["seniority"]: s["rate"] for s in plan["staff"]},
                         {"managing_partner": 650, "partner": 560, "senior_associate": 460, "associate": 360,
                          "paralegal": 190})
        self.assertIn("checked by sales", plan["rates_note"])

    def test_nz_firm_uses_nz_rates_and_nz_matter_types(self):
        snapshot = {**SNAPSHOT, "domain": "brightwater.example.nz",
                    "offices": [{"name": "Wellington", "address": "1 Example Quay, Wellington 6011", "source": SOURCE}]}
        plan = generate(snapshot)
        self.assertEqual(plan["region"]["key"], "NZ")
        self.assertEqual(plan["staff"][0]["rate"], 550)
        nz_ids = {leaf["id"] for leaf in TREE["NZ"]}
        self.assertTrue(all(m["matter_type"]["id"] in nz_ids for m in plan["matters"]))
        self.assertTrue(all(not m["client"]["name"].endswith("Pty Ltd") for m in plan["matters"]))

    def test_matters_follow_the_practice_area_mix_with_fictional_clients(self):
        for seed in range(20):
            plan = generate(seed=seed)
            self.assertTrue(15 <= len(plan["matters"]) <= 25)
            counts = {m["template"]: m["matters"] for m in plan["mix"]}
            self.assertEqual(sum(counts.values()), len(plan["matters"]))
            # Family law (4 people + the area) outweighs conveyancing, which outweighs wills and estates.
            self.assertGreater(counts["family"], counts["conveyancing"])
            self.assertGreater(counts["conveyancing"], counts["wills_estates"])
            staff_names = {s["full_name"] for s in plan["staff"]} | {"Riley Fenn", "Sam Reed", "Quinn Ashby"}
            self.assertFalse({m["client"]["name"] for m in plan["matters"]} & staff_names)

    def test_area_without_a_template_falls_back_to_the_closest_with_a_flag(self):
        flags = [f for f in generate()["flags"] if f["code"] == "template_fallback"]
        self.assertEqual([(f["practice_area"], f["template"]) for f in flags], [("Employment Law", "commercial")])

    def test_time_covers_three_to_four_weeks_at_four_to_six_hours_a_day(self):
        for seed in range(10):
            plan = generate(seed=seed)
            dates = sorted({e["date"] for e in plan["time_entries"]})
            first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
            self.assertEqual(last, TODAY)
            self.assertIn((TODAY - first).days, range(14, 28))
            per_day = {}
            for entry in plan["time_entries"]:
                per_day[(entry["staff"], entry["date"])] = per_day.get((entry["staff"], entry["date"]), 0) + entry["hours"]
                self.assertEqual(entry["rate"], plan["staff"][entry["staff"] - 1]["rate"])
            self.assertTrue(all(4 <= round(hours, 1) <= 6 for hours in per_day.values()))
            self.assertEqual({slot for slot, _ in per_day}, {1, 2, 3, 4, 5, 6})

    def test_two_or_three_matters_are_invoiced_and_about_80_percent_of_time_is_unbilled(self):
        for seed in range(20):
            plan = generate(seed=seed)
            self.assertIn(len(plan["invoices"]), (2, 3))
            hours = sum(e["hours"] for e in plan["time_entries"])
            unbilled = sum(e["hours"] for e in plan["time_entries"] if not e["billed"]) / hours
            self.assertTrue(0.7 <= unbilled <= 0.9, f"seed {seed}: {unbilled:.0%} unbilled")
            self.assertTrue(plan["fees"] and plan["disbursements"])
            fee = plan["fees"][0]
            self.assertIn("fixed fee", fee["description"])

    def test_tailoring_changes_names_and_descriptions_only(self):
        requests = []

        def tailor(request):
            requests.append(request)
            return {"matters": [{"key": "M01", "client_name": "Juniper Vale", "description": "Vale: tailored matter",
                                 "time_entry_notes": ["Tailored note"], "hours": 99, "matter_type": "Space Law"},
                                {"key": "M02", "client_name": "Avery Quill", "description": "x" * 500},
                                {"key": "M99", "client_name": "Nobody"}],
                    "staff": [{"slot": 1, "rate": 1}]}

        plain, tailored = generate(), generate(tailor=tailor)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("Avery Quill", json.dumps(requests[0]))  # no staff names go to the tailor
        self.assertTrue(tailored["tailored"])
        first = tailored["matters"][0]
        self.assertEqual((first["client"]["name"], first["description"]), ("Juniper Vale", "Vale: tailored matter"))
        self.assertEqual(tailored["matters"][1], plain["matters"][1])  # staff name and overlong text refused
        notes = {e["description"] for e in tailored["time_entries"] if e["matter"] == "M01"}
        self.assertEqual(notes, {"Tailored note"})
        strip = lambda plan: [{k: v for k, v in e.items() if k != "description"} for e in plan["time_entries"]]
        self.assertEqual(strip(tailored), strip(plain))
        for part in ("staff", "fees", "disbursements", "invoices"):
            self.assertEqual(tailored[part], plain[part])
        self.assertEqual([m["matter_type"] for m in tailored["matters"]], [m["matter_type"] for m in plain["matters"]])

    def test_failed_tailoring_keeps_template_wording_with_a_flag(self):
        def broken(request):
            raise RuntimeError("provider down")

        plan = generate(tailor=broken)
        self.assertFalse(plan["tailored"])
        self.assertIn("tailoring_failed", [f["code"] for f in plan["flags"]])
        self.assertEqual(plan["matters"], generate()["matters"])

    def test_templates_whose_matter_types_do_not_resolve_are_dropped_with_a_flag(self):
        tree = [leaf for leaf in TREE["AU"] if leaf["path"][0] != "Wills & Estates"]
        plan = demo_plan.generate_plan(SNAPSHOT, today=TODAY, seed=7, matter_types=lambda c, s: tree)
        self.assertNotIn("wills_estates", {m["template"] for m in plan["matters"]})
        self.assertIn("template_unresolved", [f["code"] for f in plan["flags"]])
        with self.assertRaises(demo_plan.PlanError):
            demo_plan.generate_plan(SNAPSHOT, today=TODAY, seed=7, matter_types=lambda c, s: [])


class ValidatePlanTests(unittest.TestCase):
    def errors(self, plan):
        return demo_plan.validate_plan(plan, matter_types=matter_types, today=TODAY)

    def test_unknown_matter_type_is_rejected(self):
        plan = generate()
        plan["matters"][0]["matter_type"] = {"name": "Space Law", "folder": None, "id": None}
        errors = self.errors(plan)
        self.assertEqual([e["path"] for e in errors], ["matters[0].matter_type"])
        self.assertIn("Space Law", errors[0]["message"])

    def test_matter_type_id_must_belong_to_the_firms_country(self):
        plan = generate()
        nz_purchase = next(leaf for leaf in TREE["NZ"] if leaf["name"] == "Purchase")
        plan["matters"][0]["matter_type"] = {"name": "Purchase", "folder": None, "id": nz_purchase["id"]}
        self.assertEqual([e["path"] for e in self.errors(plan)], ["matters[0].matter_type"])

    def test_future_dates_and_out_of_range_amounts_are_rejected(self):
        plan = generate()
        plan["time_entries"][0]["date"] = "2026-11-20"
        plan["fees"][0]["amount"] = 0
        plan["disbursements"][0]["amount"] = 999999
        self.assertEqual([e["path"] for e in self.errors(plan)],
                         ["time_entries[0].date", "fees[0].amount", "disbursements[0].amount"])

    def test_malformed_plan_is_reported_not_raised(self):
        self.assertEqual(self.errors({"version": 1})[0]["path"], "")


class OpenAITailorTests(unittest.TestCase):
    def test_one_openai_call_gets_matter_wording_but_no_staff_or_contact_details(self):
        plan = generate()
        parsed = demo_plan.Tailoring(matters=[demo_plan.TailoredMatter(
            key="M01", client_name="Juniper Vale", description="Vale: tailored", time_entry_notes=["Note"])])
        with patch("demo_plan.openai.OpenAI") as client_class:
            client = client_class.return_value.__enter__.return_value
            client.responses.parse.return_value = Record(status="completed", output_parsed=parsed)
            result = demo_plan.tailor_with_openai(demo_plan.tailoring_request(plan))
        self.assertEqual(client.responses.parse.call_count, 1)
        sent = json.dumps(client.responses.parse.call_args.kwargs["input"])
        self.assertIn("M01", sent)
        for private in ("Avery Quill", "@", "9999"):
            self.assertNotIn(private, sent)
        self.assertEqual(result["matters"][0]["client_name"], "Juniper Vale")


class DemoPlanEndpointTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prospects, "DB_PATH", Path(folder) / "test.sqlite3"))
        prospects.initialize()
        self.firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        with prospects.database() as db:
            prospects.store_research(db, self.firm["id"], PAGES, RECORDS)
        detail = prospects.get_firm(self.firm["id"])
        self.approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=detail["brief_token"], people=[p["id"] for p in detail["records"]["people"]],
            practice_areas=[a["id"] for a in detail["records"]["practice_areas"]],
            offices=[o["id"] for o in detail["records"]["offices"]]))
        self.tailor = self.enterContext(patch("demo_plan.tailor_with_openai", return_value={}))
        self.enterContext(patch("demo_plan.current_date", return_value=TODAY))

    def generate(self, **kwargs):
        body = demo_plan.PlanRequest(approval_id=self.approval["id"], seed=3, today=TODAY, **kwargs)
        return demo_plan.create_plan(self.firm["id"], body)

    def status(self, call):
        with self.assertRaises(HTTPException) as failure:
            call()
        return failure.exception.status_code

    def test_matter_types_need_a_provider(self):
        with patch.object(demo_plan, "matter_type_provider", None):
            self.assertEqual(self.status(self.generate), 503)
        self.assertEqual(self.status(lambda: demo_plan.get_plan(self.firm["id"])), 404)

    def test_generated_plan_is_saved_per_firm_and_can_be_edited(self):
        with patch.object(demo_plan, "matter_type_provider", matter_types):
            saved = self.generate()
            self.tailor.assert_called_once()
            self.assertEqual((saved["approval_id"], saved["errors"], saved["edited"]), (self.approval["id"], [], False))
            self.assertEqual(demo_plan.get_plan(self.firm["id"])["plan"], saved["plan"])
            self.assertEqual(saved["plan"]["staff"][0]["full_name"], "Jane Smith")

            plan = saved["plan"]
            plan["staff"][0]["rate"] = 600
            plan["matters"][0]["matter_type"] = {"name": "Parenting"}  # rep picks another type by name
            edited = demo_plan.edit_plan(self.firm["id"], demo_plan.PlanEdit(plan=plan))
            self.assertTrue(edited["edited"])
            first = edited["plan"]["time_entries"][0]
            self.assertEqual((first["rate"], first["amount"]), (600, round(first["hours"] * 600, 2)))
            parenting = next(leaf for leaf in TREE["AU"] if leaf["name"] == "Parenting")
            self.assertEqual(edited["plan"]["matters"][0]["matter_type"]["id"], parenting["id"])

            plan = copy.deepcopy(edited["plan"])
            plan["matters"][1]["matter_type"] = {"name": "Space Law"}
            self.assertEqual(self.status(
                lambda: demo_plan.edit_plan(self.firm["id"], demo_plan.PlanEdit(plan=plan))), 422)
            self.assertEqual(demo_plan.get_plan(self.firm["id"])["plan"], edited["plan"])
            self.assertEqual(self.generate(tailor=False)["edited"], False)
            self.tailor.assert_called_once()

    def test_plan_needs_an_approval_of_this_firm(self):
        with patch.object(demo_plan, "matter_type_provider", matter_types):
            body = demo_plan.PlanRequest(approval_id=self.approval["id"] + 1, today=TODAY)
            self.assertEqual(self.status(lambda: demo_plan.create_plan(self.firm["id"], body)), 404)
