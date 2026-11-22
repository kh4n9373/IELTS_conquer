import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("npa.mail")


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    def send(self, mail: Mail) -> None: ...


class ConsoleMailer:
    """Development mailer. A real provider is chosen with the hosting decision (BA question 1)."""

    def send(self, mail: Mail) -> None:
        log.warning("[mail] to=%s subject=%s\n%s", mail.to, mail.subject, mail.text)
