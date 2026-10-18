"""Outgoing email for sign-in links.

Resend's HTTP API when RESEND_API_KEY is set, sending from AUTH_EMAIL_FROM.
Otherwise the message (link included) is written to the log instead, so the
whole sign-in flow can be tried locally without sending anything.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

_log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


class ConsoleMailer:
    def send(self, to: str, subject: str, text: str) -> None:
        _log.warning("email not sent (no RESEND_API_KEY)\nTo: %s\nSubject: %s\n\n%s", to, subject, text)


class ResendMailer:
    URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str) -> None:
        self.api_key = api_key
        self.sender = sender

    def send(self, to: str, subject: str, text: str) -> None:
        r = httpx.post(
            self.URL, timeout=15,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"from": self.sender, "to": [to], "subject": subject, "text": text})
        r.raise_for_status()


def from_env() -> Mailer:
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        return ConsoleMailer()
    sender = os.environ.get("AUTH_EMAIL_FROM", "").strip() or "Sunnah Semantic Search <login@searchthehadith.fyi>"
    return ResendMailer(key, sender)
