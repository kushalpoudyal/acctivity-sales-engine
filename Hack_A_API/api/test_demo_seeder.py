"""Demo seeder (#19): mocked end to end against a fake Corto (httpx.MockTransport through demo_logins' hooks)."""
import json
import os
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import demo_logins
import demo_plan
import demo_seeder
import live_updates
import prospects
from test_demo_logins import NOW, SETTINGS, rep_jwt
from test_demo_plan import SNAPSHOT, SOURCE, TREE, generate

FIRM = "firm-1111"  # the demo firm (on the allowlist in SETTINGS)
OWNER = "user-owner"
STAFF = [f"user-staff-{n}" for n in range(1, 7)]
SEED_SETTINGS = {**SETTINGS, "DEMO_FIRM_ID": FIRM, "DEMO_STAFF_USER_IDS": ",".join(STAFF),
                 "DEMO_SEED_CONCURRENCY": "3"}
NZ_SNAPSHOT = {**SNAPSHOT, "domain": "brightwater.example.nz",
               "offices": [{"name": "Wellington", "address": "1 Example Quay, Wellington 6011", "source": SOURCE}]}


def tree_nodes(leaves):
    """Nest fixture leaves the way Docs Core returns the precedent tree, plus one deleted leaf."""
    folders = {}
    for leaf in leaves:
        folder = folders.setdefault(leaf["path"][0], {"id": str(uuid.uuid4()), "name": leaf["path"][0],
                                                      "type": "Folder", "parentId": None, "deleteCode": "0",
                                                      "children": []})
        folder["children"].append({"id": leaf["id"], "name": leaf["name"], "type": "MatterType",
                                   "parentId": folder["id"], "deleteCode": "0", "children": []})
    first = next(iter(folders.values()))
    first["children"].append({"id": "deleted-type", "name": "Old Type", "type": "MatterType",
                              "parentId": first["id"], "deleteCode": "1", "children": []})
    return list(folders.values())


class FakeCorto:
    """Auth0 + Management + Docs Core + Acctivity, answering from simple state. `fail` maps a (method, path)
    to [status, after]: the call fails once `after` earlier calls to it succeeded, until cleared."""

    def __init__(self, is_test_firm=True, users=None, country="NZ"):
        self.is_test_firm, self.country = is_test_firm, country
        self.users = users if users is not None else [
            {"userId": OWNER, "firstName": "Rep", "lastName": "Owner", "isAccountOwner": True, "deleteCode": 0},
            *[{"userId": uid, "firstName": f"Alias{n}", "lastName": "Demo", "isAccountOwner": False,
               "deleteCode": 0} for n, uid in enumerate(STAFF, 1)]]
        self.requests, self.fail, self.counts = [], {}, Counter()
        self.settings = {STAFF[0]: 7}  # staff 1 already has a rate row (rowversion 7); the rest get 404
        self.matters = {}

    def __enter__(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [patch.object(demo_logins, "_transport", httpx.MockTransport(self.handle)),
                        patch.object(demo_logins, "_now", lambda: NOW),
                        patch.dict(os.environ, SEED_SETTINGS),
                        patch.object(prospects, "DB_PATH", Path(self.folder.name) / "test.sqlite3")]
        for p in self.patches:
            p.start()
        demo_logins.reset()
        demo_logins.set_rep_token(rep_jwt(firm=FIRM), "pasted")
        prospects.initialize()
        return self

    def __exit__(self, *exc):
        demo_logins.reset()
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()

    def handle(self, request):
        self.requests.append(request)
        host, method, path = request.url.host, request.method, request.url.path.removeprefix("/api/")
        if host == "auth.test.invalid":
            return httpx.Response(200, json={"access_token": "service-token", "expires_in": 3600})
        route = (method, "v1/" + path.split("/")[1] if path.count("/") >= 1 else path)
        key = (method, path) if (method, path) in self.fail else route
        if key in self.fail:
            status, after = self.fail[key]
            if self.counts[key] >= after:
                return httpx.Response(status, json={"title": "injected failure"})
        self.counts[key] += 1
        body = json.loads(request.content) if request.content else None
        new_id = str(uuid.uuid4())
        if (method, path) == ("GET", "v1/firm"):
            return httpx.Response(200, json={"firmId": FIRM, "firmName": "Alias Firm", "isTestFirm": self.is_test_firm,
                                             "countryCode": self.country, "industryCategoryId": 1})
        if (method, path) == ("GET", "v1/users"):
            return httpx.Response(200, json=self.users)
        if (method, path) == ("GET", "v1/precedent-tree/matter-types"):
            return httpx.Response(200, json=tree_nodes(TREE[self.country]))
        if path == "v1/users/settings":
            user = request.headers.get("x-userid")
            if method == "GET":
                if user not in self.settings:
                    return httpx.Response(404)
                return httpx.Response(200, json={"userId": user, "billingRatePerHour": 1, "rowversion": self.settings[user]})
            self.settings[user] = self.settings.get(user, 0) + 1
            return httpx.Response(200, json={"userId": user})
        if (method, path) == ("POST", "v1/matters"):
            self.matters[new_id] = body
            return httpx.Response(201, json={"matterId": new_id})
        if method == "GET" and path.startswith("v1/matters/"):
            matter_id = path.split("/")[2]
            return httpx.Response(200, json={
                "matterId": matter_id, "fileNumber": self.matters[matter_id]["fileNumber"], "description": "",
                "status": "InProgress", "deleteCode": 0, "acl": {"enabled": False, "userIds": []},
                "customFields": [], "definableTables": [], "memories": [], "criticalDates": [],
                "contacts": [{"contactId": None, "tableId": "T1", "tableDescription": "Purchaser",
                              "displaySequence": 0, "deleteCode": 0},
                             {"contactId": None, "tableId": "T2", "tableDescription": "Client",
                              "displaySequence": 1, "deleteCode": 0}],
                "userResponsible": None, "substitution": {}})
        ids = {"v1/activities": "activityId", "v1/fees": "feeId", "v1/cost-recoveries": "costRecoveryId",
               "v1/invoices": "invoiceId"}
        if method == "POST" and path in ids:
            return httpx.Response(201, json={ids[path]: new_id})
        return httpx.Response(200 if method in ("PATCH", "PUT") else 201, json={})

    def writes(self, method=None, path=None):
        found = [r for r in self.requests if r.method != "GET" and r.url.host != "auth.test.invalid"]
        if method:
            found = [r for r in found if r.method == method]
        if path:
            found = [r for r in found if r.url.path.removeprefix("/api/") == path
                     or (path.endswith("/") and r.url.path.removeprefix("/api/").startswith(path))]
        return found


def body(request):
    return json.loads(request.content)


def app():
    application = FastAPI()
    application.include_router(demo_seeder.router)
    return application


def add_firm_with_plan(plan):
    with prospects.database() as db:
        firm_id = db.execute("INSERT INTO firms(domain,name) VALUES('brightwater.example.nz','Brightwater')").lastrowid
        db.execute("""INSERT INTO demo_plans(firm_id,approval_id,plan,created_at,updated_at)
                      VALUES(?,1,?,'2026-11-19','2026-11-19')""", (firm_id, json.dumps(plan)))
    return firm_id


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.plan = generate(NZ_SNAPSHOT)
        self.fake = self.enterContext(FakeCorto())
        self.firm_id = add_firm_with_plan(self.plan)
        self.client = TestClient(app())

    def seed(self, **body):
        return self.client.post(f"/api/v1/firms/{self.firm_id}/demo-seed", json=body)

    def test_seeds_the_whole_plan_in_order_and_reports_progress(self):
        response = self.seed()
        self.assertEqual(response.status_code, 202, response.text)
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual(status["status"], "done", status.get("error"))
        plan, fake = self.plan, self.fake
        self.assertEqual({s["step"]: (s["done"], s["total"]) for s in status["steps"]}, {
            "firm_name": (1, 1), "staff_names": (6, 6), "rates": (6, 6), "matters": (len(plan["matters"]),) * 2,
            "contacts": (len(plan["matters"]),) * 2, "time_entries": (len(plan["time_entries"]),) * 2,
            "fees": (len(plan["fees"]),) * 2, "disbursements": (len(plan["disbursements"]),) * 2,
            "invoices": (len(plan["invoices"]),) * 2, "matter_details": (len(plan["matters"]),) * 2})

        # Steps happen in the issue's order.
        order = []
        for r in fake.writes():
            step = r.url.path.removeprefix("/api/").split("/")[1]
            if not order or order[-1] != step:
                order.append(step)
        self.assertEqual(order, ["firm", "users", "matters", "contacts", "activities", "fees", "cost-recoveries",
                                 "invoices", "matters"])

        # 1-2. Firm and staff renamed with the rep login; staff slot N is configured user N; never the owner.
        rename = fake.writes("PATCH", "v1/firm/name")[0]
        self.assertEqual(body(rename), {"firmName": "Brightwater Legal"})
        self.assertTrue(rename.headers["authorization"].startswith("Bearer ey"))
        renames = {r.url.path.rsplit("/", 1)[1]: body(r) for r in fake.writes("PATCH", "v1/users/")
                   if r.url.host == "management.test.invalid"}
        self.assertEqual(renames, {STAFF[s["slot"] - 1]: {"firstName": s["first_name"], "lastName": s["last_name"]}
                                   for s in plan["staff"]})
        self.assertNotIn(OWNER, renames)

        # 3. Rates as each staff member: create when missing, json-patch with the row version when present.
        creates = {r.headers["x-userid"]: body(r) for r in fake.writes("POST", "v1/users/settings")}
        rate = {STAFF[s["slot"] - 1]: s["rate"] for s in plan["staff"]}
        self.assertEqual(creates, {uid: {"billingRatePerHour": rate[uid]} for uid in STAFF[1:]})
        update = fake.writes("PATCH", "v1/users/settings")[0]
        self.assertEqual(update.headers["x-userid"], STAFF[0])
        self.assertEqual(update.headers["rowversion"], "7")
        self.assertEqual(update.headers["content-type"], "application/json-patch+json")
        self.assertEqual(body(update), [{"op": "replace", "path": "/billingRatePerHour", "value": rate[STAFF[0]]}])

        # 4. Matters with the service login as the responsible lawyer; the description is in the file number.
        matter_posts = fake.writes("POST", "v1/matters")
        first = plan["matters"][0]
        self.assertEqual(matter_posts[0].headers["x-userid"], STAFF[first["responsible"] - 1])
        self.assertEqual(body(matter_posts[0])["matterType"],
                         {"matterTypeId": first["matter_type"]["id"], "matterTypeName": first["matter_type"]["name"]})
        self.assertIn(first["description"], body(matter_posts[0])["fileNumber"])
        self.assertEqual(len({body(r)["fileNumber"] for r in matter_posts}), len(plan["matters"]))
        created_matters = set(fake.matters)

        # 5. Contacts with the rep login, one per matter, on ids we chose.
        contacts = fake.writes("PUT", "v1/contacts/")
        self.assertEqual(len(contacts), len(plan["matters"]))
        self.assertNotIn("x-firmid", contacts[0].headers)
        client = first["client"]
        if client["kind"] == "company":
            self.assertEqual((body(contacts[0])["type"], body(contacts[0])["fullName"]), ("Organisation", client["name"]))
        contact_ids = {r.url.path.rsplit("/", 1)[1] for r in contacts}

        # 6. Time entries as the staff member who did the work, against matters we created.
        activities = fake.writes("POST", "v1/activities")
        self.assertEqual(len(activities), len(plan["time_entries"]))
        self.assertEqual(Counter(r.headers["x-userid"] for r in activities),
                         Counter(STAFF[e["staff"] - 1] for e in plan["time_entries"]))
        self.assertTrue({body(r)["matterId"] for r in activities} <= created_matters)
        entry = plan["time_entries"][0]
        sample = next(body(r) for r in activities if body(r)["description"] == entry["description"]
                      and body(r)["timestamp"].startswith(entry["date"]))
        self.assertEqual(sample["timestamp"][-6:], "+13:00")  # NZ daylight time in November
        self.assertEqual(set(sample), {"matterId", "timestamp", "description", "elapsedSeconds", "amount",
                                       "inclusiveTax"})
        self.assertFalse(sample["inclusiveTax"])

        fee = fake.writes("POST", "v1/fees")[0]
        self.assertEqual(body(fee)["quantity"], 1)
        self.assertEqual(body(fee)["amountEach"], plan["fees"][0]["amount"])
        if plan["disbursements"]:
            cost = body(fake.writes("POST", "v1/cost-recoveries")[0])
            amount = plan["disbursements"][0]["amount"]
            self.assertEqual((cost["amountExTax"], cost["amountTax"], cost["amountIncTax"], cost["quantity"]),
                             (amount, round(amount * 0.15, 2), round(amount * 1.15, 2), 1))

        # Invoices bill exactly the plan's billed items, by the ids Acctivity gave them.
        invoices = fake.writes("POST", "v1/invoices")
        self.assertEqual(len(invoices), len(plan["invoices"]))
        billed = sum(1 for e in plan["time_entries"] if e["billed"])
        self.assertEqual(sum(len(body(r)["activityIds"]) for r in invoices), billed)

        # Last: each matter gets its description, responsible lawyer and client contact in the Client slot.
        saves = fake.writes("PUT", "v1/matters/")
        self.assertEqual(len(saves), len(plan["matters"]))
        save = body(saves[0])
        self.assertEqual(save["description"], first["description"])
        self.assertEqual(save["userResponsible"], STAFF[first["responsible"] - 1])
        client_slot = next(c for c in save["contacts"] if c["tableDescription"] == "Client")
        self.assertIn(client_slot["contactId"], contact_ids)

    def test_progress_reaches_the_live_updates_stream(self):
        self.seed()
        state = live_updates.firm_state(self.firm_id)
        self.assertEqual(state["demo_seed"]["status"], "done")
        self.assertEqual(state["id"], self.firm_id)

    def test_a_failed_run_resumes_where_it_stopped_without_redoing_anything(self):
        self.fake.fail[("POST", "v1/activities")] = [500, 40]
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual((status["status"], status["step"]), ("failed", "time_entries"))
        self.assertIn("HTTP 500", status["error"])
        done_before = next(s["done"] for s in status["steps"] if s["step"] == "time_entries")
        self.assertGreaterEqual(done_before, 40)
        writes_before = len(self.fake.writes())

        del self.fake.fail[("POST", "v1/activities")]
        self.assertEqual(self.seed().status_code, 202)
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual(status["status"], "done", status.get("error"))
        later = self.fake.writes()[writes_before:]
        later_paths = {r.url.path.removeprefix("/api/").split("/")[1] for r in later}
        self.assertNotIn("firm", later_paths)
        self.assertNotIn("users", later_paths)
        self.assertNotIn("contacts", later_paths)
        self.assertFalse([r for r in later if r.method == "POST" and r.url.path.endswith("/v1/matters")])
        # Every entry exists exactly once across both attempts.
        self.assertEqual(self.fake.counts[("POST", "v1/activities")], len(self.plan["time_entries"]))

    def test_a_4xx_stops_the_run_and_is_not_retried(self):
        self.fake.fail[("POST", "v1/matters")] = [400, 2]
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual((status["status"], status["step"]), ("failed", "matters"))
        self.assertIn("HTTP 400", status["error"])
        self.assertEqual(len(self.fake.writes("POST", "v1/matters")), 3)  # 2 created + 1 refused, no retry
        self.assertFalse(self.fake.writes("PUT", "v1/contacts/"))

    def test_a_contact_resumed_after_failure_reuses_the_id_it_chose(self):
        self.fake.fail[("PUT", "v1/contacts")] = [500, 0]
        self.seed()
        first_id = self.fake.writes("PUT", "v1/contacts/")[0].url.path
        del self.fake.fail[("PUT", "v1/contacts")]
        self.seed()
        self.assertEqual(self.fake.writes("PUT", "v1/contacts/")[1].url.path, first_id)

    def test_refuses_staff_ids_it_did_not_read_from_the_firm(self):
        self.fake.users = [u for u in self.fake.users if u["userId"] != STAFF[3]]
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual(status["status"], "failed")
        self.assertIn("slot 4", status["error"])
        self.assertEqual(self.fake.writes(), [])

    def test_refuses_to_rename_the_account_owner(self):
        with patch.dict(os.environ, {"DEMO_STAFF_USER_IDS": ",".join([OWNER, *STAFF[1:]])}):
            self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertIn("owner", status["error"])
        self.assertEqual(self.fake.writes(), [])

    def test_refuses_matter_types_not_in_the_firms_tree(self):
        plan = json.loads(json.dumps(self.plan))
        plan["matters"][0]["matter_type"]["id"] = "deleted-type"
        with prospects.database() as db:
            db.execute("UPDATE demo_plans SET plan=? WHERE firm_id=?", (json.dumps(plan), self.firm_id))
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertIn("M01", status["error"])
        self.assertEqual(self.fake.writes(), [])

    def test_refuses_a_plan_for_another_country(self):
        self.fake.country = "AU"
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertIn("NZ", status["error"])
        self.assertEqual(self.fake.writes(), [])

    def test_guards_stop_the_run_before_any_write(self):
        self.fake.is_test_firm = False
        self.seed()
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual(status["status"], "failed")
        self.assertIn("not a test firm", status["error"])
        self.assertEqual(self.fake.writes(), [])

    def test_a_firm_off_the_allowlist_is_refused_up_front(self):
        with patch.dict(os.environ, {"DEMO_FIRM_ALLOWLIST": "firm-9999"}):
            response = self.seed()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fake.writes(), [])

    def test_missing_settings_are_named(self):
        with patch.dict(os.environ, {"DEMO_STAFF_USER_IDS": ""}):
            response = self.seed()
        self.assertEqual(response.status_code, 503)
        self.assertIn("DEMO_STAFF_USER_IDS", response.text)

    def test_a_finished_seed_is_not_repeated_without_fresh(self):
        self.seed()
        writes = len(self.fake.writes())
        self.assertEqual(self.seed().status_code, 409)
        self.assertEqual(len(self.fake.writes()), writes)
        self.assertEqual(self.seed(fresh=True).status_code, 202)
        status = self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()
        self.assertEqual(status["status"], "done")
        self.assertEqual(len(self.fake.writes("POST", "v1/matters")), 2 * len(self.plan["matters"]))

    def test_no_plan_is_a_404(self):
        with prospects.database() as db:
            db.execute("DELETE FROM demo_plans")
        self.assertEqual(self.seed().status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").status_code, 404)


class MatterTypeProviderTests(unittest.TestCase):
    def test_flattens_live_matter_type_leaves_with_their_folder_path(self):
        with FakeCorto() as fake:
            leaves = demo_seeder.docs_core_matter_types("NZ", None)
        self.assertEqual(sorted(l["id"] for l in leaves), sorted(l["id"] for l in TREE["NZ"]))
        purchase = next(l for l in leaves if l["name"] == "Purchase")
        self.assertEqual(purchase["path"], ["Conveyancing & Property", "Purchase"])
        tree_call = next(r for r in fake.requests if r.url.path.endswith("precedent-tree/matter-types"))
        self.assertTrue(tree_call.headers["authorization"].startswith("Bearer ey"))  # the rep login

    def test_unavailable_without_a_current_rep_login(self):
        with FakeCorto():
            demo_logins.reset()
            with self.assertRaises(demo_plan.MatterTypesUnavailable):
                demo_seeder.docs_core_matter_types("NZ", None)

    def test_unavailable_when_the_demo_firm_is_not_configured(self):
        with FakeCorto(), patch.dict(os.environ, {"DEMO_FIRM_ID": ""}):
            with self.assertRaises(demo_plan.MatterTypesUnavailable) as error:
                demo_seeder.docs_core_matter_types("NZ", None)
        self.assertIn("DEMO_FIRM_ID", str(error.exception))


if __name__ == "__main__":
    unittest.main()
