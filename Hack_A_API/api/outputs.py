import io
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import openai
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

import sendspark
from decks import DeckError, validate_pptx
from documents import RENDERERS, write_copy
from model_stream import ModelStreamError
from prospects import SNAPSHOT_VERSION, database, digest

router = APIRouter(prefix="/api/v1/firms", tags=["Output studio"])
FORMATS = {
    "pptx": ("proposal.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "pdf": ("welcome.pdf", "application/pdf"),
}
# "video" is a Sendspark share link rather than a saved file.
OutputFormat = Literal["pptx", "pdf", "video"]
VIDEO_POLL_SECONDS = 15
VIDEO_TIMEOUT_SECONDS = 900


class OutputRequest(BaseModel):
    approval_id: int
    formats: list[OutputFormat] = Field(min_length=1, max_length=3)
    instructions: str = Field(default="", max_length=6000)
    logo_id: int | None = None
    request_key: UUID


def validate_document(data: bytes, format: str):
    if not 0 < len(data) <= 10_000_000:
        raise DeckError("Expected a nonempty document smaller than 10 MB.")
    if format == "pptx":
        validate_pptx(data)
    elif format == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or not reader.pages:
                raise DeckError("The PDF is encrypted or has no pages.")
        except (PdfReadError, ValueError, OSError) as exc:
            raise DeckError("The PDF could not be read back.") from exc
    else:
        raise DeckError("Unsupported output format.")


def update_stage(job_id: int, stage: str):
    with database() as db:
        db.execute("UPDATE output_jobs SET stage=? WHERE id=?", (stage, job_id))


def save_file(job_id: int, format: str, data: bytes | None, error: str | None):
    timestamp = datetime.now(timezone.utc).isoformat()
    with database() as db:
        db.execute("""UPDATE output_files SET status=?,content=?,error=?,stage=?,updated_at=?,finished_at=?
                      WHERE job_id=? AND format=?""",
                   ("ready" if data is not None else "failed", data, error,
                    "Download saved" if data is not None else "Failed", timestamp, timestamp, job_id, format))


def file_progress(job_id: int, format: str, stage: str, delta=None):
    timestamp = datetime.now(timezone.utc).isoformat()
    with database() as db:
        db.execute("""UPDATE output_files SET status='running',stage=?,started_at=COALESCE(started_at,?),
                      updated_at=?,preview=substr(preview || ?, -4000) WHERE job_id=? AND format=?""",
                   (stage, timestamp, timestamp, delta or "", job_id, format))


def generate_outputs(job_id: int, job: dict):
    """OpenAI writes the copy once; our brand templates build each requested file from the approved records."""
    snapshot = json.loads(job["snapshot"])
    update_stage(job_id, "Writing proposal copy with OpenAI")

    def progress(stage, delta):
        for format in job["formats"]:
            file_progress(job_id, format, f"Copy: {stage}", delta)

    progress("Requesting proposal copy", None)
    copy = write_copy(snapshot, job["instructions"], progress)
    for format in job["formats"]:
        update_stage(job_id, f"Building {format.upper()} from Acctivity templates")
        file_progress(job_id, format, "Building from Acctivity template")
        try:
            data = RENDERERS[format](snapshot, copy, job["logo"])
            validate_document(data, format)
            save_file(job_id, format, data, None)
        except DeckError as exc:
            save_file(job_id, format, None, str(exc))
        except Exception:
            # One template failing must not lose the other formats.
            logging.exception("Output job %s could not build %s.", job_id, format)
            save_file(job_id, format, None, f"Could not build the {format.upper()} file. See the API log.")


def save_video(job_id: int, url: str | None, error: str | None):
    timestamp = datetime.now(timezone.utc).isoformat()
    with database() as db:
        db.execute("""UPDATE output_files SET status=?,url=?,error=?,stage=?,updated_at=?,finished_at=?
                      WHERE job_id=? AND format='video'""",
                   ("ready" if url else "failed", url, error, "Video ready" if url else "Failed",
                    timestamp, timestamp, job_id))


def generate_video(job_id: int, job: dict):
    """Sendspark renders the campaign video with the firm's name; we poll (free) until its share page is ready."""
    snapshot = json.loads(job["snapshot"])
    email = sendspark.prospect_email(snapshot["domain"])
    file_progress(job_id, "video", "Sending the firm name to Sendspark")
    try:
        sendspark.add_prospect(snapshot["firm"], email, "https://" + snapshot["domain"])
        deadline = time.monotonic() + VIDEO_TIMEOUT_SECONDS
        while True:
            prospect = sendspark.get_prospect(email)
            status = prospect.get("status") or "processing"
            if status == sendspark.DONE and prospect.get("shareUrl"):
                return save_video(job_id, prospect["shareUrl"], None)
            if status == sendspark.FAILED:
                return save_video(job_id, None, "Sendspark could not generate the video. Check the campaign in Sendspark.")
            if time.monotonic() > deadline:
                return save_video(job_id, None, "Sendspark was still rendering after 15 minutes. Check the campaign in Sendspark.")
            file_progress(job_id, "video", f"Sendspark: {status}")
            time.sleep(VIDEO_POLL_SECONDS)
    except sendspark.SendsparkError as exc:
        save_video(job_id, None, str(exc))


def run_outputs(job_id: int):
    with database() as db:
        row = db.execute("SELECT * FROM output_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise RuntimeError("Queued output job was not found.")
        job = {**dict(row), "formats": json.loads(row["formats"])}
        db.execute("UPDATE output_jobs SET status='running',stage='Starting' WHERE id=?", (job_id,))
    error = None
    documents = [format for format in job["formats"] if format != "video"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        if documents:
            futures[executor.submit(generate_outputs, job_id, {**job, "formats": documents})] = "documents"
        if "video" in job["formats"]:
            futures[executor.submit(generate_video, job_id, job)] = "video"
        remaining = set(futures.values())
        for future in as_completed(futures):
            kind = futures[future]
            try:
                future.result()
            except Exception as exc:
                # Settle each independent provider task without losing the other's results.
                logging.error("Output job %s %s failed (%s).", job_id, kind, type(exc).__name__)
                if kind == "video":
                    save_video(job_id, None, "Could not create the video. Check Sendspark or retry.")
                else:
                    error = (str(exc) if isinstance(exc, (DeckError, ModelStreamError)) else
                             "OpenAI request timed out. Prepare a retry of this run."
                             if isinstance(exc, openai.APITimeoutError) else
                             "Output build failed. Check provider access or retry this run.")
            if kind == "documents":
                with database() as db:
                    unfinished = [row["format"] for row in db.execute(
                        """SELECT format FROM output_files WHERE job_id=? AND format!='video'
                           AND status NOT IN ('ready','failed')""", (job_id,))]
                for format in unfinished:
                    save_file(job_id, format, None, error or "No file returned for this format.")
            remaining.remove(kind)
            if remaining == {"video"}:
                update_stage(job_id, "Waiting for Sendspark video; documents have finished")
            elif remaining == {"documents"}:
                update_stage(job_id, "Video has finished; documents are still generating")
    with database() as db:
        db.execute("""UPDATE output_files SET status='failed',error=?
                      WHERE job_id=? AND status!='ready' AND status!='failed'""",
                   (error or "No file returned for this format.", job_id))
        total, ready = db.execute(
            "SELECT count(*),sum(status='ready') FROM output_files WHERE job_id=?", (job_id,)).fetchone()
        status = "ready" if ready == total else "partial" if ready else "failed"
        db.execute("UPDATE output_jobs SET status=?,stage=?,error=? WHERE id=?",
                   (status, "Downloads saved" if status == "ready" else "Finished with errors", error, job_id))


@router.post("/{firm_id}/outputs", status_code=202)
def create_outputs(firm_id: int, body: OutputRequest, tasks: BackgroundTasks):
    payload = body.model_dump(mode="json", exclude={"request_key"})
    payload["formats"] = sorted(set(payload["formats"]))
    request_digest = digest(json.dumps(payload, sort_keys=True))
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT id,status,request_digest FROM output_jobs WHERE firm_id=? AND request_key=?",
                              (firm_id, str(body.request_key))).fetchone()
        if previous:
            if previous["request_digest"] != request_digest:
                raise HTTPException(409, "This request key was already used with different output options.")
            return {"id": previous["id"], "status": previous["status"]}
        approval = db.execute("""SELECT snapshot FROM proposals
                                 WHERE id=? AND firm_id=? AND json_extract(snapshot,'$.version')=?""",
                              (body.approval_id, firm_id, SNAPSHOT_VERSION)).fetchone()
        if not approval:
            raise HTTPException(404, "Choose an approved brief belonging to this firm.")
        if set(payload["formats"]) - {"video"} and not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(503, "Output generation is not configured.")
        if "video" in payload["formats"] and not sendspark.configured():
            raise HTTPException(503, sendspark.NOT_CONFIGURED)
        logo = None
        if body.logo_id is not None:
            logo = db.execute("SELECT content,mime_type FROM brand_assets WHERE id=? AND firm_id=?",
                              (body.logo_id, firm_id)).fetchone()
            if not logo:
                raise HTTPException(422, "Choose a saved logo belonging to this firm.")
        row = db.execute("""INSERT INTO output_jobs
            (firm_id,approval_id,request_key,request_digest,snapshot,instructions,formats,logo,logo_mime)
            VALUES(?,?,?,?,?,?,?,?,?) RETURNING id""",
            (firm_id, body.approval_id, str(body.request_key), request_digest, approval["snapshot"],
             body.instructions, json.dumps(payload["formats"]),
             logo["content"] if logo else None, logo["mime_type"] if logo else None)).fetchone()
        job_id = row["id"]
        for format in payload["formats"]:
            db.execute("INSERT INTO output_files(job_id,format) VALUES(?,?)", (job_id, format))
    tasks.add_task(run_outputs, job_id)
    return {"id": job_id, "status": "queued"}


@router.get("/{firm_id}/outputs/{job_id}/files/{file_id}")
def download_output(firm_id: int, job_id: int, file_id: int):
    with database() as db:
        row = db.execute("""SELECT f.format,f.content FROM output_files f
            JOIN output_jobs j ON j.id=f.job_id
            WHERE f.id=? AND f.job_id=? AND j.firm_id=? AND f.status='ready' AND f.content IS NOT NULL""",
            (file_id, job_id, firm_id)).fetchone()
    # Word files from before Word generation was removed are no longer served.
    if not row or row["format"] not in FORMATS:
        raise HTTPException(404, "No completed file for this firm and run.")
    filename, mime = FORMATS[row["format"]]
    return Response(content=row["content"], media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Content-Type-Options": "nosniff"})


@router.delete("/{firm_id}/outputs/{job_id}")
def delete_output(firm_id: int, job_id: int):
    """Hard delete one output run and its saved files. A Sendspark video stays in Sendspark."""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT status FROM output_jobs WHERE id=? AND firm_id=?", (job_id, firm_id)).fetchone()
        if not job:
            raise HTTPException(404, "Output run not found for this firm.")
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "This run is still in progress. Delete it once it has finished.")
        db.execute("DELETE FROM output_files WHERE job_id=?", (job_id,))
        db.execute("DELETE FROM output_jobs WHERE id=?", (job_id,))
    return {"id": job_id, "deleted": True}
