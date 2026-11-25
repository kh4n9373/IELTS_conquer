"""Test doubles: an in-memory file store and generated PDFs (no fixture files on disk)."""

import hashlib
import io
import struct
from pathlib import Path

from PIL import Image


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


def make_pdf(
    pages: int = 1,
    text: str | None = "Hello reading passage",
    *,
    encrypted: bool = False,
    declared: int | None = None,
    page_extra: str = "",
) -> bytes:
    """A small valid PDF. `text=None` gives pages with no text, like a scan.
    `page_extra` adds page dictionary entries, such as /Rotate or /CropBox."""
    objects: list[bytes] = []
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {pages if declared is None else declared} >>".encode()
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(pages):
        contents = 5 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] {page_extra} /Contents {contents} 0 R "
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


def _half_black(mode: str) -> Image.Image:
    """An invented 64x64 image: the top half black, the bottom half white."""
    image = Image.new(mode, (64, 64), 255 if mode == "L" else 1)
    image.paste(0, (0, 0, 64, 32))
    return image


def _image_pdf(filter_name: str, data: bytes, bits: int) -> bytes:
    content = b"q 180 0 0 180 10 10 cm /Im Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /XObject << /Im 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        f"<< /Type /XObject /Subtype /Image /Width 64 /Height 64 /Filter /{filter_name} "
        f"/ColorSpace /DeviceGray /BitsPerComponent {bits} /Length {len(data)} >>\nstream\n".encode()
        + data
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.5\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def make_jpx_pdf() -> bytes:
    """One page whose picture is JPEG2000 (needs PDF.js's OpenJPEG decoder): top half black."""
    buffer = io.BytesIO()
    _half_black("L").save(buffer, "JPEG2000")
    return _image_pdf("JPXDecode", buffer.getvalue(), 8)


def make_jbig2_pdf() -> bytes:
    """One page whose picture is JBIG2 (MMR generic region, needs PDF.js's JBIG2 decoder): top half black."""
    tiff = io.BytesIO()
    _half_black("1").save(tiff, "TIFF", compression="group4")
    reread = Image.open(io.BytesIO(tiff.getvalue()))
    start, size = reread.tag_v2[273], reread.tag_v2[279]
    start = start[0] if isinstance(start, tuple) else start
    size = size[0] if isinstance(size, tuple) else size
    mmr = tiff.getvalue()[start : start + size]

    def segment(number: int, kind: int, body: bytes) -> bytes:
        return struct.pack(">IBBBI", number, kind, 0, 1, len(body)) + body

    page_info = struct.pack(">IIIIBH", 64, 64, 0, 0, 0, 0)
    region = struct.pack(">IIIIB", 64, 64, 0, 0, 0) + bytes([1]) + mmr
    return _image_pdf("JBIG2Decode", segment(0, 48, page_info) + segment(1, 38, region), 1)


class FakeGoogle:
    """Accepts `ok:<email>:<name>` credentials; anything else is refused."""

    client_id = "test-client"

    def verify(self, credential: str):  # type: ignore[no-untyped-def]
        from npa_api.core.google import GoogleError, GoogleIdentity

        kind, _, rest = credential.partition(":")
        if kind == "down":
            raise GoogleError(unavailable=True)
        if kind != "ok":
            raise GoogleError
        email, _, name = rest.partition(":")
        return GoogleIdentity(email=email, name=name, picture=f"https://pic.test/{email.lower()}.png")
