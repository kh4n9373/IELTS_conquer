"""Provider-neutral OCR types. Errors carry a fixed code and never any provider text."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

OcrState = Literal["pending", "running", "done", "failed"]
ErrorCode = Literal[
    "auth_failed",
    "quota_exceeded",
    "provider_error",
    "provider_timeout",
    "bad_result",
    "storage_error",
    "lease_expired",
    "parse_failed",
]


class OcrError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code)
        self.code: ErrorCode = code


class OcrJobExpired(OcrError):
    """The provider no longer knows this job, so it has to be submitted again."""

    def __init__(self) -> None:
        super().__init__("provider_error")


@dataclass(frozen=True)
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float] | None
    confidence: float | None


@dataclass(frozen=True)
class OcrPage:
    page: int
    text: str
    lines: list[OcrLine]


@dataclass(frozen=True)
class OcrStatus:
    job_id: str
    state: OcrState
    # A signed URL: kept out of repr so it cannot reach a log by accident.
    result_url: str | None = field(default=None, repr=False)


class OcrProvider(Protocol):
    def submit(self, path: Path) -> str: ...
    def status(self, job_id: str) -> OcrStatus: ...
    def pages(self, status: OcrStatus) -> list[OcrPage]: ...
