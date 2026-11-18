import io
import unittest
import zipfile
from types import SimpleNamespace as Record
from unittest.mock import patch

from container_documents import asset_bundle, build_document


class ContainerDocumentTests(unittest.TestCase):
    def test_brand_archive_contains_actual_assets_not_server_paths(self):
        with zipfile.ZipFile(io.BytesIO(asset_bundle())) as archive:
            names = archive.namelist()
        self.assertIn("brand/BRAND.md", names)
        self.assertTrue(any(name.endswith(".ttf") for name in names))
        self.assertTrue(any(name.startswith("brand/logo/") for name in names))
        self.assertTrue(any(name.startswith("brand/images/") for name in names))
        self.assertFalse(any(name.endswith(".mp4") or ".." in name for name in names))

    def test_all_inputs_uploaded_before_stream_and_cleanup_even_on_failure(self):
        job = {"id": 1, "snapshot": '{"firm":"Demo Legal"}', "instructions": "Partner demo",
               "logo": b"logo-fixture", "logo_mime": "image/png"}
        with patch("container_documents.openai.OpenAI") as factory, \
                patch("container_documents.stream_response") as stream:
            client = factory.return_value.__enter__.return_value
            client.containers.create.return_value = Record(id="cntr-test")
            client.containers.files.list.return_value = [
                Record(id="cfile", path="/mnt/data/proposal.docx", bytes=None)]
            client.containers.files.content.retrieve.return_value.read.return_value = b"document-fixture"

            def response(*args, **kwargs):
                names = [call.kwargs["file"][0] for call in client.containers.files.create.call_args_list]
                self.assertEqual(names, ["brand-assets.zip", "approved-firm.json", "firm-logo.png"])
                self.assertEqual(kwargs["tools"], [{"type": "code_interpreter", "container": "cntr-test"}])

            stream.side_effect = response
            self.assertEqual(build_document(job, "docx", b"archive-fixture", lambda *args: None),
                             b"document-fixture")
            client.containers.delete.assert_called_once_with("cntr-test")
            client.containers.delete.reset_mock()
            stream.side_effect = RuntimeError("provider disconnected")
            with self.assertRaises(RuntimeError):
                build_document(job, "docx", b"archive-fixture", lambda *args: None)
            client.containers.delete.assert_called_once_with("cntr-test")
