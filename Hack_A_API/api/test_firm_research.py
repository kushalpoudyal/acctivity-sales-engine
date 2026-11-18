import unittest
from unittest.mock import Mock, patch
from pydantic import ValidationError

from firm_research import (Finding, FirmProfile, Person, ResearchConnectionError, ResearchError, canonical_url,
                           crawl_site, download_logo, extract_profile, fetch_public, fetch_resource, grounded_findings)


class FirmResearchTests(unittest.TestCase):
    def test_links_stay_on_firm_and_remove_tracking(self):
        self.assertEqual(canonical_url("http://www.firm.example/team/?x=1#bio", "firm.example"),
                         "https://firm.example/team/")
        for url in ["https://evil.example/", "https://firm.example.evil.example/",
                    "https://user@firm.example/", "https://firm.example:9001/"]:
            self.assertIsNone(canonical_url(url, "firm.example"))

    def test_private_dns_is_rejected_before_connection(self):
        with patch("firm_research.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443))]), patch("firm_research.socket.create_connection") as connect:
            with self.assertRaises(ResearchError):
                fetch_public("https://firm.example/", "firm.example")
            connect.assert_not_called()

    def test_crawl_reads_bios_not_external_links_or_disallowed_pages(self):
        html = {
            "/robots.txt": "User-agent: *\nDisallow: /private/",
            "/": '<a href="/news/">News</a><a href="/our-team/">Team</a><a href="/private/">Private</a><a href="https://elsewhere.example/">External</a>',
            "/our-team/": '<title>Lawyers</title><a href="/our-team/jane/">Jane</a>',
            "/our-team/jane/": "<h1>Jane Smith</h1><p>Jane is a partner specialising in family law.</p>",
            "/news/": "<p>Public news</p>",
        }
        calls = []

        def fake(url, domain):
            calls.append(url)
            return url, "text/html", html[url.replace("https://firm.example", "")]

        with patch("firm_research.fetch_public", side_effect=fake), patch("firm_research.time.sleep"):
            pages, warnings = crawl_site("firm.example", lambda n: None)
        self.assertTrue(any("Jane is a partner" in p["text"] for p in pages))
        self.assertLess(calls.index("https://firm.example/our-team/jane/"),
                        calls.index("https://firm.example/news/"))
        self.assertNotIn("https://firm.example/private/", calls)
        self.assertEqual(len(warnings), 1)

    def test_unverifiable_findings_are_excluded(self):
        good = Finding(category="Lawyers and staff", title="Jane Smith",
                       detail="Jane is a partner.", page_id=1,
                       evidence_quote="Jane Smith is a family law partner.")
        bad = good.model_copy(update={"evidence_quote": "Jane was admitted to practice in 1990."})
        findings, rejected = grounded_findings(FirmProfile(findings=[good, bad]),
            [{"id": 1, "url": "https://firm.example/jane", "title": "Jane",
              "text": "Biography: Jane Smith is a family law partner."}])
        self.assertEqual(len(findings), 1)
        self.assertEqual(rejected, 1)
        self.assertEqual(findings[0]["title"], "Jane Smith")

    def test_staff_role_and_contact_must_be_published(self):
        person = Person(full_name="Jane Smith", position="Partner", biography=None,
                        qualifications=[], practice_areas=["Family law"],
                        public_work_email="guessed@firm.example", public_work_phone=None)
        finding = Finding(category="Lawyers and staff", title="Jane Smith",
                          detail="Jane Smith is a Partner.", page_id=1,
                          evidence_quote="Jane Smith is a Partner.", person=person)
        findings, rejected = grounded_findings(FirmProfile(findings=[finding]),
            [{"id": 1, "url": "https://firm.example/jane", "title": "Jane",
              "text": "Jane Smith is a Partner."}])
        self.assertEqual(findings[0]["person"]["position"], "Partner")
        self.assertIsNone(findings[0]["person"]["public_work_email"])
        self.assertEqual(rejected, 1)

    def test_logo_rejects_html_disguised_as_png(self):
        with patch("firm_research.fetch_resource", return_value=(
            "https://firm.example/logo.png", "image/png", b"<script>bad</script>")):
            with self.assertRaises(ResearchError):
                download_logo("https://firm.example/logo.png", "firm.example")

    def test_temporary_connection_failure_retries_without_skipping_robots(self):
        expected = ("https://firm.example/robots.txt", "text/plain", b"User-agent: *")
        with patch("firm_research._fetch_resource", side_effect=[
            ResearchConnectionError("Connection reset"), expected
        ]) as request, patch("firm_research.time.sleep"), self.assertLogs(level="WARNING"):
            self.assertEqual(fetch_resource(expected[0], "firm.example"), expected)
        self.assertEqual(request.call_count, 2)

    def test_policy_failure_does_not_retry(self):
        with patch("firm_research._fetch_resource", side_effect=ResearchError("Private address")) as request:
            with self.assertRaises(ResearchError):
                fetch_resource("https://firm.example/", "firm.example")
        self.assertEqual(request.call_count, 1)

    def test_crawl_stops_at_twelve_pages(self):
        def fake(url, domain):
            if url.endswith("/robots.txt"):
                return url, "text/plain", "User-agent: *\nAllow: /"
            links = "".join(f'<a href="/team/{i}/">Lawyer {i}</a>' for i in range(30))
            return url, "text/html", "<p>Public website content</p>" + (links if url.endswith(".example/") else "")

        with patch("firm_research.fetch_public", side_effect=fake), patch("firm_research.time.sleep"):
            pages, warnings = crawl_site("firm.example", lambda n: None)
        self.assertEqual(len(pages), 12)
        self.assertIn("Crawl limit reached; some pages were not read.", warnings)

    def test_invalid_model_output_is_reported_as_validation_not_timeout(self):
        try:
            FirmProfile.model_validate({"findings": [{"category": "unexpected"}]})
        except ValidationError as exc:
            error = exc
        client = Mock()
        client.responses.parse.side_effect = error
        with self.assertLogs(level="ERROR"), self.assertRaisesRegex(ResearchError, "invalid structured firm data"):
            extract_profile(client, "test-model", [])
