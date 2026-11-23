"""PDF inspection with pypdfium2. Runs on untrusted files, so it only reads structure and text."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdf
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


MAX_NODES = 5000


def _leaf_pages(path: Path) -> tuple[int, int | None]:
    """Counts real leaf pages by walking /Kids (bounded, cycle-safe) and reads the root /Count."""
    try:
        reader = pypdf.PdfReader(str(path), strict=False)
        trailer: Any = reader.trailer
        root: Any = trailer["/Root"]["/Pages"]
        declared = root.get("/Count")
        leaves, seen, stack = 0, set(), [root.indirect_reference or root]
        while stack:
            ref = stack.pop()
            marker = getattr(ref, "idnum", None)
            if marker is not None:
                if marker in seen:
                    raise PdfRejected("corrupt_pdf")
                seen.add(marker)
            if len(seen) > MAX_NODES:
                raise PdfRejected("corrupt_pdf")
            node = ref.get_object()
            kids = node.get("/Kids")
            if kids is None or node.get("/Type") == "/Page":
                leaves += 1
                if leaves > MAX_PAGES:
                    raise PdfRejected("too_many_pages")
            else:
                stack.extend(kids.get_object())
    except PdfRejected:
        raise
    except Exception as err:
        raise PdfRejected("corrupt_pdf") from err
    return leaves, None if declared is None else int(declared)


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
        leaves, declared = _leaf_pages(path)
        if leaves != count or (declared is not None and declared != leaves):
            raise PdfRejected("corrupt_pdf")
        has_text = False
        try:
            for index in range(count):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        if textpage.get_text_range().strip():
                            has_text = True
                    finally:
                        textpage.close()
                finally:
                    page.close()
        except pdfium.PdfiumError as err:
            raise PdfRejected("corrupt_pdf") from err
    finally:
        document.close()
    return PdfInfo(count, has_text, _digest(path))
