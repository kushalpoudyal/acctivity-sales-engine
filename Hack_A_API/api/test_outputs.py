import asyncio
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException

import outputs
import prospects


def word_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document><p>Firm proposal</p></document>")
    return buffer.getvalue()


class OutputTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prospects, "DB_PATH", Path(folder) / "test.sqlite3"))
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-real-key"}))
        prospects.initialize()
        self.firm = prospects.add_firm(prospects.Intake(website="firm.example"))
        finding = {"text": "Fictional practice", "sources": [{"url": "https://firm.example/"}]}
        with prospects.database() as db:
            db.execute("UPDATE firms SET brief=? WHERE id=?", (json.dumps([finding]), self.firm["id"]))
        detail = prospects.get_firm(self.firm["id"])
        self.approval = prospects.approve(self.firm["id"], prospects.Approval(
            brief_token=detail["brief_token"], findings=[0]))

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

    def test_partial_run_saves_valid_file_with_missing_size_and_cleans_container(self):
        tasks = BackgroundTasks()
        job = outputs.create_outputs(self.firm["id"], self.request_body(), tasks)
        docx = word_bytes()
        with patch("outputs.openai.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.containers.create.return_value = Record(id="cntr-test")
            client.responses.create.return_value = Record(status="completed")
            client.containers.files.list.return_value = [
                Record(id="doc", path="/mnt/data/proposal.docx", bytes=None)]
            client.containers.files.content.retrieve.return_value.read.return_value = docx
            asyncio.run(tasks())
            client.containers.delete.assert_called_once_with("cntr-test")
        detail = prospects.get_firm(self.firm["id"])
        run = detail["outputs"][0]
        self.assertEqual(run["status"], "partial")
        file = next(f for f in run["files"] if f["format"] == "docx")
        self.assertEqual(outputs.download_output(self.firm["id"], job["id"], file["id"]).body, docx)
        with self.assertRaises(HTTPException):
            outputs.download_output(self.firm["id"] + 1, job["id"], file["id"])
        prospects.initialize()
        self.assertEqual(outputs.download_output(self.firm["id"], job["id"], file["id"]).body, docx)

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
