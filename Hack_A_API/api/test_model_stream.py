import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import MagicMock, Mock, patch

import prospects
from live_updates import firm_events
from model_stream import ModelStreamError, stream_response


class ModelStreamTests(unittest.TestCase):
    def test_progress_and_text_are_emitted_before_stream_is_complete(self):
        seen = []

        def events():
            yield Record(type="response.web_search_call.searching")
            self.assertEqual(seen[0], ("Searching public sources", None))
            yield Record(type="response.output_text.delta", delta="First result")
            self.assertTrue(any(delta == "First result" for _, delta in seen))
            yield Record(type="response.output_text.delta", delta=" plus detail")

        client = MagicMock()
        stream = client.responses.stream.return_value.__enter__.return_value
        stream.__iter__ = Mock(return_value=events())
        stream.get_final_response.return_value = Record(status="completed")
        stream_response(client, lambda stage, delta: seen.append((stage, delta)),
                        model="test", tools=[{"type": "web_search"}])
        self.assertEqual("".join(delta or "" for _, delta in seen), "First result plus detail")

    def test_failed_or_truncated_stream_never_reports_completion(self):
        client = MagicMock()
        stream = client.responses.stream.return_value.__enter__.return_value
        stream.__iter__ = Mock(return_value=iter([Record(type="response.failed")]))
        with self.assertRaises(ModelStreamError):
            stream_response(client)

    def test_sse_snapshot_contains_progress_but_no_file_contents(self):
        class Connected:
            async def is_disconnected(self):
                return False

        async def read(firm_id):
            stream = firm_events(Connected(), firm_id)
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="firm.example"))
            message = asyncio.run(read(firm["id"]))
        payload = json.loads(message.removeprefix("data: ").strip())
        self.assertEqual(payload["id"], firm["id"])
        self.assertIn("outputs", payload)
        self.assertNotIn("research_pages", payload)
