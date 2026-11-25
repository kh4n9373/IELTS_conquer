"""Google sign-in: checks an ID token from Google Identity Services."""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger("npa.google")

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleError(Exception):
    """`unavailable` means Google could not be reached; otherwise the token itself was refused."""

    def __init__(self, unavailable: bool = False) -> None:
        super().__init__("google_unavailable" if unavailable else "invalid_google_token")
        self.unavailable = unavailable


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    name: str
    picture: str | None = None


class GoogleVerifier(Protocol):
    client_id: str

    def verify(self, credential: str) -> GoogleIdentity: ...


class TokenInfoVerifier:
    """Google validates the signature and expiry; this class checks audience, issuer and email."""

    def __init__(self, client_id: str, client: httpx.Client | None = None) -> None:
        self.client_id = client_id
        self.client = client or httpx.Client(timeout=5)

    def verify(self, credential: str) -> GoogleIdentity:
        try:
            res = self.client.get(TOKENINFO_URL, params={"id_token": credential})
        except httpx.HTTPError as err:
            log.warning("Google tokeninfo unreachable: %s", err)
            raise GoogleError(unavailable=True) from err
        if res.status_code >= 500:
            raise GoogleError(unavailable=True)
        if res.status_code != 200:
            raise GoogleError
        try:
            claims = res.json()
        except ValueError as err:
            raise GoogleError from err
        email = claims.get("email")
        if (
            claims.get("aud") != self.client_id
            or claims.get("iss") not in ISSUERS
            or str(claims.get("email_verified")).lower() != "true"
            or not isinstance(email, str)
            or not email
        ):
            raise GoogleError
        name = claims.get("name")
        picture = claims.get("picture")
        return GoogleIdentity(
            email=email,
            name=name if isinstance(name, str) else "",
            picture=picture if isinstance(picture, str) and picture.startswith("https://") else None,
        )
