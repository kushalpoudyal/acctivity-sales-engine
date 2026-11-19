import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from threading import Barrier, Event

import httpx
from fastapi import BackgroundTasks, HTTPException

import outputs
import prospects
import sendspark
from test_prospects import PAGES, RECORDS

SETTINGS = {"SENDSPARK_API_KEY": "test-key", "SENDSPARK_API_SECRET_KEY": "test-secret",
            "SENDSPARK_WORKSPACE_ID": "ws1", "SENDSPARK_DYNAMIC_ID": "dyn1",
            "SENDSPARK_PROSPECT_EMAIL": "sales@team.example"}


class FakeSendspark:
    """Stands in for api-gw.sendspark.com: records requests and answers GETs from `statuses` in order."""

    def __init__(self, statuses=("completed",), add_status=200):
        self.statuses, self.add_status, self.requests = list(statuses), add_status, []

    def __enter__(self):
        self.patches = [patch.object(sendspark, "_transport", httpx.MockTransport(self.handle)),
                        patch.dict(os.environ, SETTINGS)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()

    def handle(self, request):
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(self.add_status, json={"_id": "dyn1", "status": "pending"})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        prospect = {"status": status, "shareUrl": "https://sendspark.com/share/abc" if status == "completed" else None}
        return httpx.Response(200, json={"response": {"data": prospect}})


class SendsparkTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prospects, "DB_PATH", Path(folder) / "test.sqlite3"))
        self.enterContext(patch.object(outputs, "VIDEO_POLL_SECONDS", 0))
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": ""}))
        prospects.initialize()
        self.firm = prospects.add_firm(prospects.Intake(website="firm.example", display_name="Firm Ltd"))
        with prospects.database() as db:
            prospects.store_research(db, self.firm["id"], PAGES, RECORDS)
        detail = prospects.get_firm(self.firm["id"])
        self.approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=detail["brief_token"], people=[p["id"] for p in detail["records"]["people"]]))

    def run_video(self):
        tasks = BackgroundTasks()
        outputs.create_outputs(self.firm["id"], outputs.OutputRequest(
            approval_id=self.approval["id"], formats=["video"], request_key=uuid4()), tasks)
        with patch("outputs.write_copy") as write_copy:
            asyncio.run(tasks())
        write_copy.assert_not_called()
        return prospects.get_firm(self.firm["id"])["outputs"][0]

    def test_video_sends_only_the_firm_name_and_saves_the_share_link(self):
        with FakeSendspark(statuses=("processing", "completed")) as fake:
            run = self.run_video()
        add = fake.requests[0]
        self.assertEqual(add.url.path, "/v1/workspaces/ws1/dynamics/dyn1/prospect")
        self.assertEqual((add.headers["x-api-key"], add.headers["x-api-secret"]), ("test-key", "test-secret"))
        self.assertEqual(json.loads(add.content), {
            "prospect": {"contactName": "Firm Ltd", "company": "Firm Ltd",
                         "contactEmail": "sales+firm-example@team.example", "backgroundUrl": "https://firm.example"},
            "processAndAuthorizeCharge": True})
        self.assertEqual(len(fake.requests), 3)
        self.assertTrue(fake.requests[-1].url.path.endswith("/prospects/sales+firm-example@team.example"))
        self.assertEqual(run["status"], "ready")
        self.assertEqual(run["files"][0]["url"], "https://sendspark.com/share/abc")

    def test_failed_render_and_rejected_request_are_terminal(self):
        with FakeSendspark(statuses=("errored",)):
            run = self.run_video()
        self.assertEqual((run["status"], run["files"][0]["status"]), ("failed", "failed"))
        self.assertIn("could not generate", run["files"][0]["error"])
        with FakeSendspark(add_status=401) as fake:
            run = self.run_video()
        self.assertEqual(len(fake.requests), 1)
        self.assertIn("401", run["files"][0]["error"])

    def test_video_needs_sendspark_settings(self):
        with self.assertRaises(HTTPException) as missing:
            outputs.create_outputs(self.firm["id"], outputs.OutputRequest(
                approval_id=self.approval["id"], formats=["video"], request_key=uuid4()), BackgroundTasks())
        self.assertEqual(missing.exception.status_code, 503)
        self.assertFalse(prospects.get_firm(self.firm["id"])["sendspark_configured"])

    def test_video_starts_and_finishes_while_documents_are_still_running(self):
        barrier, video_ready = Barrier(2), Event()
        original_video = outputs.generate_video

        def documents(job_id, job):
            outputs.file_progress(job_id, "pdf", "Generating document")
            barrier.wait(timeout=3)
            self.assertTrue(video_ready.wait(timeout=3), "Video waited for documents to finish")
            run = prospects.get_firm(self.firm["id"])["outputs"][0]
            files = {f["format"]: f for f in run["files"]}
            self.assertEqual(files["video"]["status"], "ready")
            self.assertEqual(files["pdf"]["status"], "running")
            outputs.save_file(job_id, "pdf", b"fixture", None)

        def video(job_id, job):
            barrier.wait(timeout=3)
            original_video(job_id, job)
            video_ready.set()

        with FakeSendspark(), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("outputs.generate_outputs", side_effect=documents), \
                patch("outputs.generate_video", side_effect=video):
            tasks = BackgroundTasks()
            outputs.create_outputs(self.firm["id"], outputs.OutputRequest(
                approval_id=self.approval["id"], formats=["pdf", "video"], request_key=uuid4()), tasks)
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "ready")

    def test_document_failure_does_not_prevent_video_completion(self):
        with FakeSendspark(), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("outputs.generate_outputs", side_effect=RuntimeError("test")), \
                self.assertLogs(level="ERROR"):
            tasks = BackgroundTasks()
            outputs.create_outputs(self.firm["id"], outputs.OutputRequest(
                approval_id=self.approval["id"], formats=["pdf", "video"], request_key=uuid4()), tasks)
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        files = {f["format"]: f for f in run["files"]}
        self.assertEqual(run["status"], "partial")
        self.assertEqual(files["video"]["status"], "ready")
        self.assertEqual(files["pdf"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
