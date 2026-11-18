import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from threading import Barrier

from fastapi import BackgroundTasks, HTTPException

import outputs
import prospects
from test_documents import COPY
from documents import RENDERERS
from test_prospects import PAGES, RECORDS


class OutputTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prospects, "DB_PATH", Path(folder) / "test.sqlite3"))
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-real-key"}))
        prospects.initialize()
        self.firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        with prospects.database() as db:
            prospects.store_research(db, self.firm["id"], PAGES, RECORDS)
        detail = prospects.get_firm(self.firm["id"])
        self.approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=detail["brief_token"], people=[p["id"] for p in detail["records"]["people"]]))

    def request_body(self, **kwargs):
        return outputs.OutputRequest(approval_id=self.approval["id"], formats=["docx", "pdf"],
                                     request_key=uuid4(), **kwargs)

    def test_repeated_request_reuses_job_and_approval_is_required(self):
        tasks = BackgroundTasks()
        body = self.request_body(instructions="Partner meeting")
        first = outputs.create_outputs(self.firm["id"], body, tasks)
        again = outputs.create_outputs(self.firm["id"], body, tasks)
        self.assertEqual(first, again)
        self.assertEqual(len(tasks.tasks), 1)
        with self.assertRaises(HTTPException) as conflict:
            outputs.create_outputs(self.firm["id"], body.model_copy(update={"instructions": "Changed"}), tasks)
        self.assertEqual(conflict.exception.status_code, 409)
        with self.assertRaises(HTTPException) as wrong_firm:
            outputs.create_outputs(self.firm["id"] + 1, self.request_body(), BackgroundTasks())
        self.assertEqual(wrong_firm.exception.status_code, 404)

    def test_outputs_are_built_in_sandboxes_and_survive_restart(self):
        tasks = BackgroundTasks()
        job = outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        def build(job, format, bundle, progress):
            progress("Uploading brand kit", "cntr-" + format)
            self.assertTrue(bundle.startswith(b"PK"))
            return RENDERERS[format](json.loads(job["snapshot"]), COPY, None)

        with patch("outputs.build_document", side_effect=build) as generate:
            asyncio.run(tasks())
        self.assertEqual(json.loads(generate.call_args.args[0]["snapshot"])["people"][0]["full_name"], "Jane Smith")
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "ready")
        self.assertTrue(all(f["stage"] == "Download saved" and f["started_at"] and f["finished_at"]
                            for f in run["files"]))
        file = next(f for f in run["files"] if f["format"] == "docx")
        data = outputs.download_output(self.firm["id"], job["id"], file["id"]).body
        outputs.validate_document(data, "docx")
        with self.assertRaises(HTTPException):
            outputs.download_output(self.firm["id"] + 1, job["id"], file["id"])
        prospects.initialize()
        self.assertEqual(outputs.download_output(self.firm["id"], job["id"], file["id"]).body, data)

    def test_one_failing_sandbox_keeps_the_other_formats(self):
        tasks = BackgroundTasks()
        outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        barrier = Barrier(2)

        def build(job, format, bundle, progress):
            barrier.wait(timeout=3)
            if format == "pdf":
                raise outputs.DeckError("PDF sandbox failure")
            return RENDERERS[format](json.loads(job["snapshot"]), COPY, None)

        with patch("outputs.build_document", side_effect=build), self.assertLogs(level="ERROR"):
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "partial")
        pdf = next(f for f in run["files"] if f["format"] == "pdf")
        self.assertEqual(pdf["status"], "failed")
        self.assertIn("PDF sandbox failure", pdf["error"])

    def test_provider_failure_has_terminal_job_and_file_states(self):
        tasks = BackgroundTasks()
        outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        with patch("outputs.generate_outputs", side_effect=RuntimeError("test")), self.assertLogs(level="ERROR"):
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "failed")
        self.assertTrue(all(f["status"] == "failed" and f["error"] for f in run["files"]))

    def test_invalid_documents_are_not_downloadable(self):
        for format in ("pptx", "docx", "pdf"):
            with self.subTest(format=format), self.assertRaises(outputs.DeckError):
                outputs.validate_document(b"this is not a document", format)
