"""Test doubles: an in-memory file store and generated PDFs (no fixture files on disk)."""

import hashlib
from pathlib import Path


class MemoryFileStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_delete = False

    def put(self, key: str, path: Path) -> None:
        self.objects[key] = path.read_bytes()

    def get(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])

    def delete(self, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.objects.pop(key, None)

    def signed_url(self, key: str, seconds: int) -> str:
        # Opaque on purpose: a real URL carries the key, the API must not add it anywhere else.
        return f"https://files.test/signed/{hashlib.sha256(key.encode()).hexdigest()}?expires={seconds}"


def make_pdf(pages: int = 1, text: str | None = "Hello reading passage", *, encrypted: bool = False) -> bytes:
    """A small valid PDF. `text=None` gives pages with no text, like a scan."""
    objects: list[bytes] = []
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(pages):
        contents = 5 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents {contents} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        stream = b"" if text is None else f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    trailer_extra = ""
    if encrypted:
        objects.append(
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 /O <"
            + b"11" * 32
            + b"> /U <"
            + b"22" * 32
            + b"> >>"
        )
        trailer_extra = f" /Encrypt {len(objects)} 0 R /ID [<{'aa' * 16}> <{'aa' * 16}>]"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R{trailer_extra} >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
