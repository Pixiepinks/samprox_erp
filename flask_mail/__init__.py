"""Simplified Flask-Mail compatible implementation used by the application.

The original project bundled a minimal test double that only stored emails
in memory.  That behaviour prevented production email delivery entirely
because this module shadowed the real :mod:`flask_mail` package.  The
implementation below keeps the lightweight API expected by the tests while
supporting real SMTP delivery when the application is not running in testing
mode and delivery has not been explicitly suppressed.
"""
from __future__ import annotations

import errno
import smtplib
import ssl
import socket
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr
from typing import Iterable, List, Optional, Sequence


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

    @staticmethod
    def _address_sources(addresses) -> List[str]:
        """Convert various address container types into a list of strings."""

        if not addresses:
            return []

        if isinstance(addresses, str):
            return [addresses]

        if isinstance(addresses, Iterable):
            sources: List[str] = []
            for item in addresses:
                if not item:
                    continue
                if isinstance(item, str):
                    sources.append(item)
                elif isinstance(item, Sequence):
                    if len(item) != 2:
                        raise ValueError(
                            "Email address tuples must contain name and address"
                        )
                    sources.append(formataddr((item[0], item[1])))
                else:
                    sources.append(str(item))
            return sources

        return [str(addresses)]

    def _parse_addresses(self, addresses) -> List[tuple[str, str]]:
        """Parse addresses into ``(name, email)`` tuples."""

        sources = self._address_sources(addresses)
        if not sources:
            return []

        parsed_pairs = getaddresses(sources)
        parsed: List[tuple[str, str]] = []
        for name, email in parsed_pairs:
            clean_email = (email or "").strip()
            if not clean_email:
                raise ValueError(f"Invalid email address: {name or email}")
            parsed.append((name.strip(), clean_email))
        return parsed

    def _collect_recipients(self, message: Message) -> List[str]:
        recipients = self._parse_addresses(getattr(message, "recipients", None))
        cc = self._parse_addresses(getattr(message, "cc", None))
        bcc = self._parse_addresses(getattr(message, "bcc", None))

        if not (recipients or cc or bcc):
            raise ValueError("At least one recipient must be specified")

        all_recipients = [*recipients, *cc, *bcc]
        return [email for _, email in all_recipients]

    def _build_email(self, message: Message) -> tuple[EmailMessage, List[str], str]:
        email_message = EmailMessage()
        email_message["Subject"] = message.subject or ""

        sender_header = self._resolve_sender(message)
        email_message["From"] = sender_header

        recipients = self._parse_addresses(getattr(message, "recipients", None))
        if not recipients:
            raise ValueError("No recipients specified for outgoing email")
        email_message["To"] = ", ".join(
            formataddr((name, email)) if name else email for name, email in recipients
        )

        cc = self._parse_addresses(getattr(message, "cc", None))
        if cc:
            email_message["Cc"] = ", ".join(
                formataddr((name, email)) if name else email for name, email in cc
            )

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
        timeout = config.get("MAIL_TIMEOUT", 10.0)
        fallback_to_tls = bool(config.get("MAIL_FALLBACK_TO_TLS", False))
        fallback_port_value = config.get("MAIL_FALLBACK_PORT", 587)
        fallback_server = config.get("MAIL_FALLBACK_SERVER") or host
        additional_servers = config.get("MAIL_ADDITIONAL_SERVERS") or []
        additional_ports_value = config.get("MAIL_ADDITIONAL_PORTS") or []
        force_ipv4 = bool(config.get("MAIL_FORCE_IPV4", False))
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError):
            timeout_value = 10.0
        else:
            if timeout_value <= 0:
                timeout_value = 10.0

        try:
            fallback_port = int(fallback_port_value)
        except (TypeError, ValueError):
            fallback_port = 587

        tls_context = ssl.create_default_context()
        fallback_use_ssl = bool(
            config.get("MAIL_FALLBACK_USE_SSL", False) or fallback_port == 465
        )

        def _normalized_hosts(value) -> list[str]:
            if not value:
                return []
            if isinstance(value, str):
                items = [item.strip() for item in value.split(",")]
            else:
                try:
                    iterator = iter(value)
                except TypeError:
                    items = [str(value).strip()]
                else:
                    items = []
                    for item in iterator:
                        if not item:
                            continue
                        if isinstance(item, str):
                            parts = [part.strip() for part in item.split(",")]
                            items.extend(part for part in parts if part)
                        else:
                            items.append(str(item).strip())
            return [item for item in items if item]

        def _normalized_ports(value) -> list[int]:
            if not value:
                return []
            if isinstance(value, str):
                raw_items = value.split(",")
            else:
                try:
                    iterator = iter(value)
                except TypeError:
                    raw_items = [value]
                else:
                    raw_items = list(iterator)
            result: list[int] = []
            for item in raw_items:
                try:
                    number = int(str(item).strip())
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    result.append(number)
            return result

        def _is_ipv6_resolution_error(exc: Exception) -> bool:
            if isinstance(exc, socket.gaierror):
                return True
            if isinstance(exc, OSError):
                retry_errnos = {
                    errno.EHOSTUNREACH,
                    errno.ENETUNREACH,
                    errno.ECONNREFUSED,
                    errno.ETIMEDOUT,
                }
                eai_again = getattr(socket, "EAI_AGAIN", None)
                if eai_again is not None:
                    retry_errnos.add(eai_again)
                return exc.errno in retry_errnos
            if isinstance(exc, (TimeoutError, smtplib.SMTPConnectError, ssl.SSLError)):
                return True
            return False

        def _connect_with_optional_ipv4(factory):
            original_getaddrinfo = socket.getaddrinfo

            def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
                if family in (0, socket.AF_UNSPEC, socket.AF_INET6):
                    family = socket.AF_INET
                result = original_getaddrinfo(host, port, family, type, proto, flags)
                if not isinstance(result, list):
                    return result
                ipv4_results = [item for item in result if item and item[0] == socket.AF_INET]
                return ipv4_results or result

            def connect_using_ipv4():
                socket.getaddrinfo = ipv4_only
                try:
                    return factory()
                finally:
                    socket.getaddrinfo = original_getaddrinfo

            if force_ipv4:
                return connect_using_ipv4()

            try:
                return factory()
            except Exception as exc:
                if not _is_ipv6_resolution_error(exc):
                    raise
                try:
                    return connect_using_ipv4()
                except Exception as ipv4_exc:
                    raise ipv4_exc from exc

        def _send_once(
            target_host: str,
            target_port: int,
            ssl_enabled: bool,
            tls_enabled: bool,
        ) -> None:
            if ssl_enabled:
                smtp_class = smtplib.SMTP_SSL
                smtp = _connect_with_optional_ipv4(
                    lambda: smtp_class(
                        target_host,
                        target_port,
                        timeout=timeout_value,
                        context=tls_context,
                    )
                )
            else:
                smtp_class = smtplib.SMTP
                smtp = _connect_with_optional_ipv4(
                    lambda: smtp_class(
                        target_host,
                        target_port,
                        timeout=timeout_value,
                    )
                )

            with smtp as server:
                server.ehlo()
                if tls_enabled and not ssl_enabled:
                    server.starttls(context=tls_context)
                    server.ehlo()
                if username:
                    server.login(username, password or "")
                server.send_message(email_message, from_addr=sender, to_addrs=recipients)

        def _should_retry(exc: Exception) -> bool:
            if isinstance(
                exc,
                (
                    smtplib.SMTPAuthenticationError,
                    smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPSenderRefused,
                ),
            ):
                return False

            recoverable = (
                OSError,
                TimeoutError,
                smtplib.SMTPConnectError,
                smtplib.SMTPServerDisconnected,
                ssl.SSLError,
                smtplib.SMTPNotSupportedError,
            )
            if isinstance(exc, recoverable):
                return True
            if isinstance(exc, smtplib.SMTPException):
                return True
            return False

        attempts: list[tuple[str, int, bool, bool]] = []
        seen: set[tuple[str, int, bool, bool]] = set()

        def _schedule(target_host: str, target_port: int, ssl_enabled: bool, tls_enabled: bool) -> None:
            if target_port <= 0:
                return
            key = (target_host, target_port, ssl_enabled, tls_enabled)
            if key in seen:
                return
            seen.add(key)
            attempts.append(key)

        host_variants: list[str] = []

        def _add_host_variant(value) -> None:
            for item in _normalized_hosts(value):
                lower = item.lower()
                if not item or item in host_variants:
                    continue
                host_variants.append(item)

        _add_host_variant(host)
        _add_host_variant(fallback_server)
        _add_host_variant(additional_servers)

        if any("gmail" in candidate.lower() or "googlemail" in candidate.lower() for candidate in host_variants):
            _add_host_variant(["smtp.gmail.com", "smtp.googlemail.com", "smtp-relay.gmail.com"])

        port_variants: list[tuple[int, bool, bool]] = []

        def _add_port_variant(port_value: int, ssl_enabled: bool, tls_enabled: bool) -> None:
            if port_value <= 0:
                return
            key = (port_value, ssl_enabled, tls_enabled)
            if key in port_variants:
                return
            port_variants.append(key)

        _add_port_variant(port, use_ssl, use_tls)

        if fallback_to_tls:
            _add_port_variant(fallback_port, fallback_use_ssl, not fallback_use_ssl)
            if not use_ssl:
                _add_port_variant(465, True, False)

        for extra_port in _normalized_ports(additional_ports_value):
            if extra_port == 465:
                _add_port_variant(extra_port, True, False)
            else:
                _add_port_variant(extra_port, False, True)

        if not host_variants:
            host_variants.append(host)
        if not port_variants:
            port_variants.append((port, use_ssl, use_tls))

        for target_host in host_variants:
            for port_value, ssl_enabled, tls_enabled in port_variants:
                _schedule(target_host, port_value, ssl_enabled, tls_enabled)

        last_exception: Exception | None = None
        for target_host, target_port, ssl_enabled, tls_enabled in attempts:
            try:
                _send_once(target_host, target_port, ssl_enabled, tls_enabled)
                return
            except Exception as exc:
                if not _should_retry(exc):
                    raise
                last_exception = exc
                continue

        if last_exception is not None:
            raise last_exception

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
