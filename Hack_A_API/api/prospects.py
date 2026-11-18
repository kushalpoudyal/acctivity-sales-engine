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
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from decks import DeckError, generate_deck
from firm_research import ResearchError, crawl_site, download_logo, extract_profile
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/firms", tags=["Prospects"])
DB_PATH = Path(__file__).with_name("prospects.sqlite3")


# source: 'website' (found by research), 'apollo' (revealed in Apollo) or 'both' (merged by name).
# apollo_email holds the revealed work email apart from the website's public_work_email.
STAFF_APOLLO_COLUMNS = [("source", "TEXT NOT NULL DEFAULT 'website'"), ("apollo_id", "TEXT"),
                        ("linkedin_url", "TEXT"), ("email_status", "TEXT"), ("match_confidence", "TEXT"),
                        ("apollo_email", "TEXT")]


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
        proposals = [dict(p) for p in db.execute(
            "SELECT id,status,error FROM proposals WHERE firm_id=? ORDER BY id DESC", (firm_id,))]
        staff = [{**dict(p), "profile": json.loads(p["profile"])} for p in db.execute(
            "SELECT * FROM staff WHERE firm_id=? ORDER BY full_name", (firm_id,))]
        assets = [dict(p) for p in db.execute(
            "SELECT id,source_url,mime_type FROM brand_assets WHERE firm_id=?", (firm_id,))]
    if not row:
        raise HTTPException(404, "Firm not found.")
    values = dict(row)
    values.pop("research_pages")
    return {**values, "research_meta": json.loads(row["research_meta"]),
            "apollo_org": json.loads(row["apollo_org"]) if row["apollo_org"] else None,
            "apollo_configured": apollo.configured(),
            "brief": json.loads(row["brief"]), "brief_token": digest(row["brief"]),
            "proposals": proposals, "staff": staff, "brand_assets": assets}


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
        if digest(firm["brief"]) != body.brief_token or firm["status"] == "researching":
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
                  json.dumps(person), finding["sources"][0]["url"], finding["evidence"])
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
                      match_confidence=?, position=COALESCE(position,?), source=? WHERE id=?""",
                   (*apollo_fields, match.get("title"), "apollo" if row["source"] == "apollo" else "both",
                    row["id"]))
    else:
        db.execute("""INSERT INTO staff(firm_id,full_name,position,profile,source_url,evidence,source,
                      apollo_id,linkedin_url,apollo_email,email_status,match_confidence)
                      VALUES(?,?,?,'{}',?,'','apollo',?,?,?,?,?)""",
                   (firm_id, name, match.get("title"), match.get("linkedin_url") or "", *apollo_fields))


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
            replace_website_staff(db, firm_id, brief)
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
