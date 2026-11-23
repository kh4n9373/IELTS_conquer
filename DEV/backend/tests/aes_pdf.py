"""Hand-built AES-128 (AESV2) PDF with an empty user password and object streams.

Pure Python so the fixture exists without any crypto library: the code under test must bring its own.
"""

import hashlib
import struct
import zlib

PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def xt(a):
    return ((a << 1) ^ 0x1B) & 255 if a & 128 else a << 1


SBOX = [0] * 256
p = q = 1
while True:
    p = p ^ xt(p)
    q ^= (q << 1) & 255
    q ^= (q << 2) & 255
    q ^= (q << 4) & 255
    if q & 128:
        q ^= 9
    r = lambda v, n: ((v << n) | (v >> (8 - n))) & 255
    SBOX[p] = q ^ r(q, 1) ^ r(q, 2) ^ r(q, 3) ^ r(q, 4) ^ 0x63
    if p == 1:
        break
SBOX[0] = 0x63


def expand(key):
    w = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rc = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[t[1]] ^ rc, SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]
            rc = xt(rc)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[i : i + 4], []) for i in range(0, 44, 4)]


def block(b, rks):
    s = [x ^ k for x, k in zip(b, rks[0])]
    for rnd in range(1, 11):
        s = [SBOX[x] for x in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if rnd < 10:
            n = []
            for c in range(0, 16, 4):
                a = s[c : c + 4]
                n += [
                    xt(a[i]) ^ xt(a[(i + 1) % 4]) ^ a[(i + 1) % 4] ^ a[(i + 2) % 4] ^ a[(i + 3) % 4]
                    for i in range(4)
                ]
            s = n
        s = [x ^ k for x, k in zip(s, rks[rnd])]
    return bytes(s)


assert block(bytes(range(16)), expand(bytes(range(16)))).hex() == "0a940bb5416ef045f1c39458c653ea5a"


def cbc(key, data):
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    iv = b"I" * 16
    prev = iv
    out = bytearray(iv)
    rks = expand(key)
    for i in range(0, len(data), 16):
        prev = block(bytes(x ^ y for x, y in zip(data[i : i + 16], prev)), rks)
        out += prev
    return bytes(out)


def make_aes_pdf() -> bytes:
    aes = True
    fid = b"0123456789abcdef"
    o = b"O" * 32
    perm = -44
    k = hashlib.md5(PAD + o + struct.pack("<i", perm) + fid).digest()
    for _ in range(50):
        k = hashlib.md5(k[:16]).digest()
    k = k[:16]
    u = rc4(k, hashlib.md5(PAD + fid).digest())
    for i in range(1, 20):
        u = rc4(bytes(b ^ i for b in k), u)
    u += b"\0" * 16
    items = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"),
    ]
    head, body = b"", b""
    for n, d in items:
        head += b"%d %d " % (n, len(body))
        body += d + b" "
    raw = zlib.compress(head + body)
    if aes:
        ok = hashlib.md5(k + struct.pack("<I", 5)[:3] + b"\0\0" + b"sAlT").digest()[:16]
        raw = cbc(ok, raw)
    out = bytearray(b"%PDF-1.6\n")
    off = {}
    off[5] = len(out)
    out += (
        b"5 0 obj\n<< /Type /ObjStm /N 3 /First %d /Filter /FlateDecode /Length %d >>\nstream\n"
        % (len(head), len(raw))
        + raw
        + b"\nendstream\nendobj\n"
    )
    extra = b""
    if aes:
        off[4] = len(out)
        out += (
            b"4 0 obj\n<< /Filter /Standard /V 4 /R 4 /Length 128 /P %d /O <%s> /U <%s> /CF << /StdCF << /CFM /AESV2 /AuthEvent /DocOpen /Length 16 >> >> /StmF /StdCF /StrF /StdCF >>\nendobj\n"
            % (perm, o.hex().encode(), u.hex().encode())
        )
        extra = b"/Encrypt 4 0 R "
    x = len(out)
    off[6] = x
    rows = b"\0\0\0\0"
    for n in range(1, 7):
        if n in (1, 2, 3):
            rows += b"\2" + struct.pack(">H", 5) + bytes([n - 1])
        elif n in off:
            rows += b"\1" + struct.pack(">H", off[n]) + b"\0"
        else:
            rows += b"\0\0\0\0"
    out += (
        b"6 0 obj\n<< /Type /XRef /Size 7 /W [1 2 1] /Root 1 0 R %s/ID [<%s> <%s>] /Length %d >>\nstream\n"
        % (extra, fid.hex().encode(), fid.hex().encode(), len(rows))
        + rows
        + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % x
    )
    return bytes(out)
