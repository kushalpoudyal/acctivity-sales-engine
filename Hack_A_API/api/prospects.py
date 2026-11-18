import json
import hashlib
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import openai
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from decks import DeckError, validate_pptx
from documents import render_pptx, write_copy
from firm_research import RECORD_KINDS, FactKind, ResearchError, crawl_site, download_logo, extract_profile
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/firms", tags=["Prospects"])
DB_PATH = Path(__file__).with_name("prospects.sqlite3")
SNAPSHOT_VERSION = 2
# Fields a rep can edit per record table; list values are stored as JSON text.
EDITABLE = {
    "facts": ("kind", "title", "detail"),
    "practice_areas": ("name", "description", "services"),
    "people": ("full_name", "position", "biography", "qualifications", "public_work_email", "public_work_phone"),
    "offices": ("name", "address", "phone", "email"),
}
REQUIRED = {"facts": ("title", "detail"), "practice_areas": ("name", "description"),
            "people": ("full_name",), "offices": ("name",)}
JSON_COLUMNS = {"services", "qualifications"}


@contextmanager
def database():
    with closing(sqlite3.connect(DB_PATH, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        yield db


def initialize():
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS firms (
            id INTEGER PRIMARY KEY, domain TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new', brief TEXT NOT NULL DEFAULT '[]',
            error TEXT, researched_at TEXT)""")
        db.execute("""UPDATE firms SET status='failed', error='Research interrupted. Run it again.'
                      WHERE status='researching'""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(firms)")}
        for column, default in [("research_meta", "'{}'"), ("research_pages", "'[]'")]:
            if column not in columns:
                db.execute(f"ALTER TABLE firms ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
        if "preferred_logo_id" not in columns:
            db.execute("ALTER TABLE firms ADD COLUMN preferred_logo_id INTEGER")
        db.execute("""CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, digest TEXT NOT NULL,
            snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'approved',
            error TEXT, pptx BLOB, UNIQUE(firm_id,digest))""")
        db.execute("UPDATE proposals SET status='failed',error='Build interrupted; retry.' WHERE status='building'")
        db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
            url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL)""")
        # Every research record cites the saved page and verbatim passage it came from.
        # original holds the extracted values once a rep edits the record.
        source = "page_id INTEGER NOT NULL REFERENCES pages(id), evidence TEXT NOT NULL, original TEXT"
        db.execute(f"""CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
            kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL, {source})""")
        db.execute(f"""CREATE TABLE IF NOT EXISTS practice_areas (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
            name TEXT NOT NULL, description TEXT NOT NULL, services TEXT NOT NULL DEFAULT '[]', {source},
            UNIQUE(firm_id,name))""")
        db.execute(f"""CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
            full_name TEXT NOT NULL, position TEXT, biography TEXT, qualifications TEXT NOT NULL DEFAULT '[]',
            public_work_email TEXT, public_work_phone TEXT, {source}, UNIQUE(firm_id,full_name))""")
        db.execute("""CREATE TABLE IF NOT EXISTS person_practice_areas (
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            practice_area_id INTEGER NOT NULL REFERENCES practice_areas(id) ON DELETE CASCADE,
            PRIMARY KEY(person_id,practice_area_id))""")
        db.execute(f"""CREATE TABLE IF NOT EXISTS offices (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
            name TEXT NOT NULL, address TEXT, phone TEXT, email TEXT, {source})""")
        # Pages from earlier versions were saved as JSON on the firm; move them to the pages table
        # so they can be re-extracted without crawling again.
        for firm in db.execute("SELECT id,research_pages FROM firms WHERE research_pages!='[]'").fetchall():
            for page in json.loads(firm["research_pages"]):
                db.execute("INSERT INTO pages(firm_id,url,title,text) VALUES(?,?,?,?)",
                           (firm["id"], page["url"], page["title"], page["text"]))
            db.execute("UPDATE firms SET research_pages='[]' WHERE id=?", (firm["id"],))
        db.execute("""CREATE TABLE IF NOT EXISTS brand_assets (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, source_url TEXT NOT NULL,
            mime_type TEXT NOT NULL, content BLOB NOT NULL,
            UNIQUE(firm_id,source_url))""")
        db.execute("""CREATE TABLE IF NOT EXISTS output_jobs (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, approval_id INTEGER NOT NULL,
            request_key TEXT NOT NULL, request_digest TEXT NOT NULL,
            snapshot TEXT NOT NULL, instructions TEXT NOT NULL, formats TEXT NOT NULL,
            logo BLOB, logo_mime TEXT, status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT NOT NULL DEFAULT 'Queued', error TEXT, container_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(firm_id,request_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS output_files (
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, format TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', error TEXT, content BLOB,
            UNIQUE(job_id,format))""")
        db.execute("""UPDATE output_files SET status='failed',error='Build interrupted; start a new run.'
                      WHERE job_id IN (SELECT id FROM output_jobs WHERE status IN ('queued','running'))
                      AND status!='ready'""")
        db.execute("""UPDATE output_jobs SET status='failed',stage='Interrupted',
                      error='Build interrupted by server restart. Saved files remain available.'
                      WHERE status IN ('queued','running')""")


class Intake(BaseModel):
    website: str = Field(min_length=1, max_length=2048)
    display_name: str = Field(default="", max_length=160)


def firm_domain(raw: str) -> str:
    raw = raw.strip()
    try:
        url = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (url.hostname or "").encode("idna").decode().lower().removeprefix("www.")
        if (url.scheme not in {"https", "http"} or url.username is not None
                or url.password is not None or url.port is not None
                or not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
                or host.endswith((".local", ".localhost", ".internal"))):
            raise ValueError
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Enter a public website domain, without credentials or a port.") from None
    return host


@router.get("")
def list_firms():
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT id, domain, name, status FROM firms ORDER BY id DESC")]


def load_records(db, firm_id: int) -> dict[str, list[dict]]:
    """Research records per table, each with its source page; people carry linked practice area ids."""
    links = {}
    for link in db.execute("""SELECT l.person_id,l.practice_area_id FROM person_practice_areas l
                              JOIN people p ON p.id=l.person_id WHERE p.firm_id=? ORDER BY l.practice_area_id""",
                           (firm_id,)):
        links.setdefault(link["person_id"], []).append(link["practice_area_id"])
    records = {}
    for kind in RECORD_KINDS:
        records[kind] = []
        for row in db.execute(f"""SELECT r.*,p.url AS source_url,p.title AS source_title FROM {kind} r
                                  JOIN pages p ON p.id=r.page_id WHERE r.firm_id=? ORDER BY r.id""", (firm_id,)):
            record = {key: row[key] for key in row.keys()
                      if key not in ("firm_id", "page_id", "source_url", "source_title")}
            for column in JSON_COLUMNS & record.keys():
                record[column] = json.loads(record[column])
            record["original"] = json.loads(row["original"]) if row["original"] else None
            record["source"] = {"url": row["source_url"], "title": row["source_title"]}
            if kind == "people":
                record["practice_area_ids"] = links.get(row["id"], [])
            records[kind].append(record)
    return records


@router.get("/{firm_id}")
def get_firm(firm_id: int):
    with database() as db:
        row = db.execute("""SELECT id,domain,name,status,error,researched_at,research_meta,preferred_logo_id
                            FROM firms WHERE id=?""", (firm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Firm not found.")
        records = load_records(db, firm_id)
        pages_saved = db.execute("SELECT count(*) FROM pages WHERE firm_id=?", (firm_id,)).fetchone()[0]
        proposals = [{**dict(p), "snapshot": json.loads(p["snapshot"])} for p in db.execute(
            """SELECT id,status,error,snapshot FROM proposals
               WHERE firm_id=? AND json_extract(snapshot,'$.version')=? ORDER BY id DESC""",
            (firm_id, SNAPSHOT_VERSION))]
        assets = [dict(p) for p in db.execute(
            "SELECT id,source_url,mime_type FROM brand_assets WHERE firm_id=?", (firm_id,))]
        outputs = [dict(p) for p in db.execute(
            """SELECT id,approval_id,instructions,formats,status,stage,error,created_at
               FROM output_jobs WHERE firm_id=? ORDER BY id DESC""", (firm_id,))]
        for job in outputs:
            job["formats"] = json.loads(job["formats"])
            job["files"] = [dict(p) for p in db.execute(
                "SELECT id,format,status,error,length(content) AS size FROM output_files WHERE job_id=?",
                (job["id"],))]
    return {**dict(row), "research_meta": json.loads(row["research_meta"]), "records": records,
            "brief_token": revision_token(row, records), "pages_saved": pages_saved,
            "proposals": proposals, "brand_assets": assets, "outputs": outputs}


@router.get("/{firm_id}/brand-assets/{asset_id}")
def brand_asset(firm_id: int, asset_id: int):
    with database() as db:
        row = db.execute("SELECT mime_type,content FROM brand_assets WHERE id=? AND firm_id=?",
                         (asset_id, firm_id)).fetchone()
    if not row:
        raise HTTPException(404, "Brand asset not found.")
    return Response(content=row["content"], media_type=row["mime_type"],
                    headers={"X-Content-Type-Options": "nosniff"})


@router.get("/{firm_id}/proposals/{proposal_id}/demo-export")
def demo_export(firm_id: int, proposal_id: int):
    with database() as db:
        row = db.execute("""SELECT snapshot FROM proposals
                            WHERE id=? AND firm_id=? AND json_extract(snapshot,'$.version')=?""",
                         (proposal_id, firm_id, SNAPSHOT_VERSION)).fetchone()
        logos = [dict(asset) for asset in db.execute(
            "SELECT id,source_url,mime_type FROM brand_assets WHERE firm_id=?", (firm_id,))]
    if not row:
        raise HTTPException(404, "Approved brief not found.")
    snapshot = json.loads(row["snapshot"])
    payload = {"schema_version": 2, "mode": "demo_staging", "send_invitations": False,
               "firm": {"name": snapshot["firm"], "website": "https://" + snapshot["domain"]},
               "staff": snapshot["people"], "practice_areas": snapshot["practice_areas"],
               "offices": snapshot["offices"], "facts": snapshot["facts"],
               "branding": [{**asset, "download_path": f"/api/v1/firms/{firm_id}/brand-assets/{asset['id']}"}
                            for asset in logos]}
    return Response(content=json.dumps(payload, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="firm-{firm_id}-demo.json"'})


def digest(text: str):
    return hashlib.sha256(text.encode()).hexdigest()


def revision_token(firm, records: dict) -> str:
    return digest(json.dumps([firm["name"], firm["preferred_logo_id"], records]))


class FactEdit(BaseModel):
    id: int
    kind: FactKind
    title: str = Field(max_length=200)
    detail: str = Field(max_length=12000)


class PracticeAreaEdit(BaseModel):
    id: int
    name: str = Field(max_length=200)
    description: str = Field(max_length=12000)
    services: list[str] = Field(default=[], max_length=100)


class PersonEdit(BaseModel):
    id: int
    full_name: str = Field(max_length=320)
    position: str | None = Field(default=None, max_length=320)
    biography: str | None = Field(default=None, max_length=8000)
    qualifications: list[str] = Field(default=[], max_length=50)
    public_work_email: str | None = Field(default=None, max_length=320)
    public_work_phone: str | None = Field(default=None, max_length=320)
    practice_area_ids: list[int] = []


class OfficeEdit(BaseModel):
    id: int
    name: str = Field(max_length=200)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=320)
    email: str | None = Field(default=None, max_length=320)


class BriefEdit(BaseModel):
    brief_token: str
    display_name: str = Field(min_length=1, max_length=160)
    logo_id: int | None = None
    facts: list[FactEdit] = Field(default=[], max_length=250)
    practice_areas: list[PracticeAreaEdit] = Field(default=[], max_length=250)
    people: list[PersonEdit] = Field(default=[], max_length=250)
    offices: list[OfficeEdit] = Field(default=[], max_length=250)


def cleaned(value):
    if isinstance(value, list):
        return [item.strip() for item in value if item.strip()]
    if isinstance(value, str):
        return value.strip() or None
    return value


@router.put("/{firm_id}/brief")
def edit_brief(firm_id: int, body: BriefEdit):
    if not body.display_name.strip() or any(
        not (getattr(item, field) or "").strip()
        for kind, fields in REQUIRED.items() for item in getattr(body, kind) for field in fields
    ):
        raise HTTPException(422, "Firm name, record names, titles and descriptions cannot be blank.")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if not firm:
            raise HTTPException(404, "Firm not found.")
        records = load_records(db, firm_id)
        if firm["status"] == "researching" or revision_token(firm, records) != body.brief_token:
            raise HTTPException(409, "Research changed or is running. Refresh before editing.")
        for kind in RECORD_KINDS:
            if sorted(item.id for item in getattr(body, kind)) != sorted(r["id"] for r in records[kind]):
                raise HTTPException(422, "Save the complete brief. Uncheck records to exclude them from approval.")
        if body.logo_id is not None and not db.execute(
            "SELECT id FROM brand_assets WHERE id=? AND firm_id=?", (body.logo_id, firm_id)
        ).fetchone():
            raise HTTPException(422, "Choose a saved logo belonging to this firm.")
        for kind, field in (("people", "full_name"), ("practice_areas", "name")):
            names = [getattr(item, field).strip().casefold() for item in getattr(body, kind)]
            if len(names) != len(set(names)):
                raise HTTPException(422, "People and practice area names must be unique.")
        area_ids = {area["id"] for area in records["practice_areas"]}
        if any(not set(person.practice_area_ids) <= area_ids for person in body.people):
            raise HTTPException(422, "Link people only to this firm's practice areas.")
        for kind in RECORD_KINDS:
            current = {record["id"]: record for record in records[kind]}
            for item in getattr(body, kind):
                old = current[item.id]
                values = {field: cleaned(getattr(item, field)) for field in EDITABLE[kind]}
                if kind == "people":
                    values["practice_area_ids"] = sorted(set(item.practice_area_ids))
                before = {field: old[field] for field in values}
                if values == before:
                    continue
                columns = {field: json.dumps(value) if field in JSON_COLUMNS else value
                           for field, value in values.items() if field != "practice_area_ids"}
                db.execute(f"UPDATE {kind} SET {', '.join(f'{column}=?' for column in columns)},original=? WHERE id=?",
                           (*columns.values(), json.dumps(old["original"] or before), item.id))
                if kind == "people":
                    db.execute("DELETE FROM person_practice_areas WHERE person_id=?", (item.id,))
                    db.executemany("INSERT INTO person_practice_areas(person_id,practice_area_id) VALUES(?,?)",
                                   [(item.id, area_id) for area_id in values["practice_area_ids"]])
        db.execute("UPDATE firms SET name=?,preferred_logo_id=? WHERE id=?",
                   (body.display_name.strip(), body.logo_id, firm_id))
    return get_firm(firm_id)


class Approval(BaseModel):
    brief_token: str
    facts: list[int] = []
    practice_areas: list[int] = []
    people: list[int] = []
    offices: list[int] = []


@router.post("/{firm_id}/approvals")
def approve(firm_id: int, body: Approval):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if not firm:
            raise HTTPException(404, "Firm not found.")
        records = load_records(db, firm_id)
        if revision_token(firm, records) != body.brief_token or firm["status"] == "researching":
            raise HTTPException(409, "Research changed or is running. Refresh and review the records again.")
        area_names = {area["id"]: area["name"] for area in records["practice_areas"]}
        chosen = {}
        for kind in RECORD_KINDS:
            ids = set(getattr(body, kind))
            if not ids <= {record["id"] for record in records[kind]}:
                raise HTTPException(422, "Select records from the current brief.")
            chosen[kind] = []
            for record in records[kind]:
                if record["id"] in ids:
                    record = dict(record)
                    record["rep_edited"] = record.pop("original") is not None
                    if kind == "people":
                        record["practice_areas"] = [area_names[i] for i in record.pop("practice_area_ids")]
                    chosen[kind].append(record)
        if not any(chosen.values()):
            raise HTTPException(422, "Select at least one record to approve.")
        snapshot = json.dumps({"version": SNAPSHOT_VERSION, "brief_token": body.brief_token,
                               "firm": firm["name"], "domain": firm["domain"], **chosen})
        token = digest(snapshot)
        db.execute("INSERT OR IGNORE INTO proposals(firm_id,digest,snapshot) VALUES(?,?,?)",
                   (firm_id, token, snapshot))
        return dict(db.execute("SELECT id,status FROM proposals WHERE firm_id=? AND digest=?",
                               (firm_id, token)).fetchone())


def build_deck(proposal_id: int, snapshot: str):
    try:
        approved = json.loads(snapshot)
        data = render_pptx(approved, write_copy(approved, ""), None)
        validate_pptx(data)
    except (DeckError, openai.OpenAIError) as exc:
        message = str(exc) if isinstance(exc, DeckError) else "OpenAI build failed. Check model access and quota."
        with database() as db:
            db.execute("UPDATE proposals SET status='failed',error=? WHERE id=?", (message, proposal_id))
        return
    with database() as db:
        db.execute("UPDATE proposals SET status='ready',pptx=? WHERE id=?", (data, proposal_id))


@router.post("/{firm_id}/proposals/{proposal_id}/build", status_code=202)
def start_build(firm_id: int, proposal_id: int, tasks: BackgroundTasks, body: dict):
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "Deck generation is not configured.")
    with database() as db:
        row = db.execute("""UPDATE proposals SET status='building',error=NULL
                            WHERE id=? AND firm_id=? AND status IN ('approved','failed')
                            AND json_extract(snapshot,'$.version')=?
                            RETURNING snapshot""", (proposal_id, firm_id, SNAPSHOT_VERSION)).fetchone()
        if not row:
            raise HTTPException(409, "No buildable approval. Refresh to see the current state.")
    tasks.add_task(build_deck, proposal_id, row["snapshot"])
    return {"id": proposal_id, "status": "building"}


@router.get("/{firm_id}/proposals/{proposal_id}/download")
def download(firm_id: int, proposal_id: int):
    with database() as db:
        row = db.execute("SELECT pptx FROM proposals WHERE id=? AND firm_id=? AND status='ready'",
                         (proposal_id, firm_id)).fetchone()
    if not row:
        raise HTTPException(404, "No completed deck for this approval.")
    return Response(content=row["pptx"],
                    media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    headers={"Content-Disposition": f'attachment; filename="proposal-{proposal_id}.pptx"'})


@router.post("", status_code=201)
def add_firm(body: Intake):
    domain = firm_domain(body.website)
    with database() as db:
        row = db.execute("""INSERT INTO firms(domain,name) VALUES (?,?)
                            ON CONFLICT(domain) DO NOTHING RETURNING id""",
                         (domain, body.display_name.strip() or domain)).fetchone()
        if not row:
            raise HTTPException(409, "This website is already in the directory.")
    return get_firm(row["id"])


def cited_passages(response, domain):
    if response.status != "completed" or not any(
        item.type == "web_search_call" and item.status == "completed" for item in response.output
    ):
        return []
    passages = []
    for msg in response.output:
        if msg.type != "message":
            continue
        for part in msg.content:
            if part.type != "output_text":
                continue
            start = 0
            for line in part.text.splitlines(keepends=True):
                end = start + len(line)
                links = {}
                for a in part.annotations:
                    if a.type != "url_citation" or not start <= a.start_index < end:
                        continue
                    try:
                        u = urlsplit(a.url)
                        host = u.hostname or ""
                    except ValueError:
                        continue
                    if (u.scheme in {"http", "https"} and u.username is None and
                            (host == domain or host.endswith("." + domain))):
                        links[a.url] = a.title or host
                if links:
                    passages.append({"text": line.strip(), "sources": [
                        {"url": url, "title": title} for url, title in links.items()]})
                start = end
    return passages


def store_research(db, firm_id: int, pages: list[dict], records: dict[str, list[dict]]):
    """Replace the firm's saved pages and research records in one transaction."""
    db.execute("DELETE FROM person_practice_areas WHERE person_id IN (SELECT id FROM people WHERE firm_id=?)",
               (firm_id,))
    for table in (*RECORD_KINDS, "pages"):
        db.execute(f"DELETE FROM {table} WHERE firm_id=?", (firm_id,))
    page_ids = {page["id"]: db.execute("INSERT INTO pages(firm_id,url,title,text) VALUES(?,?,?,?) RETURNING id",
                                       (firm_id, page["url"], page["title"], page["text"])).fetchone()[0]
                for page in pages}

    def insert(table: str, record: dict, fields: tuple[str, ...]) -> int:
        values = [json.dumps(record[f]) if f in JSON_COLUMNS else record[f] for f in fields]
        return db.execute(f"""INSERT INTO {table}(firm_id,{','.join(fields)},page_id,evidence)
                              VALUES(?,{','.join('?' * len(fields))},?,?) RETURNING id""",
                          (firm_id, *values, page_ids[record["page_id"]], record["evidence"])).fetchone()[0]

    for fact in records["facts"]:
        insert("facts", fact, EDITABLE["facts"])
    areas = {}
    for area in records["practice_areas"]:
        if area["name"].casefold() not in areas:
            areas[area["name"].casefold()] = insert("practice_areas", area, EDITABLE["practice_areas"])
    seen = set()
    for person in records["people"]:
        if person["full_name"].casefold() in seen:
            continue
        seen.add(person["full_name"].casefold())
        person_id = insert("people", person, EDITABLE["people"])
        linked = {areas[name.casefold()] for name in person["practice_areas"] if name.casefold() in areas}
        db.executemany("INSERT INTO person_practice_areas(person_id,practice_area_id) VALUES(?,?)",
                       [(person_id, area_id) for area_id in sorted(linked)])
    for office in records["offices"]:
        insert("offices", office, EDITABLE["offices"])


def research(firm_id: int, domain: str, reuse_pages: bool = False):
    def progress(count):
        with database() as db:
            db.execute("UPDATE firms SET research_meta=? WHERE id=?",
                       (json.dumps({"stage": "Reading website pages", "pages_read": count}), firm_id))

    pages, warnings, logos, records = [], [], [], {}
    try:
        if reuse_pages:
            with database() as db:
                pages = [{"id": number, "url": row["url"], "title": row["title"], "text": row["text"]}
                         for number, row in enumerate(db.execute(
                             "SELECT url,title,text FROM pages WHERE firm_id=? ORDER BY id", (firm_id,)), 1)]
            if not pages:
                raise ResearchError("No saved pages to re-extract. Run website research first.")
        else:
            pages, warnings = crawl_site(domain, progress)
        with database() as db:
            db.execute("UPDATE firms SET research_meta=? WHERE id=?",
                       (json.dumps({"stage": "Extracting firm profile", "pages_read": len(pages)}), firm_id))
        with openai.OpenAI(timeout=180, max_retries=0) as client:
            records, rejected = extract_profile(
                client, os.getenv("OPENAI_RESEARCH_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-6.1-sol",
                [{key: page[key] for key in ("id", "url", "title", "text")} for page in pages])
        if rejected:
            warnings.append(f"{rejected} records or fields omitted because supporting evidence could not be verified.")
        for url in dict.fromkeys(url for page in pages for url in page.get("logos", [])):
            try:
                logos.append(download_logo(url, domain))
            except ResearchError as exc:
                warnings.append(f"Logo {url}: {exc}")
        status = "ready" if any(records.values()) else "needs_review"
        error = None if status == "ready" else "No evidence-backed records returned. Review the website or retry."
    except ResearchError as exc:
        status, records, error = "failed", {}, str(exc)
    except openai.OpenAIError:
        status, records, error = "failed", {}, "OpenAI research failed. Check model access, quota and connectivity."
    with database() as db:
        if any(records.values()):
            store_research(db, firm_id, pages, records)
            db.execute("UPDATE firms SET researched_at=CURRENT_TIMESTAMP WHERE id=?", (firm_id,))
            # A re-extraction reads saved pages only, so it keeps the logos from the last crawl.
            if not reuse_pages:
                db.execute("UPDATE firms SET preferred_logo_id=NULL WHERE id=?", (firm_id,))
                db.execute("DELETE FROM brand_assets WHERE firm_id=?", (firm_id,))
                for logo in logos:
                    db.execute("INSERT INTO brand_assets(firm_id,source_url,mime_type,content) VALUES(?,?,?,?)",
                               (firm_id, logo["source_url"], logo["mime_type"], logo["content"]))
        db.execute("UPDATE firms SET status=?, error=? WHERE id=?", (status, error, firm_id))
        db.execute("UPDATE firms SET research_meta=? WHERE id=?",
                   (json.dumps({"stage": status, "pages_read": len(pages), "warnings": warnings,
                                "pages": [{"url": p["url"], "title": p["title"]} for p in pages]}), firm_id))


class ResearchRequest(BaseModel):
    reuse_pages: bool = False


@router.post("/{firm_id}/research", status_code=202)
def start_research(firm_id: int, tasks: BackgroundTasks, body: ResearchRequest):
    firm = get_firm(firm_id)
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "Research is not configured on the server.")
    if body.reuse_pages and not firm["pages_saved"]:
        raise HTTPException(409, "No saved pages to re-extract. Run website research first.")
    with database() as db:
        if not db.execute("UPDATE firms SET status='researching',error=NULL,research_meta='{}' WHERE id=? AND status!='researching'",
                          (firm_id,)).rowcount:
            raise HTTPException(409, "Research is already running.")
    tasks.add_task(research, firm_id, firm["domain"], body.reuse_pages)
    return {"id": firm_id, "status": "researching"}
