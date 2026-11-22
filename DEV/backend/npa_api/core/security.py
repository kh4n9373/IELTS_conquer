"""Password hashing and random tokens."""

import base64
import binascii
import hashlib
import hmac
import secrets
import unicodedata

N = 2**15
R = 8
P = 1
KEY_LENGTH = 32
MAX_MEMORY = 64 * 1024 * 1024

# No 0/O or 1/I, so codes survive being read aloud in class.
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    normalized = unicodedata.normalize("NFKC", password).encode()
    return hashlib.scrypt(normalized, salt=salt, n=n, r=r, p=p, maxmem=MAX_MEMORY, dklen=KEY_LENGTH)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = _derive(password, salt, N, R, P)
    return f"scrypt${N}${R}${P}${_b64encode(salt)}${_b64encode(key)}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _b64decode(parts[4]), _b64decode(parts[5])
        actual = _derive(password, salt, n, r, p)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(expected, actual)


_DUMMY_HASH = hash_password(secrets.token_hex(16))


def burn_password_check(password: str) -> None:
    """Spends the same work for unknown emails, so timing does not reveal accounts."""
    verify_password(password, _DUMMY_HASH)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_invite_code(length: int = 8) -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))
