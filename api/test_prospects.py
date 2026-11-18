import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Record
from unittest.mock import patch

from fastapi import HTTPException

import prospects


class DirectoryTests(unittest.TestCase):
    def test_domain_deduplication_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            prospects, "DB_PATH", Path(folder) / "test.sqlite3"
        ):
            prospects.initialize()
            firm = prospects.add_firm(prospects.Intake(website="https://www.firm.example/about"))
            prospects.initialize()
            self.assertEqual(prospects.list_firms()[0]["id"], firm["id"])
            with self.assertRaises(HTTPException) as duplicate:
                prospects.add_firm(prospects.Intake(website="firm.example"))
            self.assertEqual(duplicate.exception.status_code, 409)

    def test_only_first_party_cited_lines_are_saved(self):
        part = Record(type="output_text", text="Offices\nGuess\nOther",
                      annotations=[Record(type="url_citation", start_index=1,
                                          url="https://firm.example/about", title="Team"),
                                   Record(type="url_citation", start_index=15,
                                          url="https://notfirm.example/", title="Other")])
        response = Record(status="completed", output=[
            Record(type="web_search_call", status="completed"),
            Record(type="message", content=[part])])
        result = prospects.cited_passages(response, "firm.example")
        self.assertEqual(result, [{"text": "Offices", "sources": [
            {"url": "https://firm.example/about", "title": "Team"}]}])
