"""Simplified Flask-Mail compatible implementation used by the application.

The original project bundled a minimal test double that only stored emails
in memory.  That behaviour prevented production email delivery entirely
because this module shadowed the real :mod:`flask_mail` package.  The
implementation below keeps the lightweight API expected by the tests while
supporting real SMTP delivery when the application is not running in testing
mode and delivery has not been explicitly suppressed.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
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

    # -- helper methods -------------------------------------------------
    @staticmethod
    def _coerce_address(value) -> str:
        """Convert different sender/recipient formats to header strings."""

        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError("Email address tuples must contain name and address")
            return formataddr((value[0], value[1]))
        return str(value)

    def _resolve_sender(self, message: Message) -> str:
        sender = message.sender or self.app.config.get("MAIL_DEFAULT_SENDER")
        if not sender:
            raise ValueError("No sender configured for outgoing email")
        return self._coerce_address(sender)

    def _collect_recipients(self, message: Message) -> List[str]:
        recipients: List[str] = list(message.recipients or [])
        cc = getattr(message, "cc", None)
        if cc:
            recipients.extend(cc)
        bcc = getattr(message, "bcc", None)
        if bcc:
            recipients.extend(bcc)
        if not recipients:
            raise ValueError("At least one recipient must be specified")
        parsed: List[str] = []
        for addr in recipients:
            formatted = self._coerce_address(addr)
            email = parseaddr(formatted)[1]
            if not email:
                raise ValueError(f"Invalid email address: {addr}")
            parsed.append(email)
        return parsed

    def _build_email(self, message: Message) -> tuple[EmailMessage, List[str], str]:
        email_message = EmailMessage()
        email_message["Subject"] = message.subject or ""

        sender_header = self._resolve_sender(message)
        email_message["From"] = sender_header

        recipients = [self._coerce_address(addr) for addr in (message.recipients or [])]
        if not recipients:
            raise ValueError("No recipients specified for outgoing email")
        email_message["To"] = ", ".join(recipients)

        cc = getattr(message, "cc", None)
        if cc:
            email_message["Cc"] = ", ".join(self._coerce_address(addr) for addr in cc)

        reply_to = getattr(message, "reply_to", None)
        if reply_to:
            if isinstance(reply_to, (list, tuple)) and not isinstance(reply_to, str):
                addresses = [self._coerce_address(addr) for addr in reply_to]
                email_message["Reply-To"] = ", ".join(addresses)
            else:
                email_message["Reply-To"] = self._coerce_address(reply_to)

        body = message.body or ""
        html = getattr(message, "html", None)
        if html:
            email_message.set_content(body)
            email_message.add_alternative(html, subtype="html")
        else:
            email_message.set_content(body)

        all_recipients = self._collect_recipients(message)
        sender_address = parseaddr(sender_header)[1]
        if not sender_address:
            raise ValueError("Invalid sender email address")
        return email_message, all_recipients, sender_address

    def _deliver(self, email_message: EmailMessage, sender: str, recipients: List[str]) -> None:
        config = self.app.config
        host = config.get("MAIL_SERVER", "localhost")
        port = int(config.get("MAIL_PORT", 25) or 25)
        username = config.get("MAIL_USERNAME")
        password = config.get("MAIL_PASSWORD")
        use_tls = bool(config.get("MAIL_USE_TLS", False))
        use_ssl = bool(config.get("MAIL_USE_SSL", False))
        timeout = config.get("MAIL_TIMEOUT")

        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=timeout) as server:
            server.ehlo()
            if use_tls and not use_ssl:
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password or "")
            server.send_message(email_message, from_addr=sender, to_addrs=recipients)

    # -- public API -----------------------------------------------------
    def send(self, message: Message) -> None:
        """Deliver the message via SMTP unless suppressed."""

        if self.app is None:
            raise RuntimeError("The Mail extension has not been initialised with an app")

        self.sent_messages.append(message)

        if self.app.testing or self.app.config.get("MAIL_SUPPRESS_SEND"):
            return

        email_message, recipients, sender = self._build_email(message)
        self._deliver(email_message, sender, recipients)


__all__ = ["Mail", "Message"]
