import io
import zipfile
from xml.etree import ElementTree


class DeckError(ValueError):
    pass


def validate_pptx(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32_000_000:
                raise DeckError("Presentation exceeds the prototype size limit.")
            slides = [p for p in archive.namelist() if p.startswith("ppt/slides/slide") and p.endswith(".xml")]
            if not slides:
                raise DeckError("The presentation contains no slides.")
            for path in ["[Content_Types].xml", "ppt/presentation.xml", *slides]:
                ElementTree.fromstring(archive.read(path))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise DeckError("The PowerPoint could not be read back.") from exc
