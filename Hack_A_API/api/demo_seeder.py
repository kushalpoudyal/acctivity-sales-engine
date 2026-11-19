"""Demo seeder (issue #19): writes a prospect's demo plan into the rep's Acctivity test firm.

Reseed steps 4-7 of context/TECHNICAL_DESIGN.md → Demo environment, in this order:
rename the firm and the staff (rep login), set staff rates (service login as each staff member), create matters
(service login), client contacts (rep login), time entries / fixed fees / disbursements / invoices (service login
as the staff member), and finally put each matter's description, responsible lawyer and client contact on it
(rep login; Docs Core ignores the description on create).

Two ids: the *prospect* firm is our local int id (where the plan lives); the *demo* firm is the rep's Acctivity
test firm GUID (DEMO_FIRM_ID). Plan staff slot N is the Nth id in DEMO_STAFF_USER_IDS.

Every plan item's external id is recorded in `seed_run_items` as it is created, so a failed run resumes where it
stopped and skips what already exists. The seeder only sends user ids it read from the firm's user list, matter
type ids it read from the firm's Docs Core tree, and matter/activity/fee/cost-recovery ids it created in this run
(Acctivity does not check that they exist). Nothing is retried: the first failure stops the run.
"""
import json
import logging
import os
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import demo_plan
import prospects
from demo_logins import DemoFirm, DemoLoginError, GuardRefused

logger = logging.getLogger(__name__)

STEPS = [
    ("firm_name", "Rename the firm"),
    ("staff_names", "Rename the staff"),
    ("rates", "Set staff rates"),
    ("matters", "Create matters"),
    ("contacts", "Create client contacts"),
    ("time_entries", "Create time entries"),
    ("fees", "Create fixed fees"),
    ("disbursements", "Create disbursements"),
    ("invoices", "Create invoices"),
    ("matter_details", "Add descriptions and clients to matters"),
]
# A reseed (demo_reseed.py) wipes the demo firm first; its run reports these steps before STEPS.
WIPE_STEPS = [
    ("wipe_invoices", "Delete invoices"),
    ("wipe_activities", "Delete time entries"),
    ("wipe_fees", "Delete fixed fees"),
    ("wipe_cost_recoveries", "Delete disbursements"),
    ("wipe_matters", "Delete matters"),
    ("wipe_contacts", "Delete contacts"),
    ("wipe_invitees", "Remove previous invitees"),
]
GST = {"NZ": 0.15, "AU": 0.10}
TIMEZONES = {"NZ": "Pacific/Auckland", "AU": "Australia/Sydney"}
AU_TIMEZONES = {"VIC": "Australia/Melbourne", "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide",
                "WA": "Australia/Perth", "TAS": "Australia/Hobart", "NT": "Australia/Darwin"}
DAY_START = time(9, 0)
# Management field limits (UpdateFirmNameDTO, UpdateUserRequestDTO).
FIRM_NAME_MAX, PERSON_NAME_MAX = 80, 25
FILE_NUMBER_MAX = 100
DEFAULT_CONCURRENCY = 4


class SeedError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------- settings

def demo_firm_id() -> str:
    firm_id = os.getenv("DEMO_FIRM_ID", "").strip()
    if not firm_id:
        raise SeedError("DEMO_FIRM_ID (the rep's Acctivity test firm) is not set in the server .env.", 503)
    return firm_id


def staff_user_ids() -> list[str]:
    ids = [item.strip() for item in os.getenv("DEMO_STAFF_USER_IDS", "").split(",") if item.strip()]
    if not ids:
        raise SeedError("DEMO_STAFF_USER_IDS (the 6 staff alias user ids, in slot order) is not set in the "
                        "server .env.", 503)
    return ids


def concurrency() -> int:
    try:
        return min(8, max(1, int(os.getenv("DEMO_SEED_CONCURRENCY", DEFAULT_CONCURRENCY))))
    except ValueError:
        return DEFAULT_CONCURRENCY


# ---------------------------------------------------------------- matter types (provider for demo_plan)

def flatten_matter_types(nodes: list, path: tuple = ()) -> list[dict]:
    """Docs Core precedent tree → live `MatterType` leaves with their folder path. Deleted nodes are skipped
    along with everything under them."""
    leaves = []
    for node in nodes or []:
        if str(node.get("deleteCode") or "0") != "0":
            continue
        here = (*path, node.get("name"))
        if node.get("type") == "MatterType":
            leaves.append({"id": node["id"], "name": node.get("name"), "path": list(here)})
        leaves += flatten_matter_types(node.get("children") or [], here)
    return leaves


def _read_matter_types(firm: DemoFirm) -> list[dict]:
    return flatten_matter_types(firm.rep().get("docscore", "v1/precedent-tree/matter-types") or [])


def docs_core_matter_types(country: str, state: str | None) -> list[dict]:
    """The demo firm's matter types, read with the rep login (the tree follows the login's country, so
    `country`/`state` only matter to the caller's plan)."""
    try:
        return _read_matter_types(DemoFirm(demo_firm_id()))
    except (SeedError, DemoLoginError) as error:
        raise demo_plan.MatterTypesUnavailable(f"Docs Core matter types unavailable: {error}") from None


def register_matter_type_provider() -> None:
    demo_plan.set_matter_type_provider(docs_core_matter_types)


# ---------------------------------------------------------------- storage

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def plan_totals(plan: dict) -> dict:
    matters = len(plan["matters"])
    return {"firm_name": 1, "staff_names": len(plan["staff"]), "rates": len(plan["staff"]), "matters": matters,
            "contacts": matters, "time_entries": len(plan["time_entries"]), "fees": len(plan["fees"]),
            "disbursements": len(plan["disbursements"]), "invoices": len(plan["invoices"]),
            "matter_details": matters}


def _summary(db, row) -> dict:
    counts = {(r["step"], r["status"]): r["n"] for r in db.execute(
        "SELECT step,status,count(*) AS n FROM seed_run_items WHERE run_id=? GROUP BY step,status", (row["id"],))}
    totals = json.loads(row["totals"])
    steps = WIPE_STEPS + STEPS if row["kind"] == "reseed" else STEPS
    return {"id": row["id"], "firm_id": row["firm_id"], "kind": row["kind"], "status": row["status"],
            "step": row["step"], "error": row["error"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "finished_at": row["finished_at"], "wiped_at": row["wiped_at"],
            "steps": [{"step": step, "label": label, "total": totals.get(step, 0),
                       "done": counts.get((step, "done"), 0), "failed": counts.get((step, "failed"), 0)}
                      for step, label in steps]}


def latest_run(firm_id: int) -> dict | None:
    """The prospect firm's most recent seed run with per-step progress, or None."""
    with prospects.database() as db:
        row = db.execute("SELECT * FROM seed_runs WHERE firm_id=? ORDER BY id DESC LIMIT 1", (firm_id,)).fetchone()
        return _summary(db, row) if row else None


def update_run(run_id: int, **fields) -> None:
    fields["updated_at"] = now()
    with prospects.database() as db:
        db.execute(f"UPDATE seed_runs SET {','.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), run_id))


def record_item(run_id: int, step: str, key: str, external_id: str | None, status: str,
                error: str | None = None) -> dict:
    """Upsert one item's outcome in seed_run_items."""
    item = {"run_id": run_id, "step": step, "item_key": key, "external_id": external_id, "status": status,
            "error": error, "updated_at": now()}
    with prospects.database() as db:
        db.execute("""INSERT INTO seed_run_items(run_id,step,item_key,external_id,status,error,updated_at)
                      VALUES(:run_id,:step,:item_key,:external_id,:status,:error,:updated_at)
                      ON CONFLICT(run_id,step,item_key) DO UPDATE SET external_id=excluded.external_id,
                      status=excluded.status,error=excluded.error,updated_at=excluded.updated_at""", item)
    return item


def refuse_if_running(firm_id: int, demo_firm: str) -> None:
    """409 while a seed or reseed runs for this prospect, or for any prospect into the same demo firm."""
    with prospects.database() as db:
        running = db.execute("""SELECT 1 FROM seed_runs WHERE status IN ('queued','running')
                                AND (firm_id=? OR demo_firm_id=?)""", (firm_id, demo_firm)).fetchone()
    if running:
        raise HTTPException(409, "A demo seed or reseed is already in progress for this demo firm.")


# ---------------------------------------------------------------- the seeder

def _timestamps(entries: list[dict], zone: ZoneInfo) -> dict[str, str]:
    """Local start times: each staff member's day starts at 9:00 and runs through their entries in plan order."""
    starts, clock = {}, {}
    for entry in sorted(entries, key=lambda e: e["key"]):
        day = date.fromisoformat(entry["date"])
        at = clock.get((entry["staff"], day), datetime.combine(day, DAY_START, zone))
        starts[entry["key"]] = at.isoformat(timespec="seconds")
        clock[(entry["staff"], day)] = at + timedelta(hours=entry["hours"])
    return starts


class Seeder:
    def __init__(self, run_id: int):
        with prospects.database() as db:
            run = db.execute("SELECT * FROM seed_runs WHERE id=?", (run_id,)).fetchone()
            self.items = {(r["step"], r["item_key"]): dict(r) for r in db.execute(
                "SELECT * FROM seed_run_items WHERE run_id=?", (run_id,))}
        self.run_id, self.plan = run_id, json.loads(run["plan"])
        self.firm = DemoFirm(run["demo_firm_id"])
        self.matters = {m["key"]: m for m in self.plan["matters"]}

    # -- bookkeeping

    def _record(self, step: str, key: str, external_id: str | None, status: str, error: str | None = None):
        self.items[(step, key)] = record_item(self.run_id, step, key, external_id, status, error)

    def _done(self, step: str, key: str) -> bool:
        return self.items.get((step, key), {}).get("status") == "done"

    def created(self, step: str, key: str) -> str:
        """An id this run created; refuses anything else."""
        item = self.items.get((step, key))
        if not item or item["status"] != "done" or not item["external_id"]:
            raise SeedError(f"Refused: {step} {key} was not created by this seed run.")
        return item["external_id"]

    def staff_id(self, slot: int) -> str:
        if slot not in self.staff_ids:
            raise SeedError(f"Refused: staff slot {slot} has no user read from the firm.")
        return self.staff_ids[slot]

    def _create(self, step: str, key: str, send) -> str:
        """Run one create unless it already happened; record the outcome."""
        if self._done(step, key):
            return self.items[(step, key)]["external_id"]
        try:
            external_id = send()
        except Exception as error:
            self._record(step, key, self.items.get((step, key), {}).get("external_id"), "failed", str(error))
            raise
        self._record(step, key, external_id, "done")
        return external_id

    def _create_many(self, step: str, items: list[dict], send) -> None:
        """Creates with bounded concurrency. The first failure stops new requests; requests already in flight
        finish and are recorded, then the failure is raised."""
        todo = [item for item in items if not self._done(step, item["key"])]
        failure, running = None, {}
        with ThreadPoolExecutor(max_workers=concurrency()) as pool:
            while (todo and failure is None) or running:
                while todo and failure is None and len(running) < concurrency():
                    item = todo.pop(0)
                    running[pool.submit(send, item)] = item
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    item = running.pop(future)
                    try:
                        self._record(step, item["key"], future.result(), "done")
                    except Exception as error:
                        self._record(step, item["key"], None, "failed", str(error))
                        failure = failure or error
        if failure:
            raise failure

    # -- checks before any write

    def preflight(self) -> None:
        self.firm.check_writable()
        details = self.firm.details()
        self.country = str(details.get("countryCode") or "").upper()
        region = self.plan["region"]
        if region["country"] != self.country:
            raise SeedError(f"Refused: the plan is for {region['country']} but the demo firm is in {self.country}; "
                            "its matter types and rates would not fit.")
        self.industry = details.get("industryCategoryId") or 1
        state = region.get("state") if self.country == "AU" else None
        self.state = state
        self.zone = ZoneInfo(AU_TIMEZONES.get(state, TIMEZONES["AU"]) if self.country == "AU"
                             else TIMEZONES.get(self.country, "UTC"))

        users = {str(u.get("userId", "")).lower(): u for u in self.firm.rep().get("management", "v1/users") or []}
        configured = staff_user_ids()
        self.staff_ids = {}
        for member in self.plan["staff"]:
            slot = member["slot"]
            if slot > len(configured):
                raise SeedError(f"Refused: staff slot {slot} has no user id in DEMO_STAFF_USER_IDS.")
            user = users.get(configured[slot - 1].lower())
            if not user or str(user.get("deleteCode") or 0) != "0":
                raise SeedError(f"Refused: the user for staff slot {slot} is not an active user of the demo firm.")
            if user.get("isAccountOwner"):
                raise SeedError(f"Refused: the user for staff slot {slot} is the firm's account owner.")
            self.staff_ids[slot] = user["userId"]

        type_ids = {leaf["id"] for leaf in _read_matter_types(self.firm)}
        for matter in self.plan["matters"]:
            if matter["matter_type"]["id"] not in type_ids:
                raise SeedError(f"Refused: matter {matter['key']}'s type ({matter['matter_type']['name']}) is not "
                                "in the demo firm's Docs Core matter types.")

    # -- steps

    def firm_name(self):
        name = (self.plan["firm"].get("name") or "Demo Firm")[:FIRM_NAME_MAX]
        self._create("firm_name", "firm", lambda: (
            self.firm.rep().patch("management", "v1/firm/name", json={"firmName": name}), self.firm.firm_id)[1])

    def staff_names(self):
        for member in self.plan["staff"]:
            user_id = self.staff_id(member["slot"])
            first = member.get("first_name") or member["full_name"].split()[0]
            last = member.get("last_name") or " ".join(member["full_name"].split()[1:])
            body = {"firstName": first[:PERSON_NAME_MAX], "lastName": last[:PERSON_NAME_MAX]}
            self._create("staff_names", f"S{member['slot']}", lambda: (
                self.firm.rep().patch("management", f"v1/users/{user_id}", json=body), user_id)[1])

    def rates(self):
        for member in self.plan["staff"]:
            user_id = self.staff_id(member["slot"])
            self._create("rates", f"S{member['slot']}", lambda: self._set_rate(user_id, member["rate"]))

    def _set_rate(self, user_id: str, rate: float) -> str:
        client = self.firm.service(user_id)
        try:
            current = client.get("acctivity", "v1/users/settings")
        except DemoLoginError as error:
            if error.upstream_status != 404:
                raise
            current = None
        if current is None:  # no settings row yet
            client.post("acctivity", "v1/users/settings", json={"billingRatePerHour": rate})
        else:
            row_version = current.get("rowversion", current.get("rowVersion"))
            client.patch("acctivity", "v1/users/settings",
                         content=json.dumps([{"op": "replace", "path": "/billingRatePerHour", "value": rate}]),
                         headers={"RowVersion": str(row_version), "Content-Type": "application/json-patch+json"})
        return user_id

    def matters_step(self):
        for number, matter in enumerate(self.plan["matters"], 1):
            matter_type = {"matterTypeId": matter["matter_type"]["id"],
                           "matterTypeName": matter["matter_type"]["name"]}
            if self.state:
                matter_type["matterTypeState"] = self.state
            body = {"fileNumber": f"{self.run_id}-{number:02d} {matter['description']}"[:FILE_NUMBER_MAX],
                    "description": matter["description"], "matterType": matter_type,
                    "countryCode": self.country, "industryCategoryId": self.industry}
            client = self.firm.service(self.staff_id(matter["responsible"]))
            self._create("matters", matter["key"],
                         lambda: (client.post("docscore", "v1/matters", json=body) or {})["matterId"])

    def contacts(self):
        for matter in self.plan["matters"]:
            key = matter["key"]
            if self._done("contacts", key):
                continue
            # We choose the id, and keep it before sending, so a resumed run upserts the same contact.
            contact_id = self.items.get(("contacts", key), {}).get("external_id") or str(uuid.uuid4())
            self._record("contacts", key, contact_id, "pending")
            body = self._contact(matter["client"])
            self._create("contacts", key, lambda: (
                self.firm.rep().put("docscore", f"v1/contacts/{contact_id}", json=body), contact_id)[1])

    @staticmethod
    def _contact(client: dict) -> dict:
        empty = {"addresses": [], "customFields": [], "emailAddresses": [], "labels": [], "memories": [],
                 "phoneNumbers": []}
        if client["kind"] == "person":
            return {"type": "Person", "firstName": client["first_name"], "lastName": client["last_name"],
                    "fullName": client["name"], **empty}
        return {"type": "Organisation", "fullName": client["name"],
                "organisationType": "Business" if client["kind"] == "company" else "Trust", **empty}

    def time_entries(self):
        starts = _timestamps(self.plan["time_entries"], self.zone)

        def send(entry):
            body = {"matterId": self.created("matters", entry["matter"]), "timestamp": starts[entry["key"]],
                    "description": entry["description"], "elapsedSeconds": round(entry["hours"] * 3600),
                    "amount": entry["amount"], "inclusiveTax": False}
            client = self.firm.service(self.staff_id(entry["staff"]))
            return (client.post("acctivity", "v1/activities", json=body) or {})["activityId"]
        self._create_many("time_entries", self.plan["time_entries"], send)

    def fees(self):
        def send(fee):
            body = {"matterId": self.created("matters", fee["matter"]), "date": fee["date"],
                    "description": fee["description"], "quantity": 1, "amountEach": fee["amount"],
                    "inclusiveTax": False}
            client = self.firm.service(self.staff_id(fee["staff"]))
            return (client.post("acctivity", "v1/fees", json=body) or {})["feeId"]
        self._create_many("fees", self.plan["fees"], send)

    def disbursements(self):
        rate = GST.get(self.country, 0)

        def send(item):
            amount = item["amount"]
            tax = round(amount * rate, 2)
            body = {"matterId": self.created("matters", item["matter"]), "date": item["date"],
                    "description": item["description"], "quantity": 1, "amountExTax": amount, "amountTax": tax,
                    "amountIncTax": round(amount + tax, 2), "displayIncTax": False}
            client = self.firm.service(self.staff_id(self.matters[item["matter"]]["responsible"]))
            return (client.post("acctivity", "v1/cost-recoveries", json=body) or {})["costRecoveryId"]
        self._create_many("disbursements", self.plan["disbursements"], send)

    def invoices(self):
        for invoice in self.plan["invoices"]:
            body = {"activityIds": [self.created("time_entries", k) for k in invoice["time_entries"]],
                    "feeIds": [self.created("fees", k) for k in invoice["fees"]],
                    "costRecoveryIds": [self.created("disbursements", k) for k in invoice["disbursements"]]}
            if not any(body.values()):
                body["matterId"] = self.created("matters", invoice["matter"])
            client = self.firm.service(self.staff_id(self.matters[invoice["matter"]]["responsible"]))
            self._create("invoices", invoice["key"],
                         lambda: (client.post("acctivity", "v1/invoices", json=body) or {})["invoiceId"])

    def matter_details(self):
        for matter in self.plan["matters"]:
            matter_id = self.created("matters", matter["key"])
            contact_id = self.created("contacts", matter["key"])
            self._create("matter_details", matter["key"], lambda: self._save_matter(matter, matter_id, contact_id))

    def _save_matter(self, matter: dict, matter_id: str, contact_id: str) -> str:
        """Read the matter back and save it with the description, the responsible lawyer and the client contact
        in the matter type's client slot (SaveMatterDTO; everything else is kept as read)."""
        rep = self.firm.rep()
        current = rep.get("docscore", f"v1/matters/{matter_id}") or {}
        slots = [{"contactId": c.get("contactId"), "tableId": c.get("tableId"),
                  "tableDescription": c.get("tableDescription") or "Client", "displaySequence": c.get("displaySequence", n)}
                 for n, c in enumerate(current.get("contacts") or []) if not c.get("deleteCode")]
        if not slots:
            params = {"state": self.state} if self.state else None
            template = rep.get("docscore", f"v1/content/mattertype/{matter['matter_type']['id']}/contacts",
                               params=params) or {}
            slots = [{"contactId": None, "tableId": c.get("tableId"), "tableDescription": c.get("tableDescription"),
                      "displaySequence": c.get("order", n)} for n, c in enumerate(template.get("contacts") or [])]
        empty = [s for s in slots if not s["contactId"]]
        slot = next((s for s in empty if "client" in (s["tableDescription"] or "").lower()), None) or \
            (empty[0] if empty else None)
        if slot is None:
            slot = {"contactId": None, "tableId": None, "tableDescription": "Client", "displaySequence": len(slots)}
            slots.append(slot)
        slot["contactId"] = contact_id
        responsible = self.staff_id(matter["responsible"])
        body = {
            "acl": current.get("acl") or {"enabled": False, "userIds": []}, "contacts": slots,
            "countryCode": current.get("countryCode") or self.country,
            "criticalDates": current.get("criticalDates") or [], "customFields": current.get("customFields") or [],
            "definableTables": current.get("definableTables") or [], "description": matter["description"],
            "fileNumber": current.get("fileNumber"),
            "industryCategoryId": current.get("industryCategoryId") or self.industry,
            "memories": current.get("memories") or [], "state": current.get("state"),
            "status": current.get("status") or "InProgress", "statusText": current.get("statusText"),
            "substitution": current.get("substitution"),
            "userActing": current.get("userActing") or responsible, "userActingAccess": True,
            "userAssisting": current.get("userAssisting"),
            "userAssistingAccess": bool(current.get("userAssistingAccess")),
            "userCredit": current.get("userCredit"), "userCreditAccess": bool(current.get("userCreditAccess")),
            "userResponsible": responsible, "userResponsibleAccess": True,
        }
        rep.put("docscore", f"v1/matters/{matter_id}", json=body)
        return matter_id

    def run(self) -> None:
        self.preflight()
        for step, _ in STEPS:
            update_run(self.run_id, step=step)
            getattr(self, "matters_step" if step == "matters" else step)()


def run_seed(run_id: int) -> None:
    """The background job: run (or resume) one seed run to the end or the first failure. A reseed run wipes the
    demo firm first, unless its wipe already finished (then it never wipes again: that would delete the seed)."""
    update_run(run_id, status="running", error=None, finished_at=None)
    try:
        with prospects.database() as db:
            run = db.execute("SELECT kind,wiped_at FROM seed_runs WHERE id=?", (run_id,)).fetchone()
        if run["kind"] == "reseed" and not run["wiped_at"]:
            import demo_reseed  # here, not at the top: demo_reseed imports this module
            demo_reseed.Wiper(run_id).run()
            update_run(run_id, wiped_at=now())
        Seeder(run_id).run()
    except (SeedError, DemoLoginError) as error:
        update_run(run_id, status="failed", error=str(error), finished_at=now())
        return
    except Exception as error:  # a bug or an unexpected response shape; keep the run resumable
        logger.exception("Demo seed run %s failed", run_id)
        update_run(run_id, status="failed", error=f"Unexpected error: {error!r}", finished_at=now())
        return
    update_run(run_id, status="done", step=None, finished_at=now())


# ---------------------------------------------------------------- API

router = APIRouter(prefix="/api/v1/firms", tags=["Demo seed"])


class SeedRequest(BaseModel):
    # Start a new run even though an earlier one finished or failed. Otherwise a failed run (seed or reseed) is
    # resumed and a finished one is not repeated. To wipe first, use POST .../demo-reseed (demo_reseed.py).
    fresh: bool = False


@router.post("/{firm_id}/demo-seed", status_code=202)
def start_seed(firm_id: int, tasks: BackgroundTasks, body: SeedRequest | None = None):
    """Seed the demo firm from this prospect's demo plan as a background job; progress via GET and the live
    updates stream."""
    body = body or SeedRequest()
    try:
        firm = demo_firm_id()
        staff_user_ids()
        DemoFirm(firm)  # environment and allowlist guards, before anything is queued
    except (SeedError, DemoLoginError) as error:
        raise HTTPException(error.status_code, str(error)) from None
    with prospects.database() as db:
        plan = db.execute("SELECT plan FROM demo_plans WHERE firm_id=?", (firm_id,)).fetchone()
    if not plan:
        raise HTTPException(404, "No demo plan for this firm yet.")
    refuse_if_running(firm_id, firm)
    with prospects.database() as db:
        latest = db.execute("SELECT * FROM seed_runs WHERE firm_id=? ORDER BY id DESC LIMIT 1", (firm_id,)).fetchone()
        if latest and not body.fresh and latest["status"] == "done":
            raise HTTPException(409, "This plan is already seeded. Reseed (wipe first) to seed again.")
        stamp = now()
        if latest and not body.fresh:  # failed: resume it
            if latest["demo_firm_id"] != firm:
                raise HTTPException(409, "The failed run was for another demo firm; start a fresh run.")
            run_id = latest["id"]
            db.execute("UPDATE seed_runs SET status='queued',error=NULL,updated_at=? WHERE id=?", (stamp, run_id))
        else:
            plan_data = json.loads(plan["plan"])
            run_id = db.execute("""INSERT INTO seed_runs(firm_id,demo_firm_id,plan,totals,status,created_at,updated_at)
                                   VALUES(?,?,?,?,'queued',?,?)""",
                                (firm_id, firm, plan["plan"], json.dumps(plan_totals(plan_data)), stamp, stamp)).lastrowid
    tasks.add_task(run_seed, run_id)
    return latest_run(firm_id)


@router.get("/{firm_id}/demo-seed")
def get_seed(firm_id: int):
    run = latest_run(firm_id)
    if not run:
        raise HTTPException(404, "This firm has not been seeded yet.")
    return run
