"""PDF inspection with pypdfium2. Runs on untrusted files, so it only reads structure and text."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

MAX_PAGES = 100
MAGIC = b"%PDF-"


class PdfRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    has_text_layer: bool
    sha256: str


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def inspect_pdf(path: Path) -> PdfInfo:
    with path.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise PdfRejected("not_pdf")
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as err:
        code = "encrypted_pdf" if err.err_code == pdfium_c.FPDF_ERR_PASSWORD else "corrupt_pdf"
        raise PdfRejected(code) from err
    try:
        count = len(document)
        if count < 1:
            raise PdfRejected("corrupt_pdf")
        if count > MAX_PAGES:
            raise PdfRejected("too_many_pages")
        has_text = False
        try:
            for index in range(count):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        if textpage.get_text_range().strip():
                            has_text = True
                            break
                    finally:
                        textpage.close()
                finally:
                    page.close()
        except pdfium.PdfiumError as err:
            raise PdfRejected("corrupt_pdf") from err
    finally:
        document.close()
    return PdfInfo(count, has_text, _digest(path))
