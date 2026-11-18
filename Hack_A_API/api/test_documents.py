import io
import re
import unittest
import zipfile

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
        palette = {documents.SLATE, documents.BLUE, documents.PINK, documents.GOLD, documents.GREY, documents.WHITE}
        self.assertLessEqual(set(re.findall(r'<a:latin typeface="([^"]+)"', slides)), {"Lora", "Inter"})
        self.assertLessEqual(set(re.findall(r'<a:srgbClr val="([0-9A-F]{6})"', slides)), palette)
        media = [archive.read(name) for name in archive.namelist() if name.startswith("ppt/media/")]
        self.assertIn(documents.LOGO.read_bytes(), media)
        self.assertIn("Example Legal at a glance", slides)

    def test_word_document_sets_brand_fonts_and_fallbacks(self):
        archive = zipfile.ZipFile(io.BytesIO(documents.render_docx(SNAPSHOT, COPY, None)))
        styles, body = archive.read("word/styles.xml").decode(), archive.read("word/document.xml").decode()
        self.assertIn('w:ascii="Lora"', styles)
        self.assertIn('w:ascii="Inter"', styles)
        self.assertLessEqual(set(re.findall(r'w:ascii="([^"]+)"', body)), {"Lora", "Inter"})
        self.assertIn('w:name="Lora"><w:family w:val="roman"/>', archive.read("word/fontTable.xml").decode())
        self.assertIn("Pat Principal", body)

    def test_pdf_embeds_brand_fonts(self):
        reader = PdfReader(io.BytesIO(documents.render_pdf(SNAPSHOT, COPY, None)))
        fonts = {font.get_object()["/BaseFont"] for page in reader.pages
                 for font in page["/Resources"]["/Font"].values()}
        self.assertTrue(any("Lora" in name for name in fonts) and any("Inter" in name for name in fonts))
        self.assertIn("Welcome, Example Legal", reader.pages[0].extract_text())

    def test_most_senior_people_come_first(self):
        self.assertEqual([p["full_name"] for p in documents.key_people(SNAPSHOT, 2)], ["Pat Principal", "Sam Solicitor"])
