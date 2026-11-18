import json
import hashlib
import logging
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from threading import Lock

import apollo
from apollo_findings import apollo_findings
import openai
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from decks import DeckError, generate_deck
from firm_research import MAX_PAGES, Person, ResearchError, crawl_site, download_logo, extract_profile
from pydantic import BaseModel, Field
from web_research import search_web

router = APIRouter(prefix="/api/v1/firms", tags=["Prospects"])
DB_PATH = Path(__file__).with_name("prospects.sqlite3")


# source: 'website' (found by research), 'apollo' (revealed in Apollo) or 'both' (merged by name).
# apollo_email holds the revealed work email apart from the website's public_work_email.
STAFF_APOLLO_COLUMNS = [("source", "TEXT NOT NULL DEFAULT 'website'"), ("apollo_id", "TEXT"),
                        ("linkedin_url", "TEXT"), ("email_status", "TEXT"), ("match_confidence", "TEXT"),
                        ("apollo_email", "TEXT"), ("apollo_revealed_at", "TEXT")]


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
        staff_columns = {row["name"] for row in db.execute("PRAGMA table_info(staff)")}
        for column, definition in STAFF_APOLLO_COLUMNS:
            if column not in staff_columns:
                db.execute(f"ALTER TABLE staff ADD COLUMN {column} {definition}")
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
    org = json.loads(row["apollo_org"]) if row["apollo_org"] else None
    extra = apollo_findings(org, row["apollo_enriched_at"], staff)
    return {**values, "research_meta": json.loads(row["research_meta"]),
            "apollo_org": org, "apollo_configured": apollo.configured(), "apollo_findings": extra,
            "brief": json.loads(row["brief"]), "brief_token": review_token(row, extra),
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
            # An approved website profile and an approved Apollo contact for the same name merge into one
            # person; Apollo values (apollo_email, linkedin_url) appear only when their finding was approved.
            key = finding["person"]["full_name"].casefold()
            person = people.get(key, {"sources": []})
            person.update({k: v for k, v in finding["person"].items() if v is not None or k not in person})
            person["sources"] = person.pop("sources") + finding["sources"]
            people[key] = person
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


def review_token(firm, extra: list[dict]):
    """The approval/edit token: changes when the brief, name or preferred logo change (revision_token)
    and when the Apollo findings under review change. Without Apollo findings it is revision_token."""
    base = revision_token(firm)
    return digest(base + json.dumps(extra, sort_keys=True)) if extra else base


def current_review(db, firm):
    """The firm's Apollo findings and review token, read inside the caller's transaction."""
    staff = [dict(p) for p in db.execute("SELECT * FROM staff WHERE firm_id=? ORDER BY full_name", (firm["id"],))]
    extra = apollo_findings(json.loads(firm["apollo_org"]) if firm["apollo_org"] else None,
                            firm["apollo_enriched_at"], staff)
    return extra, review_token(firm, extra)


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
        if row["status"] == "researching" or current_review(db, row)[1] != body.brief_token:
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
        replace_website_staff(db, firm_id, edited)
    return get_firm(firm_id)


class Approval(BaseModel):
    brief_token: str
    findings: list[int] = Field(default_factory=list)
    apollo_findings: list[str] = Field(default_factory=list, max_length=1000)


@router.post("/{firm_id}/approvals")
def approve(firm_id: int, body: Approval):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if not firm:
            raise HTTPException(404, "Firm not found.")
        extra, token = current_review(db, firm)
        if token != body.brief_token or firm["status"] == "researching":
            raise HTTPException(409, "Research changed or is running. Refresh and review the findings again.")
        findings = json.loads(firm["brief"])
        indices = sorted(set(body.findings))
        keys = set(body.apollo_findings)
        if not indices and not keys:
            raise HTTPException(422, "Select at least one finding.")
        if indices and (min(indices) < 0 or max(indices) >= len(findings)):
            raise HTTPException(422, "Select findings from the current brief.")
        if keys - {item["key"] for item in extra}:
            raise HTTPException(422, "Select Apollo findings from the current review.")
        snapshot = json.dumps({"firm": firm["name"], "domain": firm["domain"],
                               "findings": [findings[i] for i in indices]
                               + [item for item in extra if item["key"] in keys]})
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


def staff_row(db, firm_id: int, full_name: str, apollo_id: str | None = None):
    """The firm's staff row for this person: same Apollo id, else same name ignoring case."""
    if apollo_id:
        row = db.execute("SELECT * FROM staff WHERE firm_id=? AND apollo_id=?", (firm_id, apollo_id)).fetchone()
        if row:
            return row
    key = full_name.strip().casefold()
    return next((row for row in db.execute("SELECT * FROM staff WHERE firm_id=?", (firm_id,))
                 if row["full_name"].strip().casefold() == key), None)


def replace_website_staff(db, firm_id: int, brief: list[dict]):
    """Swap in the website staff from a new brief. Rows with Apollo data survive: merged rows lose
    their old website fields, then website people merge back in by name (case-insensitive)."""
    db.execute("DELETE FROM staff WHERE firm_id=? AND source='website' AND apollo_id IS NULL", (firm_id,))
    db.execute("""UPDATE staff SET source='apollo', public_work_email=NULL, public_work_phone=NULL,
                  profile='{}', source_url=COALESCE(linkedin_url,''), evidence=''
                  WHERE firm_id=? AND source='both'""", (firm_id,))
    for finding in brief:
        if not (person := finding.get("person")):
            continue
        values = (person["position"], person["public_work_email"], person["public_work_phone"],
                  json.dumps(person), finding["sources"][0]["url"], finding.get("evidence", ""))
        if row := staff_row(db, firm_id, person["full_name"]):
            db.execute("""UPDATE staff SET position=?, public_work_email=?, public_work_phone=?, profile=?,
                          source_url=?, evidence=?, source=? WHERE id=?""",
                       (*values, "both" if row["apollo_id"] else "website", row["id"]))
        else:
            db.execute("""INSERT INTO staff(firm_id,full_name,position,public_work_email,public_work_phone,
                          profile,source_url,evidence,source) VALUES(?,?,?,?,?,?,?,?,'website')""",
                       (firm_id, person["full_name"], *values))


def merge_apollo_person(db, firm_id: int, match: dict):
    """Store one bulk_match result. A website row with the same name gains the Apollo fields and keeps
    its biography, practice areas and evidence."""
    name = (match.get("name") or " ".join(filter(None, [match.get("first_name"), match.get("last_name")]))).strip()
    if not name or not match.get("id"):
        return
    apollo_fields = (match["id"], match.get("linkedin_url"), match.get("email"), match.get("email_status"),
                     match.get("match_confidence"))
    if row := staff_row(db, firm_id, name, match["id"]):
        db.execute("""UPDATE staff SET apollo_id=?, linkedin_url=?, apollo_email=?, email_status=?,
                      match_confidence=?, apollo_revealed_at=CURRENT_TIMESTAMP, position=COALESCE(position,?),
                      source=? WHERE id=?""",
                   (*apollo_fields, match.get("title"), "apollo" if row["source"] == "apollo" else "both",
                    row["id"]))
    else:
        db.execute("""INSERT INTO staff(firm_id,full_name,position,profile,source_url,evidence,source,
                      apollo_id,linkedin_url,apollo_email,email_status,match_confidence,apollo_revealed_at)
                      VALUES(?,?,?,'{}',?,'','apollo',?,?,?,?,?,CURRENT_TIMESTAMP)""",
                   (firm_id, name, match.get("title"), match.get("linkedin_url") or "", *apollo_fields))


class ResearchOptions(BaseModel):
    web_search: bool = True
    reputation: bool = True


def crawl_research(domain: str, model: str, progress):
    progress(stage="Reading website pages")
    pages, warnings = crawl_site(domain, progress)
    progress(stage="Extracting structured firm data")
    with openai.OpenAI(timeout=180, max_retries=0) as client:
        brief, rejected = extract_profile(client, model, pages)
    for finding in brief:
        finding["source_kind"] = "website_crawl"
    if rejected:
        warnings.append(f"{rejected} findings or staff fields omitted because supporting evidence could not be verified.")
    logos = []
    progress(stage="Saving published logos")
    for url in dict.fromkeys(url for page in pages for url in page.get("logos", [])):
        try:
            logos.append(download_logo(url, domain))
        except ResearchError as exc:
            warnings.append(f"Logo {url}: {exc}")
    return brief, pages, logos, warnings


def research(firm_id: int, domain: str, options: ResearchOptions | None = None):
    options = options or ResearchOptions()
    model = os.getenv("OPENAI_RESEARCH_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-6.1-sol"
    firm = get_firm(firm_id)
    enabled = ["website"] + (["web_search"] if options.web_search else []) + (
        ["reputation"] if options.reputation else [])
    started_at = datetime.now(timezone.utc).isoformat()
    meta = {"stage": "Researching sources in parallel", "pages_read": 0, "max_pages": MAX_PAGES,
            "started_at": started_at, "updated_at": started_at, "provider_timeout_seconds": 180,
            "sources": {name: {"status": "running", "started_at": started_at,
                               "stage": "Starting website crawl" if name == "website" else "Searching the web"}
                        for name in enabled}}
    lock = Lock()

    def persist_progress(count=None, stage=None):
        with lock:
            if count is not None:
                meta["pages_read"] = count
            if stage is not None:
                meta["sources"]["website"]["stage"] = stage
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            with database() as db:
                db.execute("UPDATE firms SET research_meta=? WHERE id=?", (json.dumps(meta), firm_id))

    persist_progress()
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(crawl_research, domain, model, persist_progress): "website"}
        if options.web_search:
            futures[executor.submit(search_web, domain, firm["name"], model)] = "web_search"
        if options.reputation:
            futures[executor.submit(search_web, domain, firm["name"], model, True)] = "reputation"
        for future in as_completed(futures):
            name = futures[future]
            try:
                value = future.result()
                findings = value[0] if name == "website" else value
                results[name] = value
                source_status = {"status": "completed" if findings else "no_results", "findings": len(findings)}
            except Exception as exc:
                # Each independent provider task reports failure without discarding other sources.
                logging.error("Research source %s failed for firm %s (%s).", name, firm_id, type(exc).__name__)
                if isinstance(exc, openai.APITimeoutError):
                    code = "provider_timeout"
                    errors[name] = "OpenAI request timed out. Other source results are preserved; retry the research."
                elif isinstance(exc, ResearchError):
                    code = "research_error"
                    errors[name] = str(exc)
                else:
                    code = "source_error"
                    errors[name] = "Research source failed. Check provider access, quota and connectivity."
                source_status = {"status": "failed", "error": errors[name], "error_code": code}
            with lock:
                meta["sources"][name] = {**meta["sources"][name], **source_status,
                                          "stage": source_status["status"],
                                          "finished_at": datetime.now(timezone.utc).isoformat()}
            persist_progress()
    crawl_brief, pages, logos, warnings = results.get("website", ([], [], [], []))
    warnings.extend(f"{name}: {error}" for name, error in errors.items())
    if not crawl_brief:
        warnings.append("No new first-party staff records were extracted; previously saved staff and branding were retained.")
    brief = crawl_brief + results.get("web_search", []) + results.get("reputation", [])
    timestamp = datetime.now(timezone.utc).isoformat()
    for finding in brief:
        finding["retrieved_at"] = timestamp
    status = "ready" if crawl_brief and not errors else "needs_review" if brief else "failed"
    error = ("Some research sources failed. Review source coverage below." if errors and brief else
             "No sourced findings returned. Review source coverage or retry." if not brief else None)
    with database() as db:
        if brief:
            db.execute("UPDATE firms SET brief=?, researched_at=CURRENT_TIMESTAMP WHERE id=?",
                       (json.dumps(brief), firm_id))
        if crawl_brief:
            replace_website_staff(db, firm_id, crawl_brief)
            db.execute("UPDATE firms SET preferred_logo_id=NULL WHERE id=?", (firm_id,))
            db.execute("DELETE FROM brand_assets WHERE firm_id=?", (firm_id,))
            for logo in logos:
                db.execute("INSERT INTO brand_assets(firm_id,source_url,mime_type,content) VALUES(?,?,?,?)",
                           (firm_id, logo["source_url"], logo["mime_type"], logo["content"]))
        db.execute("UPDATE firms SET status=?, error=? WHERE id=?", (status, error, firm_id))
        db.execute("UPDATE firms SET research_meta=?,research_pages=? WHERE id=?",
                   (json.dumps({**meta, "stage": status, "finished_at": timestamp,
                                "updated_at": timestamp, "pages_read": max(len(pages), meta["pages_read"]), "warnings": warnings,
                                "pages": [{"url": p["url"], "title": p["title"]} for p in pages]}),
                    json.dumps(pages), firm_id))


@router.post("/{firm_id}/research", status_code=202)
def start_research(firm_id: int, tasks: BackgroundTasks, body: ResearchOptions):
    firm = get_firm(firm_id)
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "Research is not configured on the server.")
    with database() as db:
        if not db.execute("UPDATE firms SET status='researching',error=NULL,research_meta='{}' WHERE id=? AND status!='researching'",
                          (firm_id,)).rowcount:
            raise HTTPException(409, "Research is already running.")
    tasks.add_task(research, firm_id, firm["domain"], body)
    return {"id": firm_id, "status": "researching"}


DEFAULT_TITLES = ["Managing Partner", "Partner", "Practice Manager", "General Manager", "Operations Manager",
                  "Finance Manager", "CFO"]
# The 18/11 probe found nobody by title at 3 small AU firms, but these seniorities did return people.
FALLBACK_SENIORITIES = ["owner", "founder", "c_suite", "partner", "head", "director", "manager"]


def query_list(values: list[str] | None) -> list[str]:
    """Repeated or comma-separated query values, trimmed and de-duplicated."""
    items = (part.strip() for value in values or [] for part in value.split(","))
    return list(dict.fromkeys(item for item in items if item))


@router.get("/{firm_id}/apollo/candidates")
def apollo_candidates(firm_id: int, titles: list[str] | None = Query(None),
                      seniorities: list[str] | None = Query(None), fallback: bool = True):
    """Free Apollo people search at the firm (names obfuscated, no emails). Searches by title
    (DEFAULT_TITLES when neither titles nor seniorities are given); if a title-only search finds
    nobody, searches again by FALLBACK_SENIORITIES. `search` says which search produced the results."""
    firm = get_firm(firm_id)
    if not firm["apollo_configured"]:
        raise HTTPException(503, apollo.NOT_CONFIGURED)
    titles, seniorities = query_list(titles), query_list(seniorities)
    if not titles and not seniorities:
        titles = DEFAULT_TITLES
    search = "titles+seniorities" if titles and seniorities else "titles" if titles else "seniorities"
    try:
        result = apollo.search_people(firm["domain"], titles, seniorities=seniorities or None)
        people = result.get("people") or []
        fell_back = not people and search == "titles" and fallback
        if fell_back:
            search, titles, seniorities = "seniorities", [], FALLBACK_SENIORITIES
            result = apollo.search_people(firm["domain"], [], seniorities=seniorities)
            people = result.get("people") or []
    except apollo.ApolloError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    stored = {person["apollo_id"]: person for person in firm["staff"] if person["apollo_id"]}
    return {"search": search, "fallback": fell_back, "titles": titles, "seniorities": seniorities,
            "total": result.get("total_entries", len(people)), "credits": 0,
            "candidates": [{"apollo_id": p["id"], "first_name": p.get("first_name"),
                            "last_name_obfuscated": p.get("last_name_obfuscated"), "title": p.get("title"),
                            "has_email": p.get("has_email"),
                            "revealed": bool(stored.get(p["id"], {}).get("apollo_email")),
                            "staff_id": stored.get(p["id"], {}).get("id")}
                           for p in people if p.get("id")]}


class ApolloReveal(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/{firm_id}/apollo/reveal")
def apollo_reveal(firm_id: int, body: ApolloReveal):
    """Reveal work emails (Apollo bulk_match, 1 credit per matched person, no phones or personal
    emails) in batches of apollo.MAX_REVEAL, merged into staff. Refuses, before spending anything,
    when any id is already stored with an email. Each batch is saved as soon as it returns."""
    firm = get_firm(firm_id)
    if not firm["apollo_configured"]:
        raise HTTPException(503, apollo.NOT_CONFIGURED)
    ids = list(dict.fromkeys(i.strip() for i in body.ids if i.strip()))
    if not ids:
        raise HTTPException(422, "Choose at least one person to reveal.")
    already = [p["full_name"] for p in firm["staff"] if p["apollo_id"] in ids and p["apollo_email"]]
    if already:
        raise HTTPException(409, f"Email already revealed for {', '.join(already)}. "
                                 "Untick them; revealing again would spend credits twice.")
    matched = []
    for start in range(0, len(ids), apollo.MAX_REVEAL):
        try:
            batch = apollo.reveal(ids[start:start + apollo.MAX_REVEAL])
        except apollo.ApolloError as exc:
            saved = f" {len(matched)} people from earlier batches were saved." if matched else ""
            raise HTTPException(exc.status_code, str(exc) + saved) from None
        with database() as db:
            for match in batch:
                merge_apollo_person(db, firm_id, match)
        matched += batch
    matched_ids = {match.get("id") for match in matched}
    return {"requested": len(ids), "matched": len(matched),
            "with_email": sum(1 for match in matched if match.get("email")),
            "unmatched_ids": [i for i in ids if i not in matched_ids],
            "staff": get_firm(firm_id)["staff"]}
