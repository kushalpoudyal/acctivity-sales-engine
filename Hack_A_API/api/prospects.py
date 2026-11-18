import json
import hashlib
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import apollo
import openai
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from decks import DeckError, generate_deck
from firm_research import Person, ResearchError, crawl_site, download_logo, extract_profile
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/firms", tags=["Prospects"])
DB_PATH = Path(__file__).with_name("prospects.sqlite3")


@contextmanager
def database():
    with closing(sqlite3.connect(DB_PATH, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
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
        for column, definition in [("research_meta", "TEXT NOT NULL DEFAULT '{}'"),
                                   ("research_pages", "TEXT NOT NULL DEFAULT '[]'"),
                                   ("apollo_org", "TEXT"), ("apollo_enriched_at", "TEXT")]:
            if column not in columns:
                db.execute(f"ALTER TABLE firms ADD COLUMN {column} {definition}")
        if "preferred_logo_id" not in columns:
            db.execute("ALTER TABLE firms ADD COLUMN preferred_logo_id INTEGER")
        db.execute("""CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, digest TEXT NOT NULL,
            snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'approved',
            error TEXT, pptx BLOB, UNIQUE(firm_id,digest))""")
        db.execute("UPDATE proposals SET status='failed',error='Build interrupted; retry.' WHERE status='building'")
        db.execute("""CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, full_name TEXT NOT NULL,
            position TEXT, public_work_email TEXT, public_work_phone TEXT,
            profile TEXT NOT NULL, source_url TEXT NOT NULL, evidence TEXT NOT NULL,
            UNIQUE(firm_id,full_name))""")
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


@router.get("/{firm_id}")
def get_firm(firm_id: int):
    with database() as db:
        row = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        proposals = [{**dict(p), "snapshot": json.loads(p["snapshot"])} for p in db.execute(
            "SELECT id,status,error,snapshot FROM proposals WHERE firm_id=? ORDER BY id DESC", (firm_id,))]
        staff = [{**dict(p), "profile": json.loads(p["profile"])} for p in db.execute(
            "SELECT * FROM staff WHERE firm_id=? ORDER BY full_name", (firm_id,))]
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
    if not row:
        raise HTTPException(404, "Firm not found.")
    values = dict(row)
    values.pop("research_pages")
    return {**values, "research_meta": json.loads(row["research_meta"]),
            "apollo_org": json.loads(row["apollo_org"]) if row["apollo_org"] else None,
            "apollo_configured": apollo.configured(),
            "brief": json.loads(row["brief"]), "brief_token": revision_token(row),
            "proposals": proposals, "staff": staff, "brand_assets": assets, "outputs": outputs}


class ApolloEnrich(BaseModel):
    refresh: bool = False


@router.post("/{firm_id}/apollo/enrich")
def apollo_enrich(firm_id: int, body: ApolloEnrich | None = None):
    """Apollo organization enrichment: spends 1 credit. Re-enriching needs {"refresh": true}."""
    firm = get_firm(firm_id)
    if not firm["apollo_configured"]:
        raise HTTPException(503, apollo.NOT_CONFIGURED)
    if firm["apollo_org"] is not None and not (body and body.refresh):
        raise HTTPException(409, f"Already enriched on {firm['apollo_enriched_at']} UTC. "
                                 'Send {"refresh": true} to spend 1 more credit.')
    try:
        org = apollo.enrich_org(firm["domain"])
    except apollo.ApolloError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    if not org:
        raise HTTPException(404, f"Apollo has no organization for {firm['domain']}.")
    with database() as db:
        db.execute("UPDATE firms SET apollo_org=?, apollo_enriched_at=CURRENT_TIMESTAMP WHERE id=?",
                   (json.dumps(org), firm_id))
    return get_firm(firm_id)


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
        row = db.execute("SELECT snapshot FROM proposals WHERE id=? AND firm_id=?",
                         (proposal_id, firm_id)).fetchone()
        logos = [dict(asset) for asset in db.execute(
            "SELECT id,source_url,mime_type FROM brand_assets WHERE firm_id=?", (firm_id,))]
    if not row:
        raise HTTPException(404, "Approved brief not found.")
    snapshot = json.loads(row["snapshot"])
    people = {}
    for finding in snapshot["findings"]:
        if finding.get("person"):
            people[finding["person"]["full_name"].casefold()] = {
                **finding["person"], "sources": finding["sources"]}
    payload = {"schema_version": 1, "mode": "demo_staging", "send_invitations": False,
               "firm": {"name": snapshot["firm"], "website": "https://" + snapshot["domain"]},
               "staff": list(people.values()), "findings": snapshot["findings"],
               "branding": [{**asset, "download_path": f"/api/v1/firms/{firm_id}/brand-assets/{asset['id']}"}
                            for asset in logos]}
    return Response(content=json.dumps(payload, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="firm-{firm_id}-demo.json"'})


def digest(text: str):
    return hashlib.sha256(text.encode()).hexdigest()


def revision_token(firm):
    return digest(json.dumps([firm["name"], firm["brief"], firm["preferred_logo_id"]]))


def replace_staff(db, firm_id: int, findings: list[dict]):
    db.execute("DELETE FROM staff WHERE firm_id=?", (firm_id,))
    for finding in findings:
        if person := finding.get("person"):
            db.execute("""INSERT INTO staff(firm_id,full_name,position,public_work_email,
                          public_work_phone,profile,source_url,evidence) VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(firm_id,full_name) DO UPDATE SET
                          position=excluded.position, public_work_email=excluded.public_work_email,
                          public_work_phone=excluded.public_work_phone, profile=excluded.profile,
                          source_url=excluded.source_url, evidence=excluded.evidence""",
                       (firm_id, person["full_name"], person["position"], person["public_work_email"],
                        person["public_work_phone"], json.dumps(person),
                        finding["sources"][0]["url"], finding.get("evidence", "")))


class FindingEdit(BaseModel):
    index: int = Field(ge=0)
    title: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=12000)
    person: Person | None = None


class BriefEdit(BaseModel):
    brief_token: str
    display_name: str = Field(min_length=1, max_length=160)
    logo_id: int | None = None
    findings: list[FindingEdit] = Field(max_length=250)


@router.put("/{firm_id}/brief")
def edit_brief(firm_id: int, body: BriefEdit):
    if not body.display_name.strip() or any(not f.text.strip() for f in body.findings):
        raise HTTPException(422, "Firm name and finding descriptions cannot be blank.")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Firm not found.")
        if row["status"] == "researching" or revision_token(row) != body.brief_token:
            raise HTTPException(409, "Research changed or is running. Refresh before editing.")
        original = json.loads(row["brief"])
        if sorted(f.index for f in body.findings) != list(range(len(original))):
            raise HTTPException(422, "Save the complete brief. Uncheck findings to exclude them from approval.")
        if body.logo_id is not None and not db.execute(
            "SELECT id FROM brand_assets WHERE id=? AND firm_id=?", (body.logo_id, firm_id)
        ).fetchone():
            raise HTTPException(422, "Choose a saved logo belonging to this firm.")
        names = [f.person.full_name.strip().casefold() for f in body.findings if f.person]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise HTTPException(422, "Staff names must be nonempty and unique.")
        edited = []
        for item in sorted(body.findings, key=lambda f: f.index):
            old = original[item.index]
            changes = {"title": item.title.strip(), "text": item.text.strip()}
            if old.get("person"):
                if item.person is None:
                    raise HTTPException(422, "A staff finding must keep its staff profile.")
                changes["person"] = item.person.model_dump()
            elif item.person:
                raise HTTPException(422, "Staff profiles can only be edited on existing staff findings.")
            if any(old.get(key, "") != value for key, value in changes.items()):
                changes["rep_edited"] = True
                changes["original"] = old.get("original", {
                    key: old.get(key) for key in ("title", "text", "person")})
            edited.append({**old, **changes})
        db.execute("UPDATE firms SET name=?,brief=?,preferred_logo_id=? WHERE id=?",
                   (body.display_name.strip(), json.dumps(edited), body.logo_id, firm_id))
        replace_staff(db, firm_id, edited)
    return get_firm(firm_id)


class Approval(BaseModel):
    brief_token: str
    findings: list[int] = Field(min_length=1)


@router.post("/{firm_id}/approvals")
def approve(firm_id: int, body: Approval):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if not firm:
            raise HTTPException(404, "Firm not found.")
        if revision_token(firm) != body.brief_token or firm["status"] == "researching":
            raise HTTPException(409, "Research changed or is running. Refresh and review the findings again.")
        findings = json.loads(firm["brief"])
        indices = sorted(set(body.findings))
        if min(indices) < 0 or max(indices) >= len(findings):
            raise HTTPException(422, "Select findings from the current brief.")
        snapshot = json.dumps({"firm": firm["name"], "domain": firm["domain"],
                               "findings": [findings[i] for i in indices]})
        token = digest(snapshot)
        db.execute("INSERT OR IGNORE INTO proposals(firm_id,digest,snapshot) VALUES(?,?,?)",
                   (firm_id, token, snapshot))
        return dict(db.execute("SELECT id,status FROM proposals WHERE firm_id=? AND digest=?",
                               (firm_id, token)).fetchone())


def build_deck(proposal_id: int, snapshot: str):
    try:
        data = generate_deck(snapshot)
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
                            RETURNING snapshot""", (proposal_id, firm_id)).fetchone()
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


def research(firm_id: int, domain: str):
    def progress(count):
        with database() as db:
            db.execute("UPDATE firms SET research_meta=? WHERE id=?",
                       (json.dumps({"stage": "Reading website pages", "pages_read": count}), firm_id))

    pages, warnings, logos = [], [], []
    try:
        pages, warnings = crawl_site(domain, progress)
        with database() as db:
            db.execute("UPDATE firms SET research_meta=? WHERE id=?",
                       (json.dumps({"stage": "Extracting firm profile", "pages_read": len(pages)}), firm_id))
        with openai.OpenAI(timeout=180, max_retries=0) as client:
            brief, rejected = extract_profile(
                client, os.getenv("OPENAI_RESEARCH_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-6.1-sol", pages)
        if rejected:
            warnings.append(f"{rejected} findings or staff fields omitted because supporting evidence could not be verified.")
        for url in dict.fromkeys(url for page in pages for url in page.get("logos", [])):
            try:
                logos.append(download_logo(url, domain))
            except ResearchError as exc:
                warnings.append(f"Logo {url}: {exc}")
        status = "ready" if brief else "needs_review"
        error = None if brief else "No evidence-backed findings returned. Review the website or retry."
    except ResearchError as exc:
        status, brief, error = "failed", [], str(exc)
    except openai.OpenAIError:
        status, brief, error = "failed", [], "OpenAI research failed. Check model access, quota and connectivity."
    with database() as db:
        if brief:
            db.execute("UPDATE firms SET brief=?, researched_at=CURRENT_TIMESTAMP WHERE id=?",
                       (json.dumps(brief), firm_id))
            replace_staff(db, firm_id, brief)
            db.execute("UPDATE firms SET preferred_logo_id=NULL WHERE id=?", (firm_id,))
            db.execute("DELETE FROM brand_assets WHERE firm_id=?", (firm_id,))
            for logo in logos:
                db.execute("INSERT INTO brand_assets(firm_id,source_url,mime_type,content) VALUES(?,?,?,?)",
                           (firm_id, logo["source_url"], logo["mime_type"], logo["content"]))
        db.execute("UPDATE firms SET status=?, error=? WHERE id=?", (status, error, firm_id))
        db.execute("UPDATE firms SET research_meta=?,research_pages=? WHERE id=?",
                   (json.dumps({"stage": status, "pages_read": len(pages), "warnings": warnings,
                                "pages": [{"url": p["url"], "title": p["title"]} for p in pages]}),
                    json.dumps(pages), firm_id))


@router.post("/{firm_id}/research", status_code=202)
def start_research(firm_id: int, tasks: BackgroundTasks, body: dict):
    firm = get_firm(firm_id)
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "Research is not configured on the server.")
    with database() as db:
        if not db.execute("UPDATE firms SET status='researching',error=NULL,research_meta='{}' WHERE id=? AND status!='researching'",
                          (firm_id,)).rowcount:
            raise HTTPException(409, "Research is already running.")
    tasks.add_task(research, firm_id, firm["domain"])
    return {"id": firm_id, "status": "researching"}
