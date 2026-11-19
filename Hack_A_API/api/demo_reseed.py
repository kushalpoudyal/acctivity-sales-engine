"""Demo reseed (issue #20): wipe the rep's demo firm, remove the previous prospect's invitees, then seed afresh.

Reseed steps 1-3 of context/TECHNICAL_DESIGN.md → Demo environment:
1. Warn: `GET /api/v1/firms/{id}/demo-reseed` lists who will lose access and how much will be wiped (reads only).
2. Wipe everything in the demo firm, including what attendees created, in this order:
   invoices (deleting one releases what it billed), time entries, fixed fees and disbursements (batch soft
   delete), Docs Core matters, then contacts (a contact that is a party on a live matter can't be deleted).
3. Remove the previous invitees: every active user except the account owner, the 6 staff (DEMO_STAFF_USER_IDS)
   and the rep's own user, and every pending invitation.
Then the seeder (demo_seeder.py) seeds the prospect's plan as a new run.

A reseed is a seed run with `kind = 'reseed'`: the wipe steps come first in its `steps`, and `wiped_at` is set once
the wipe has finished, so resuming a failed reseed only wipes again if the wipe hadn't finished (re-listing, so
nothing is deleted twice) and never wipes what the seed has since created. Only ids read from the firm are
deleted. Nothing is retried; the first failure stops the run.
"""
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import demo_seeder
import prospects
from demo_logins import DemoFirm, DemoLoginError, GuardRefused, _current_rep_login, rep_user_id
from demo_seeder import WIPE_STEPS, SeedError, demo_firm_id, staff_user_ids

# Acctivity lists: our name -> (route, response key, id field). Lists page by cursor (`offsetKey`), max 1000.
LISTS = {"activities": ("v1/activities", "activities", "activityId"),
         "fees": ("v1/fees", "fees", "feeId"),
         "cost_recoveries": ("v1/cost-recoveries", "costRecoveries", "costRecoveryId")}
PAGE_SIZE = 1000
BATCH_SIZE = 100


# ---------------------------------------------------------------- reading the firm

def _live(entry: dict) -> bool:
    return str(entry.get("deleteCode") or 0) == "0"


def _rows(data, *keys) -> list[dict]:
    """A list response, bare or wrapped (`{"data": [...]}`)."""
    if isinstance(data, dict):
        data = next((data[k] for k in (*keys, "data", "items") if isinstance(data.get(k), list)), [])
    return [row for row in data or [] if isinstance(row, dict)]


class FirmContents:
    """What is in the demo firm now, read with the rep login (Docs Core, Management) and the service login acting
    as the rep (Acctivity). Refuses up front if the guards do."""

    def __init__(self, firm: DemoFirm):
        self.firm = firm
        firm.check_writable()
        self.rep_user = rep_user_id()
        if not self.rep_user:
            raise GuardRefused("the rep login has no user id, so the rep's own user can't be protected.")
        self.rep, self.service = firm.rep(), firm.service(self.rep_user)

    def acctivity(self, kind: str) -> list[dict]:
        """Every live item of one kind in the firm (all users), following the cursor to the end."""
        path, key, _ = LISTS[kind]
        found, offset, seen = [], None, set()
        while True:
            params = {"pageSize": PAGE_SIZE, **({"offsetKey": offset} if offset else {})}
            page = self.service.get("acctivity", path, params=params) or {}
            found += [row for row in _rows(page, key) if not row.get("isDeleted")]
            offset = page.get("offsetKey") if isinstance(page, dict) else None
            if not offset or offset in seen:
                return found
            seen.add(offset)

    def matters(self) -> list[dict]:
        return [m for m in _rows(self.rep.get("docscore", "v1/matters"), "matters") if _live(m)]

    def contacts(self) -> list[dict]:
        return [c for c in _rows(self.rep.get("docscore", "v1/contacts"), "contacts") if _live(c)]

    def protected_users(self, users: list[dict]) -> set[str]:
        """Never removed: the account owner, the 6 staff and the rep."""
        ids = {u["userId"] for u in users if u.get("isAccountOwner")} | set(staff_user_ids()) | {self.rep_user}
        return {str(i).lower() for i in ids if i}

    def invitees(self) -> tuple[list[dict], list[dict]]:
        """(users, invitations) to remove: active users who aren't protected, and pending invitations."""
        users = _rows(self.rep.get("management", "v1/users"), "users")
        keep = self.protected_users(users)
        invitations = _rows(self.rep.get("management", "v1/invitations"), "invitations")
        return ([u for u in users if _live(u) and str(u.get("userId", "")).lower() not in keep],
                [i for i in invitations if str(i.get("userId", "")).lower() not in keep])


def _invoice_ids(items: dict[str, list[dict]]) -> set[str]:
    return {row["invoiceId"] for rows in items.values() for row in rows if row.get("billed") and row.get("invoiceId")}


def _person(entry: dict, kind: str) -> dict:
    name = " ".join(p for p in (entry.get("firstName"), entry.get("lastName")) if p) or entry.get("fullName")
    email = entry.get("emailAddress") or entry.get("email")
    return {"name": name or email, "email": email, "kind": kind,
            "id": entry.get("userId") if kind == "user" else entry.get("invitationId")}


def preview(firm: DemoFirm) -> dict:
    contents = FirmContents(firm)
    items = {kind: contents.acctivity(kind) for kind in LISTS}
    users, invitations = contents.invitees()
    return {"demo_firm_id": firm.firm_id, "firm_name": firm.details().get("firmName"),
            "invitees": [_person(u, "user") for u in users] + [_person(i, "invitation") for i in invitations],
            "counts": {"matters": len(contents.matters()), "contacts": len(contents.contacts()),
                       **{kind: len(rows) for kind, rows in items.items()}, "invoices": len(_invoice_ids(items))}}


# ---------------------------------------------------------------- the wipe

class Wiper:
    """Runs the wipe steps of one reseed run, recording each deleted id in seed_run_items."""

    def __init__(self, run_id: int):
        with prospects.database() as db:
            run = db.execute("SELECT * FROM seed_runs WHERE id=?", (run_id,)).fetchone()
        self.run_id, self.firm = run_id, DemoFirm(run["demo_firm_id"])

    def _done_count(self, step: str) -> int:
        with prospects.database() as db:
            return db.execute("SELECT count(*) FROM seed_run_items WHERE run_id=? AND step=? AND status='done'",
                              (self.run_id, step)).fetchone()[0]

    def _total(self, step: str, remaining: int) -> None:
        """A wipe step's total is what it already deleted plus what is left now."""
        with prospects.database() as db:
            row = db.execute("SELECT totals FROM seed_runs WHERE id=?", (self.run_id,)).fetchone()
            totals = json.loads(row["totals"])
            totals[step] = self._done_count(step) + remaining
            db.execute("UPDATE seed_runs SET totals=? WHERE id=?", (json.dumps(totals), self.run_id))

    def _one(self, step: str, key: str, send) -> None:
        try:
            send()
        except Exception as error:
            demo_seeder.record_item(self.run_id, step, key, key, "failed", str(error))
            raise
        demo_seeder.record_item(self.run_id, step, key, key, "done")

    def _protect(self, user_id: str) -> None:
        if str(user_id).lower() in self.protected:
            raise SeedError(f"Refused: user {user_id} is the owner, a staff account or the rep.")

    def run(self) -> None:
        self.contents = FirmContents(self.firm)
        for step, _ in WIPE_STEPS:
            demo_seeder.update_run(self.run_id, step=step)
            getattr(self, step)()

    def wipe_invoices(self):
        """Deleting an invoice soft-deletes it and releases what it billed, so those items can be deleted."""
        ids = sorted(_invoice_ids({kind: self.contents.acctivity(kind) for kind in LISTS}))
        self._total("wipe_invoices", len(ids))
        for invoice_id in ids:
            self._one("wipe_invoices", invoice_id, lambda: self.contents.service.delete(
                "acctivity", f"v1/invoices/{invoice_id}", json={"invoiceId": invoice_id}))

    def _batch_delete(self, step: str, kind: str):
        path, _, id_field = LISTS[kind]
        rows = self.contents.acctivity(kind)  # listed again: deleting invoices changed the row versions
        self._total(step, len(rows))
        # Still billed with no invoice we could delete: the all-or-nothing batch would refuse, so leave them.
        for row in [r for r in rows if r.get("billed")]:
            demo_seeder.record_item(self.run_id, step, row[id_field], row[id_field], "failed",
                                    "Billed, and its invoice could not be found to delete; left in place.")
        todo = [r for r in rows if not r.get("billed")]
        for start in range(0, len(todo), BATCH_SIZE):
            chunk = todo[start:start + BATCH_SIZE]
            body = {"items": [{"id": r[id_field], "rowVersion": str(r["rowVersion"])} for r in chunk]}
            try:
                self.contents.service.post("acctivity", f"{path}/batch-delete", json=body)
            except Exception as error:
                for r in chunk:
                    demo_seeder.record_item(self.run_id, step, r[id_field], r[id_field], "failed", str(error))
                raise
            for r in chunk:
                demo_seeder.record_item(self.run_id, step, r[id_field], r[id_field], "done")

    def wipe_activities(self):
        self._batch_delete("wipe_activities", "activities")

    def wipe_fees(self):
        self._batch_delete("wipe_fees", "fees")

    def wipe_cost_recoveries(self):
        self._batch_delete("wipe_cost_recoveries", "cost_recoveries")

    def _delete_code(self, step: str, rows: list[dict], id_field: str, route: str):
        self._total(step, len(rows))
        for row in rows:
            item_id = row[id_field]
            self._one(step, item_id, lambda: self.contents.rep.patch(
                "docscore", f"{route}/{item_id}/deleteCode", json={"deleteCode": 1}))

    def wipe_matters(self):
        self._delete_code("wipe_matters", self.contents.matters(), "matterId", "v1/matters")

    def wipe_contacts(self):
        self._delete_code("wipe_contacts", self.contents.contacts(), "contactId", "v1/contacts")

    def wipe_invitees(self):
        users, invitations = self.contents.invitees()
        self.protected = self.contents.protected_users(_rows(self.contents.rep.get("management", "v1/users")))
        self._total("wipe_invitees", len(users) + len(invitations))
        for user in users:
            user_id = user["userId"]
            self._protect(user_id)
            self._one("wipe_invitees", f"user:{user_id}", lambda: self.contents.rep.patch(
                "management", f"v1/users/{user_id}/deleteCode", json={"deleteCode": 1}))
        for invitation in invitations:
            invitation_id, user_id = invitation.get("invitationId"), invitation.get("userId")
            if not invitation_id or not user_id:
                raise SeedError("Refused: a pending invitation came back without its invitation or user id.")
            self._protect(user_id)
            self._one("wipe_invitees", f"invitation:{invitation_id}", lambda: self.contents.rep.delete(
                "management", f"v1/invitations/{invitation_id}", params={"userId": user_id}))


# ---------------------------------------------------------------- API

router = APIRouter(prefix="/api/v1/firms", tags=["Demo seed"])


def _open_demo_firm() -> DemoFirm:
    """Settings (503), environment and allowlist (403), a current rep login (401), then every write guard (403)."""
    firm = DemoFirm(demo_firm_id())
    staff_user_ids()
    _current_rep_login()
    firm.check_writable()
    return firm


def _http(error: Exception) -> HTTPException:
    return HTTPException(getattr(error, "status_code", 500), str(error))


@router.get("/{firm_id}/demo-reseed")
def get_reseed(firm_id: int):
    """Read-only warning before a reseed: the demo firm's name (to type back), the previous invitees who will lose
    access, and how many items the wipe will delete."""
    prospects.get_firm(firm_id)
    try:
        return preview(_open_demo_firm())
    except (SeedError, DemoLoginError) as error:
        raise _http(error) from None


class ReseedRequest(BaseModel):
    confirm_firm_name: str


@router.post("/{firm_id}/demo-reseed", status_code=202)
def start_reseed(firm_id: int, body: ReseedRequest, tasks: BackgroundTasks):
    """Wipe the demo firm and seed this prospect's plan into it, as a background job. The rep must type the demo
    firm's current name. Resumes this prospect's latest reseed if it failed. Progress via GET .../demo-seed."""
    try:
        firm = _open_demo_firm()
    except (SeedError, DemoLoginError) as error:
        raise _http(error) from None
    with prospects.database() as db:
        plan = db.execute("SELECT plan FROM demo_plans WHERE firm_id=?", (firm_id,)).fetchone()
    if not plan:
        raise HTTPException(404, "No demo plan for this firm yet.")
    demo_seeder.refuse_if_running(firm_id, firm.firm_id)
    if body.confirm_firm_name.strip() != str(firm.details().get("firmName") or "").strip():
        raise HTTPException(422, "The firm name typed doesn't match the demo firm's name. Nothing was changed.")
    with prospects.database() as db:
        latest = db.execute("SELECT * FROM seed_runs WHERE firm_id=? ORDER BY id DESC LIMIT 1", (firm_id,)).fetchone()
        now = demo_seeder.now()
        if latest and latest["status"] == "failed" and latest["kind"] == "reseed" \
                and latest["demo_firm_id"] == firm.firm_id:
            run_id = latest["id"]
            db.execute("UPDATE seed_runs SET status='queued',error=NULL,updated_at=? WHERE id=?", (now, run_id))
        else:
            totals = {step: 0 for step, _ in WIPE_STEPS} | demo_seeder.plan_totals(json.loads(plan["plan"]))
            run_id = db.execute("""INSERT INTO seed_runs(firm_id,demo_firm_id,plan,totals,status,kind,created_at,
                                   updated_at) VALUES(?,?,?,?,'queued','reseed',?,?)""",
                                (firm_id, firm.firm_id, plan["plan"], json.dumps(totals), now, now)).lastrowid
    tasks.add_task(demo_seeder.run_seed, run_id)
    return demo_seeder.latest_run(firm_id)
