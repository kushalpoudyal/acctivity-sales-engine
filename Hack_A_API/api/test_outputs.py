import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException

import outputs
import prospects
from test_documents import COPY
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
        return outputs.OutputRequest(approval_id=self.approval["id"], formats=["pptx", "pdf"],
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

    def test_outputs_are_built_from_templates_and_survive_restart(self):
        tasks = BackgroundTasks()
        job = outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        with patch("outputs.write_copy", return_value=COPY) as write_copy:
            asyncio.run(tasks())
        self.assertEqual(write_copy.call_args.args[0]["people"][0]["full_name"], "Jane Smith")
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "ready")
        self.assertTrue(all(f["stage"] == "Download saved" and f["started_at"] and f["finished_at"]
                            for f in run["files"]))
        file = next(f for f in run["files"] if f["format"] == "pdf")
        data = outputs.download_output(self.firm["id"], job["id"], file["id"]).body
        outputs.validate_document(data, "pdf")
        with self.assertRaises(HTTPException):
            outputs.download_output(self.firm["id"] + 1, job["id"], file["id"])
        prospects.initialize()
        self.assertEqual(outputs.download_output(self.firm["id"], job["id"], file["id"]).body, data)

    def test_one_failing_template_keeps_the_other_formats(self):
        tasks = BackgroundTasks()
        outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        with patch("outputs.write_copy", return_value=COPY), patch.dict(
            outputs.RENDERERS, {"pdf": Mock(side_effect=RuntimeError("template bug"))}
        ), self.assertLogs(level="ERROR"):
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "partial")
        pdf = next(f for f in run["files"] if f["format"] == "pdf")
        self.assertEqual(pdf["status"], "failed")
        self.assertIn("Could not build the PDF", pdf["error"])

    def test_provider_failure_has_terminal_job_and_file_states(self):
        tasks = BackgroundTasks()
        outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        with patch("outputs.generate_outputs", side_effect=RuntimeError("test")), self.assertLogs(level="ERROR"):
            asyncio.run(tasks())
        run = prospects.get_firm(self.firm["id"])["outputs"][0]
        self.assertEqual(run["status"], "failed")
        self.assertTrue(all(f["status"] == "failed" and f["error"] for f in run["files"]))

    def test_delete_run_and_firm_are_hard_deletes_that_wait_for_running_work(self):
        tasks = BackgroundTasks()
        job = outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        with prospects.database() as db:
            db.execute("UPDATE output_jobs SET status='running' WHERE id=?", (job["id"],))
        for delete in (lambda: outputs.delete_output(self.firm["id"], job["id"]),
                       lambda: prospects.delete_firm(self.firm["id"])):
            with self.assertRaises(HTTPException) as running:
                delete()
            self.assertEqual(running.exception.status_code, 409)
        with prospects.database() as db:
            db.execute("UPDATE output_jobs SET status='ready' WHERE id=?", (job["id"],))
        self.assertEqual(outputs.delete_output(self.firm["id"], job["id"]), {"id": job["id"], "deleted": True})
        self.assertEqual(prospects.get_firm(self.firm["id"])["outputs"], [])
        with self.assertRaises(HTTPException):
            outputs.delete_output(self.firm["id"], job["id"])

        outputs.create_outputs(self.firm["id"], self.request_body(), BackgroundTasks())
        with prospects.database() as db:
            db.execute("UPDATE output_jobs SET status='ready'")
        prospects.delete_firm(self.firm["id"])
        with self.assertRaises(HTTPException) as gone:
            prospects.get_firm(self.firm["id"])
        self.assertEqual(gone.exception.status_code, 404)
        with prospects.database() as db:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            self.assertEqual({table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables},
                             {table: 0 for table in tables})

    def test_invalid_documents_are_not_downloadable(self):
        for format in ("pptx", "pdf"):
            with self.subTest(format=format), self.assertRaises(outputs.DeckError):
                outputs.validate_document(b"this is not a document", format)
