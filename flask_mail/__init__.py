"""Lightweight Flask-Mail compatibility shim for tests.

This module provides minimal Mail and Message implementations sufficient
for the application's usage in tests without requiring the external
Flask-Mail dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass
class Message:
    """Represents an outbound email message with minimal attributes."""

    subject: str = ""
    recipients: List[str] = field(default_factory=list)
    body: str | None = None
    sender: Optional[str] = None

    def __init__(
        self,
        subject: str = "",
        sender: Optional[str] = None,
        recipients: Optional[Iterable[str]] = None,
        body: Optional[str] = None,
        **kwargs,
    ) -> None:
        self.subject = subject
        self.sender = sender
        self.recipients = list(recipients or [])
        self.body = body
        for key, value in kwargs.items():
            setattr(self, key, value)


class Mail:
    """Stores sent messages and integrates with a Flask application."""

    def __init__(self, app=None) -> None:
        self.app = None
        self.sent_messages: List[Message] = []
        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        self.app = app
        app.extensions = getattr(app, "extensions", {})
        app.extensions["mail"] = self
        app.config.setdefault("MAIL_SUPPRESS_SEND", False)

    def send(self, message: Message) -> None:
        """Record the message send request."""

        self.sent_messages.append(message)


__all__ = ["Mail", "Message"]
