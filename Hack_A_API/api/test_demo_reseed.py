"""Demo reseed (#20): guarded wipe of the demo firm, then a fresh seed. Mocked end to end against a stateful fake
Corto (httpx.MockTransport through demo_logins' hooks), built on the seeder tests' FakeCorto."""
import json
import os
import unittest
import uuid
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import demo_logins
import demo_reseed
import demo_seeder
import live_updates
import prospects
from test_demo_logins import CLAIMS, rep_jwt
from test_demo_seeder import FIRM, NZ_SNAPSHOT, OWNER, STAFF, FakeCorto, add_firm_with_plan, generate

REP = "user-rep"            # the rep's own user (an administrator, not the owner)
INVITEES = ["user-invitee-1", "user-invitee-2"]
GONE = "user-already-deleted"
FIRM_NAME = "Alias Firm"


class FakeFirm(FakeCorto):
    """FakeCorto plus what a wipe needs: listable activities / fees / cost recoveries with row versions, invoices
    that bill them, Docs Core matters and contacts (a contact that is a party on a live matter can't be deleted),
    users that can be deleted and pending invitations. Starts with a previous prospect's data in it."""

    LISTS = {"v1/activities": ("activities", "activityId"), "v1/fees": ("fees", "feeId"),
             "v1/cost-recoveries": ("costRecoveries", "costRecoveryId")}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.users += [
            {"userId": REP, "firstName": "Rep", "lastName": "Admin", "emailAddress": "rep@example.test",
             "isAccountOwner": False, "deleteCode": 0},
            {"userId": INVITEES[0], "firstName": "Ann", "lastName": "Old", "emailAddress": "ann@old.example",
             "isAccountOwner": False, "deleteCode": 0},
            {"userId": INVITEES[1], "firstName": "Bob", "lastName": "Old", "emailAddress": "bob@old.example",
             "isAccountOwner": False, "deleteCode": 0},
            {"userId": GONE, "firstName": "Gone", "lastName": "Away", "isAccountOwner": False, "deleteCode": 1}]
        self.invitations = [
            {"invitationId": "inv-1", "userId": "user-pending-1", "emailAddress": "cat@old.example",
             "firstName": "Cat", "lastName": "Pending"},
            {"invitationId": "inv-staff", "userId": STAFF[2], "emailAddress": "alias3@example.test"}]
        self.items = {path: {} for path in self.LISTS}   # path -> id -> item
        self.invoices = {}                                # id -> {"deleted", "items": [(path, id)]}
        self.docs_matters = {}                            # id -> {"deleteCode", "parties": set()}
        self.contacts = {}                                # id -> {"deleteCode"}
        self.page_size = None
        self._previous_prospect()

    def _previous_prospect(self):
        matter, other = self._add_matter(), self._add_matter()
        party, loose = self._add_contact("Client of old matter"), self._add_contact("Attendee's contact")
        self.docs_matters[matter]["parties"].add(party)
        activities = [self._add_item("v1/activities", matter, STAFF[0]) for _ in range(5)]
        self._add_item("v1/activities", other, INVITEES[0])  # an attendee's own entry
        fee = self._add_item("v1/fees", matter, STAFF[1])
        cost = self._add_item("v1/cost-recoveries", matter, STAFF[1])
        self._add_invoice([("v1/activities", activities[0]), ("v1/activities", activities[1]), ("v1/fees", fee)])
        self._add_invoice([("v1/cost-recoveries", cost)])
        self.previous = {"matters": {matter, other}, "contacts": {party, loose}}

    def _add_matter(self, body=None):
        matter_id = str(uuid.uuid4())
        self.docs_matters[matter_id] = {"deleteCode": 0, "parties": set()}
        if body is not None:
            self.matters[matter_id] = body
        return matter_id

    def _add_contact(self, name):
        contact_id = str(uuid.uuid4())
        self.contacts[contact_id] = {"deleteCode": 0, "fullName": name}
        return contact_id

    def _add_item(self, path, matter_id, user_id):
        item_id = str(uuid.uuid4())
        self.items[path][item_id] = {self.LISTS[path][1]: item_id, "matterId": matter_id, "userId": user_id,
                                     "billed": False, "invoiceId": None, "rowVersion": "1", "isDeleted": False}
        return item_id

    def _add_invoice(self, refs):
        invoice_id = str(uuid.uuid4())
        self.invoices[invoice_id] = {"deleted": False, "items": refs}
        for path, item_id in refs:
            item = self.items[path][item_id]
            item.update(billed=True, invoiceId=invoice_id, rowVersion=str(int(item["rowVersion"]) + 1))
        return invoice_id

    # -- what's left

    def live(self, path):
        return [i for i in self.items[path].values() if not i["isDeleted"]]

    def live_users(self):
        return {u["userId"] for u in self.users if not u.get("deleteCode")}

    # -- transport

    def _failure(self, method, path):
        route = (method, "v1/" + path.split("/")[1])
        key = (method, path) if (method, path) in self.fail else route
        if key in self.fail:
            status, after = self.fail[key]
            if self.counts[key] >= after:
                return httpx.Response(status, json={"title": "injected failure"})
        self.counts[key] += 1
        return None

    def handle(self, request):
        host, method, path = request.url.host, request.method, request.url.path.removeprefix("/api/")
        service = host.split(".")[0]
        answer = getattr(self, f"_{service}", lambda *a: None)(request, method, path)
        if answer is None:
            return super().handle(request)
        self.requests.append(request)
        return self._failure(method, path) or answer()

    def _acctivity(self, request, method, path):
        body = json.loads(request.content) if request.content else None
        if method == "GET" and path in self.LISTS:
            def page():
                size = int(request.url.params.get("pageSize", 100))
                self.page_size = size
                start = int(request.url.params.get("offsetKey") or 0)
                rows = self.live(path)
                more = start + size < len(rows)
                return httpx.Response(200, json={self.LISTS[path][0]: rows[start:start + size],
                                                 "offsetKey": str(start + size) if more else None})
            return page
        if method == "POST" and path.endswith("/batch-delete") and path.removesuffix("/batch-delete") in self.LISTS:
            def batch_delete():
                items = self.items[path.removesuffix("/batch-delete")]
                for entry in body["items"]:
                    item = items.get(entry["id"])
                    if item is None:
                        return httpx.Response(404, json={"title": "not found"})
                    if item["rowVersion"] != entry["rowVersion"]:
                        return httpx.Response(409, json={"title": "row version conflict"})
                    if item["billed"] and not item["isDeleted"]:
                        return httpx.Response(409, json={"title": "billed item not editable"})
                for entry in body["items"]:
                    items[entry["id"]]["isDeleted"] = True
                return httpx.Response(200, json={"items": [{"id": e["id"], "rowVersion": "2"} for e in body["items"]]})
            return batch_delete
        if method == "DELETE" and path.startswith("v1/invoices/"):
            def delete_invoice():
                invoice = self.invoices.get(path.split("/")[2])
                if not invoice or invoice["deleted"]:
                    return httpx.Response(404)
                invoice["deleted"] = True
                for list_path, item_id in invoice["items"]:
                    item = self.items[list_path][item_id]
                    item.update(billed=False, invoiceId=None, rowVersion=str(int(item["rowVersion"]) + 1))
                return httpx.Response(204)
            return delete_invoice
        if method == "POST" and path in self.LISTS:  # the seeder creating items: keep them listable
            def create():
                item_id = self._add_item(path, body["matterId"], request.headers.get("x-userid"))
                return httpx.Response(201, json={self.LISTS[path][1]: item_id})
            return create
        if (method, path) == ("POST", "v1/invoices"):
            def invoice():
                refs = [("v1/activities", i) for i in body["activityIds"]] + \
                       [("v1/fees", i) for i in body["feeIds"]] + \
                       [("v1/cost-recoveries", i) for i in body["costRecoveryIds"]]
                return httpx.Response(201, json={"invoiceId": self._add_invoice(refs)})
            return invoice
        return None

    def _docscore(self, request, method, path):
        body = json.loads(request.content) if request.content else None
        parts = path.split("/")
        if (method, path) == ("GET", "v1/matters"):
            rows = [{"matterId": k, "deleteCode": v["deleteCode"], "fileNumber": "x"} for k, v in self.docs_matters.items()]
            return lambda: httpx.Response(200, json={"data": rows, "total": len(rows)})
        if (method, path) == ("POST", "v1/matters"):
            return lambda: httpx.Response(201, json={"matterId": self._add_matter(body)})
        if method == "PUT" and parts[:2] == ["v1", "matters"]:
            def save():
                self.docs_matters[parts[2]]["parties"] = {c["contactId"] for c in body["contacts"] if c["contactId"]}
                return httpx.Response(200, json={})
            return save
        if method == "PATCH" and len(parts) == 4 and parts[3] == "deleteCode":
            def delete_code():
                store = self.docs_matters if parts[1] == "matters" else self.contacts
                if parts[2] not in store:
                    return httpx.Response(404)
                if parts[1] == "contacts" and any(parts[2] in m["parties"] for m in self.docs_matters.values()
                                                  if not m["deleteCode"]):
                    return httpx.Response(422, json={"title": "contact is a party on a live matter"})
                store[parts[2]]["deleteCode"] = body["deleteCode"]
                return httpx.Response(204)
            return delete_code
        if (method, path) == ("GET", "v1/contacts"):
            rows = [{"contactId": k, **v} for k, v in self.contacts.items()]
            return lambda: httpx.Response(200, json={"data": rows, "total": len(rows)})
        if method == "PUT" and parts[:2] == ["v1", "contacts"]:
            def put_contact():
                self.contacts[parts[2]] = {"deleteCode": 0, "fullName": body.get("fullName")}
                return httpx.Response(200, json={})
            return put_contact
        return None

    def _management(self, request, method, path):
        body = json.loads(request.content) if request.content else None
        parts = path.split("/")
        if (method, path) == ("GET", "v1/invitations"):
            return lambda: httpx.Response(200, json=self.invitations)
        if method == "DELETE" and parts[:2] == ["v1", "invitations"]:
            def cancel():
                found = [i for i in self.invitations if i["invitationId"] == parts[2]
                         and i["userId"] == request.url.params.get("userId")]
                if not found:
                    return httpx.Response(404)
                self.invitations.remove(found[0])
                return httpx.Response(200)
            return cancel
        if method == "PATCH" and len(parts) == 4 and parts[1] == "users" and parts[3] == "deleteCode":
            def delete_user():
                user = next((u for u in self.users if u["userId"] == parts[2]), None)
                if not user:
                    return httpx.Response(404)
                user["deleteCode"] = body["deleteCode"]
                return httpx.Response(200)
            return delete_user
        return None


def app():
    application = FastAPI()
    application.include_router(demo_seeder.router)
    application.include_router(demo_reseed.router)
    return application


def write_paths(fake, *, start=0):
    """Every write after `start` as 'METHOD service path' (query string left off)."""
    return [f"{r.method} {r.url.host.split('.')[0]} {r.url.path.removeprefix('/api/')}"
            for r in fake.requests[start:] if r.method != "GET" and r.url.host != "auth.test.invalid"]


class ReseedTestCase(unittest.TestCase):
    def setUp(self):
        self.plan = generate(NZ_SNAPSHOT)
        self.fake = self.enterContext(FakeFirm())
        demo_logins.set_rep_token(rep_jwt(firm=FIRM, **{CLAIMS + "userId": REP}), "pasted")
        self.firm_id = add_firm_with_plan(self.plan)
        self.client = TestClient(app())

    def preview(self):
        return self.client.get(f"/api/v1/firms/{self.firm_id}/demo-reseed")

    def reseed(self, name=FIRM_NAME):
        return self.client.post(f"/api/v1/firms/{self.firm_id}/demo-reseed", json={"confirm_firm_name": name})

    def status(self):
        return self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").json()


class PreviewTests(ReseedTestCase):
    def test_lists_the_previous_invitees_and_what_the_wipe_will_remove_without_writing(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual((preview["demo_firm_id"], preview["firm_name"]), (FIRM, FIRM_NAME))
        self.assertEqual(sorted((i["name"], i["email"], i["kind"]) for i in preview["invitees"]), [
            ("Ann Old", "ann@old.example", "user"), ("Bob Old", "bob@old.example", "user"),
            ("Cat Pending", "cat@old.example", "invitation")])
        self.assertEqual(preview["counts"], {"matters": 2, "contacts": 2, "activities": 6, "fees": 1,
                                             "cost_recoveries": 1, "invoices": 2})
        self.assertEqual(write_paths(self.fake), [])

    def test_reads_every_page_of_a_long_list(self):
        for _ in range(2500):
            self.fake._add_item("v1/activities", next(iter(self.fake.previous["matters"])), STAFF[0])
        self.assertEqual(self.preview().json()["counts"]["activities"], 2506)
        self.assertEqual(self.fake.page_size, 1000)

    def test_refuses_a_firm_off_the_allowlist(self):
        with patch.dict(os.environ, {"DEMO_FIRM_ALLOWLIST": "firm-9999"}):
            response = self.preview()
        self.assertEqual(response.status_code, 403)
        self.assertIn("allowlist", response.text)

    def test_refuses_a_firm_that_is_not_a_test_firm(self):
        self.fake.is_test_firm = False
        response = self.preview()
        self.assertEqual(response.status_code, 403)
        self.assertIn("not a test firm", response.text)

    def test_needs_a_rep_login(self):
        demo_logins.reset()
        response = self.preview()
        self.assertEqual(response.status_code, 401)
        self.assertIn("rep login", response.text)

    def test_names_missing_settings(self):
        with patch.dict(os.environ, {"DEMO_FIRM_ID": ""}):
            response = self.preview()
        self.assertEqual(response.status_code, 503)
        self.assertIn("DEMO_FIRM_ID", response.text)


class ReseedTests(ReseedTestCase):
    def test_wipes_the_firm_removes_previous_invitees_then_seeds_the_plan(self):
        response = self.reseed()
        self.assertEqual(response.status_code, 202, response.text)
        status = self.status()
        self.assertEqual((status["kind"], status["status"]), ("reseed", "done"), status.get("error"))
        fake = self.fake

        # Nothing of the previous prospect is left; only what the new seed created.
        self.assertTrue(all(fake.docs_matters[m]["deleteCode"] == 1 for m in fake.previous["matters"]))
        self.assertTrue(all(fake.contacts[c]["deleteCode"] == 1 for c in fake.previous["contacts"]))
        new_matters = {m for m, v in fake.docs_matters.items() if not v["deleteCode"]}
        self.assertEqual(len(new_matters), len(self.plan["matters"]))
        self.assertEqual(len(fake.live("v1/activities")), len(self.plan["time_entries"]))
        self.assertTrue({i["matterId"] for i in fake.live("v1/activities")} <= new_matters)
        self.assertEqual(sum(1 for i in fake.invoices.values() if not i["deleted"]), len(self.plan["invoices"]))

        # Previous invitees are gone; the owner, the staff and the rep are untouched.
        self.assertEqual(fake.live_users(), {OWNER, *STAFF, REP})
        self.assertEqual(fake.invitations, [{"invitationId": "inv-staff", "userId": STAFF[2],
                                             "emailAddress": "alias3@example.test"}])
        touched = [r.url.path for r in fake.requests if r.method != "GET" and "/users/" in r.url.path
                   and r.url.path.endswith("deleteCode")]
        self.assertEqual(sorted(p.split("/")[-2] for p in touched), INVITEES)
        cancel = next(r for r in fake.requests if r.method == "DELETE" and "/invitations/" in r.url.path)
        self.assertEqual(cancel.url.params["userId"], "user-pending-1")

        # Progress: wipe steps first, then the seed steps.
        steps = {s["step"]: (s["done"], s["total"], s["failed"]) for s in status["steps"]}
        self.assertEqual([s["step"] for s in status["steps"]][:7], [
            "wipe_invoices", "wipe_activities", "wipe_fees", "wipe_cost_recoveries", "wipe_matters",
            "wipe_contacts", "wipe_invitees"])
        self.assertEqual(steps["wipe_invoices"], (2, 2, 0))
        self.assertEqual(steps["wipe_activities"], (6, 6, 0))
        self.assertEqual(steps["wipe_matters"], (2, 2, 0))
        self.assertEqual(steps["wipe_invitees"], (3, 3, 0))
        self.assertEqual(steps["matters"][:2], (len(self.plan["matters"]),) * 2)

    def test_deletes_in_order_invoices_items_matters_contacts_invitees_then_seeds(self):
        self.reseed()
        order = []
        for path in write_paths(self.fake):
            method, service, route = path.split(" ")
            parts = route.split("/")
            name = {"invoices": "invoices", "activities": "items", "fees": "items", "cost-recoveries": "items",
                    "matters": "matters", "contacts": "contacts", "users": "invitees",
                    "invitations": "invitees"}.get(parts[1], parts[1])
            if method == "DELETE" or route.endswith(("batch-delete", "deleteCode")):
                name = "delete " + name
            if not order or order[-1] != name:
                order.append(name)
        self.assertEqual(order[:5], ["delete invoices", "delete items", "delete matters", "delete contacts",
                                     "delete invitees"])
        self.assertNotIn("delete matters", order[5:])  # the seed never deletes
        self.assertEqual(order[5], "firm")             # then the seed: rename the firm first

    def test_batch_deletes_send_the_row_versions_read_after_the_invoices_went(self):
        self.reseed()
        batch = next(r for r in self.fake.requests if r.url.path.endswith("activities/batch-delete"))
        sent = {i["id"]: i["rowVersion"] for i in json.loads(batch.content)["items"]}
        self.assertEqual(len(sent), 6)
        self.assertEqual(sorted(sent.values()), ["1", "1", "1", "1", "3", "3"])  # billed twice-bumped

    def test_a_wrong_firm_name_writes_nothing(self):
        for name in ("alias firm", "Alias", "Brightwater Legal", ""):
            response = self.reseed(name)
            self.assertEqual(response.status_code, 422, name)
        self.assertEqual(write_paths(self.fake), [])
        self.assertEqual(self.client.get(f"/api/v1/firms/{self.firm_id}/demo-seed").status_code, 404)

    def test_the_typed_name_is_trimmed(self):
        self.assertEqual(self.reseed(f"  {FIRM_NAME} ").status_code, 202)

    def test_refuses_a_firm_off_the_allowlist_without_writing(self):
        with patch.dict(os.environ, {"DEMO_FIRM_ALLOWLIST": "firm-9999"}):
            self.assertEqual(self.reseed().status_code, 403)
        self.assertEqual(write_paths(self.fake), [])

    def test_refuses_a_firm_that_is_not_a_test_firm_without_writing(self):
        self.fake.is_test_firm = False
        response = self.reseed()
        self.assertEqual(response.status_code, 403)
        self.assertIn("not a test firm", response.text)
        self.assertEqual(write_paths(self.fake), [])

    def test_refuses_a_rep_login_for_another_firm(self):
        demo_logins.set_rep_token(rep_jwt(firm="firm-9999", **{CLAIMS + "userId": REP}), "pasted")
        self.assertEqual(self.reseed().status_code, 403)
        self.assertEqual(write_paths(self.fake), [])

    def test_refuses_without_the_reps_user_id_so_it_cannot_be_protected(self):
        demo_logins.set_rep_token(rep_jwt(firm=FIRM), "pasted")
        self.reseed()
        self.assertIn("user id", self.status()["error"])
        self.assertEqual(write_paths(self.fake), [])

    def test_never_removes_the_owner_staff_or_rep_even_listed_as_invitations(self):
        self.fake.invitations += [{"invitationId": "inv-owner", "userId": OWNER},
                                  {"invitationId": "inv-rep", "userId": REP.upper()}]
        self.reseed()
        self.assertEqual(self.status()["status"], "done", self.status()["error"])
        self.assertEqual({i["invitationId"] for i in self.fake.invitations}, {"inv-staff", "inv-owner", "inv-rep"})
        self.assertEqual(self.fake.live_users(), {OWNER, *STAFF, REP})

    def test_a_running_seed_is_a_409(self):
        self.reseed()
        with prospects.database() as db:
            db.execute("UPDATE seed_runs SET status='running'")
        writes = len(write_paths(self.fake))
        self.assertEqual(self.reseed().status_code, 409)
        self.assertEqual(self.client.post(f"/api/v1/firms/{self.firm_id}/demo-seed", json={}).status_code, 409)
        self.assertEqual(len(write_paths(self.fake)), writes)

    def test_another_prospects_run_into_the_same_demo_firm_is_a_409(self):
        with prospects.database() as db:
            other = db.execute("INSERT INTO firms(domain,name) VALUES('other.example.nz','Other')").lastrowid
            db.execute("""INSERT INTO demo_plans(firm_id,approval_id,plan,created_at,updated_at)
                          VALUES(?,1,?,'2026-11-19','2026-11-19')""", (other, json.dumps(self.plan)))
        self.client.post(f"/api/v1/firms/{other}/demo-seed", json={})
        with prospects.database() as db:
            db.execute("UPDATE seed_runs SET status='running' WHERE firm_id=?", (other,))
        self.assertEqual(self.reseed().status_code, 409)

    def test_no_plan_is_a_404(self):
        with prospects.database() as db:
            db.execute("DELETE FROM demo_plans")
        self.assertEqual(self.reseed().status_code, 404)
        self.assertEqual(write_paths(self.fake), [])

    def test_progress_reaches_the_live_updates_stream(self):
        self.reseed()
        state = live_updates.firm_state(self.firm_id)
        self.assertEqual((state["demo_seed"]["kind"], state["demo_seed"]["status"]), ("reseed", "done"))
        self.assertEqual(state["demo_seed"]["steps"][0]["step"], "wipe_invoices")


class ResumeTests(ReseedTestCase):
    def test_a_failed_wipe_resumes_by_relisting_and_never_deletes_twice(self):
        self.fake.fail[("PATCH", "v1/contacts")] = [500, 1]
        self.reseed()
        status = self.status()
        self.assertEqual((status["status"], status["step"]), ("failed", "wipe_contacts"))
        self.assertIsNone(status["wiped_at"])
        steps = {s["step"]: (s["done"], s["total"], s["failed"]) for s in status["steps"]}
        self.assertEqual(steps["wipe_contacts"], (1, 2, 1))
        self.assertEqual(steps["matters"][0], 0)  # the seed hasn't started

        del self.fake.fail[("PATCH", "v1/contacts")]
        before = len(self.fake.requests)
        self.assertEqual(self.client.post(f"/api/v1/firms/{self.firm_id}/demo-seed", json={}).status_code, 202)
        status = self.status()
        self.assertEqual((status["kind"], status["status"]), ("reseed", "done"), status.get("error"))
        later = write_paths(self.fake, start=before)
        self.assertFalse([p for p in later if "invoices/" in p or "batch-delete" in p or "matters/" in p
                          and p.endswith("deleteCode")])
        self.assertEqual(len([p for p in later if p.startswith("PATCH docscore v1/contacts/")]), 1)
        steps = {s["step"]: (s["done"], s["total"], s["failed"]) for s in status["steps"]}
        self.assertEqual(steps["wipe_contacts"], (2, 2, 0))
        self.assertEqual(self.fake.live_users(), {OWNER, *STAFF, REP})

    def test_a_reseed_that_failed_while_seeding_resumes_the_seed_without_wiping_again(self):
        self.fake.fail[("POST", "v1/activities")] = [500, 10]
        self.reseed()
        status = self.status()
        self.assertEqual((status["status"], status["step"]), ("failed", "time_entries"))
        self.assertIsNotNone(status["wiped_at"])

        del self.fake.fail[("POST", "v1/activities")]
        before = len(self.fake.requests)
        self.assertEqual(self.reseed().status_code, 202)  # re-POSTing the reseed resumes the same run
        status = self.status()
        self.assertEqual(status["status"], "done", status.get("error"))
        later = write_paths(self.fake, start=before)
        self.assertFalse([p for p in later if "batch-delete" in p or p.endswith("deleteCode")
                          or p.startswith("DELETE")])
        self.assertEqual(len(self.fake.live("v1/activities")), len(self.plan["time_entries"]))
        with prospects.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM seed_runs").fetchone()[0], 1)

    def test_a_billed_item_whose_invoice_is_gone_is_left_and_reported(self):
        stray = self.fake._add_item("v1/fees", next(iter(self.fake.previous["matters"])), STAFF[0])
        self.fake.items["v1/fees"][stray]["billed"] = True  # billed, but no invoice we can find
        self.reseed()
        status = self.status()
        self.assertEqual(status["status"], "done", status.get("error"))
        fees = next(s for s in status["steps"] if s["step"] == "wipe_fees")
        self.assertEqual((fees["done"], fees["failed"]), (1, 1))
        self.assertFalse(self.fake.items["v1/fees"][stray]["isDeleted"])


if __name__ == "__main__":
    unittest.main()
