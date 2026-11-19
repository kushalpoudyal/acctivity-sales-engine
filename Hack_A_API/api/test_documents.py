import io
import re
import unittest
import zipfile
from types import SimpleNamespace as Record
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

import documents
from outputs import validate_document

SOURCE = {"url": "https://firm.example/about", "title": "About"}
SNAPSHOT = {
    "version": 2, "firm": "Example Legal", "domain": "firm.example",
    "facts": [{"kind": "overview", "title": "Coastal firm", "detail": "A firm with two offices.", "source": SOURCE},
              {"kind": "history", "title": "Merged in 2025", "detail": "Merged with a nearby practice.", "source": SOURCE}],
    "practice_areas": [{"name": f"Area {n}", "description": "Advice through to court.", "services": ["Advice"],
                        "source": SOURCE} for n in range(8)],
    "people": [{"full_name": name, "position": position, "practice_areas": ["Area 1"], "biography": None,
                "qualifications": [], "public_work_email": None, "public_work_phone": None, "source": SOURCE}
               for name, position in [("Sam Solicitor", "Solicitor"), ("Pat Principal", "Principal"), ("No Role", None)]],
    "offices": [{"name": "Northtown", "address": "1 Main St", "phone": None, "email": None, "source": SOURCE}],
}
POINT = documents.Point(heading="A short heading", detail="One plain sentence about a proposal.")
COPY = documents.SalesCopy(title="A calmer practice", subtitle="A proposal for discussion.",
                           firm_summary="Example Legal is a firm with two offices.", opportunities=[POINT] * 3,
                           pilot_steps=[POINT] * 4, next_steps=["Book a demo"] * 3,
                           welcome_intro="Thank you for your time.", demo_agenda=[POINT] * 3)



def contact(key, name, position, email):
    return {"key": key, "category": "Apollo: contacts", "title": name, "retrieved_at": "2026-11-18",
            "person": {"full_name": name, "position": position, "apollo_email": email, "email_status": "verified",
                       "linkedin_url": "https://linkedin.example/in/" + key, "match_confidence": "high"}}


# Approved Apollo findings: one technology, a contact matching a website person and an Apollo-only contact.
APOLLO_SNAPSHOT = {**SNAPSHOT, "apollo": [
    {"key": "apollo:tech:xero", "category": "Apollo: tech stack", "title": "Xero", "retrieved_at": "2026-11-18"},
    contact("ap-pat", "PAT PRINCIPAL", "Director", "pat@firm.example"),
    contact("ap-ann", "Ann Apollo", "Practice Manager", "ann@firm.example")]}

def webp_logo() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 80), "#123456").save(buffer, "WEBP")
    return buffer.getvalue()


class TemplateTests(unittest.TestCase):
    def test_every_format_builds_a_valid_branded_file_with_a_webp_firm_logo(self):
        for format, render in documents.RENDERERS.items():
            with self.subTest(format=format):
                validate_document(render(SNAPSHOT, COPY, webp_logo()), format)

    def test_deck_uses_only_brand_fonts_colours_and_logo(self):
        archive = zipfile.ZipFile(io.BytesIO(documents.render_pptx(SNAPSHOT, COPY, None)))
        slides = "".join(archive.read(name).decode() for name in archive.namelist()
                         if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
        palette = {documents.SLATE, documents.BLUE, documents.PINK, documents.GOLD, documents.GREY, documents.WHITE,
                   documents.NAVY, documents.INK, documents.PANEL, documents.RULE}
        self.assertLessEqual(set(re.findall(r'<a:latin typeface="([^"]+)"', slides)), {"Lora", "Inter"})
        self.assertLessEqual(set(re.findall(r'<a:srgbClr val="([0-9A-F]{6})"', slides)), palette)
        media = [archive.read(name) for name in archive.namelist() if name.startswith("ppt/media/")]
        self.assertIn(documents.LOGO.read_bytes(), media)
        # Template rule: illustrations sit on plain pages, never over the watercolour background.
        self.assertNotIn((documents.BRAND / "background/acctivity-background.jpg").read_bytes(), media)
        self.assertIn("Example Legal at a glance", slides)

    def test_pdf_embeds_brand_fonts(self):
        reader = PdfReader(io.BytesIO(documents.render_pdf(SNAPSHOT, COPY, None)))
        fonts = {font.get_object()["/BaseFont"] for page in reader.pages
                 for font in page["/Resources"]["/Font"].values()}
        self.assertTrue(any("Lora" in name for name in fonts) and any("Inter" in name for name in fonts))
        self.assertIn("Welcome, Example Legal", reader.pages[0].extract_text())

    def test_most_senior_people_come_first(self):
        self.assertEqual([p["full_name"] for p in documents.key_people(SNAPSHOT, 2)], ["Pat Principal", "Sam Solicitor"])

    def test_deck_adds_approved_apollo_contacts_and_reported_data(self):
        archive = zipfile.ZipFile(io.BytesIO(documents.render_pptx(APOLLO_SNAPSHOT, COPY, None)))
        slides = "".join(archive.read(name).decode() for name in archive.namelist()
                         if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
        self.assertEqual((slides.count("Pat Principal"), slides.count("PAT PRINCIPAL")), (1, 0))
        self.assertIn("Ann Apollo", slides)
        self.assertIn("as reported by Apollo", slides)
        self.assertIn("Xero", slides)

    def test_copy_call_gets_apollo_names_but_no_emails_or_links(self):
        with patch("documents.openai.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            stream = client.responses.stream.return_value.__enter__.return_value
            stream.get_final_response.return_value = Record(status="completed", output_parsed=COPY)
            documents.write_copy(APOLLO_SNAPSHOT, "")
        sent = client.responses.stream.call_args.kwargs["input"]
        self.assertIn("Xero", sent)
        self.assertIn("Ann Apollo", sent)
        self.assertNotIn("@firm.example", sent)
        self.assertNotIn("linkedin", sent)
