import errno
import smtplib
import socket
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from flask_mail import Mail, Message


class MailDeliveryFallbackTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=False,
            MAIL_SUPPRESS_SEND=False,
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=465,
            MAIL_USE_SSL=True,
            MAIL_USE_TLS=False,
            MAIL_USERNAME="user@example.com",
            MAIL_PASSWORD="secret",
            MAIL_DEFAULT_SENDER="no-reply@example.com",
            MAIL_TIMEOUT=10.0,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_FALLBACK_PORT=587,
        )
        self.mail = Mail(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_fallback_to_tls_when_ssl_fails(self, smtp_ssl_mock, smtp_mock):
        smtp_ssl_mock.side_effect = OSError("SSL connection failed")
        tls_server = smtp_mock.return_value.__enter__.return_value

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")
        self.mail.send(message)

        smtp_ssl_mock.assert_called_once()
        ssl_args, ssl_kwargs = smtp_ssl_mock.call_args
        self.assertEqual(ssl_args, ("smtp.example.com", 465))
        self.assertEqual(ssl_kwargs.get("timeout"), 10.0)
        self.assertIn("context", ssl_kwargs)

        smtp_mock.assert_called_once()
        smtp_args, smtp_kwargs = smtp_mock.call_args
        self.assertEqual(smtp_args, ("smtp.example.com", 587))
        self.assertEqual(smtp_kwargs.get("timeout"), 10.0)

        tls_server.starttls.assert_called_once()
        starttls_kwargs = tls_server.starttls.call_args.kwargs
        self.assertIn("context", starttls_kwargs)
        tls_server.send_message.assert_called_once()

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_fallback_uses_configured_port(self, smtp_ssl_mock, smtp_mock):
        self.app.config["MAIL_FALLBACK_PORT"] = 2525
        smtp_ssl_mock.side_effect = smtplib.SMTPConnectError(421, b"Connection failed")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")
        self.mail.send(message)

        smtp_mock.assert_called_once()
        args, kwargs = smtp_mock.call_args
        self.assertEqual(args, ("smtp.example.com", 2525))
        self.assertEqual(kwargs.get("timeout"), 10.0)

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_multiple_recipients_from_single_string(self, smtp_ssl_mock, smtp_mock):
        self.app.config.update(MAIL_USE_TLS=False, MAIL_USE_SSL=False)

        message = Message(
            subject="Hello",
            recipients=["alpha@example.com, beta@example.com"],
            body="Test",
        )
        message.cc = "gamma@example.com"

        self.mail.send(message)

        smtp_ssl_mock.assert_not_called()
        smtp_mock.assert_called_once()
        smtp_instance = smtp_mock.return_value.__enter__.return_value
        send_message = smtp_instance.send_message
        send_message.assert_called_once()
        kwargs = send_message.call_args.kwargs
        self.assertEqual(
            kwargs.get("to_addrs"),
            ["alpha@example.com", "beta@example.com", "gamma@example.com"],
        )

        email_message = send_message.call_args.args[0]
        self.assertEqual(email_message["To"], "alpha@example.com, beta@example.com")
        self.assertEqual(email_message["Cc"], "gamma@example.com")

    @patch("flask_mail.smtplib.SMTP_SSL")
    @patch("flask_mail.smtplib.SMTP")
    def test_tls_connection_failure_uses_ssl_fallback(self, smtp_mock, smtp_ssl_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_FALLBACK_PORT=587,
        )

        smtp_mock.side_effect = OSError("Connection refused")
        smtp_ssl_mock.return_value.__enter__.return_value.send_message.return_value = None

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        smtp_mock.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
        smtp_ssl_mock.assert_called_once()
        args, kwargs = smtp_ssl_mock.call_args
        self.assertEqual(args, ("smtp.example.com", 465))
        self.assertEqual(kwargs.get("timeout"), 10.0)
        self.assertIsNotNone(kwargs.get("context"))

    @patch("flask_mail.smtplib.SMTP_SSL")
    @patch("flask_mail.smtplib.SMTP")
    def test_additional_server_is_attempted(self, smtp_mock, smtp_ssl_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.primary.example",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_ADDITIONAL_SERVERS=["smtp.backup.example"],
        )

        def _smtp_side_effect(host, port, timeout=10.0, **kwargs):
            if host == "smtp.primary.example":
                raise OSError("primary unreachable")
            server = MagicMock()
            connection = MagicMock()
            connection.ehlo.return_value = None
            connection.starttls.return_value = None
            connection.login.return_value = None
            connection.send_message.return_value = None
            server.__enter__.return_value = connection
            server.__exit__.return_value = False
            return server

        def _smtp_ssl_side_effect(host, port, timeout=10.0, context=None, **kwargs):
            raise OSError("ssl unreachable")

        smtp_mock.side_effect = _smtp_side_effect
        smtp_ssl_mock.side_effect = _smtp_ssl_side_effect

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        hosts = [call.args[0] for call in smtp_mock.call_args_list]
        self.assertIn("smtp.backup.example", hosts)

    @patch("flask_mail.smtplib.SMTP_SSL")
    @patch("flask_mail.smtplib.SMTP")
    def test_additional_port_is_attempted(self, smtp_mock, smtp_ssl_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_ADDITIONAL_PORTS=[2525],
        )

        def _smtp_side_effect(host, port, timeout=10.0, **kwargs):
            if port == 587:
                raise OSError("tls port unreachable")
            server = MagicMock()
            connection = MagicMock()
            connection.ehlo.return_value = None
            connection.starttls.return_value = None
            connection.login.return_value = None
            connection.send_message.return_value = None
            server.__enter__.return_value = connection
            server.__exit__.return_value = False
            return server

        smtp_mock.side_effect = _smtp_side_effect
        smtp_ssl_mock.side_effect = OSError("ssl unreachable")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        attempted_ports = [call.args[1] for call in smtp_mock.call_args_list]
        self.assertIn(2525, attempted_ports)

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_network_unreachable_does_not_retry_all_variants(
        self, smtp_ssl_mock, smtp_mock
    ):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_ADDITIONAL_SERVERS=["smtp.backup.example"],
        )

        smtp_mock.side_effect = OSError(errno.ENETUNREACH, "Network unreachable")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        with self.assertRaises(OSError):
            self.mail.send(message)

        # One attempt is made for the primary connection and a second attempt is
        # triggered when the transport retries with the IPv4-only resolver.
        self.assertEqual(smtp_mock.call_count, 2)
        smtp_ssl_mock.assert_not_called()

    @patch("flask_mail.time.monotonic")
    @patch("flask_mail.smtplib.SMTP_SSL")
    @patch("flask_mail.smtplib.SMTP")
    def test_total_delivery_timeout_limits_attempts(
        self, smtp_mock, smtp_ssl_mock, monotonic_mock
    ):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=True,
            MAIL_MAX_DELIVERY_SECONDS=5,
        )

        monotonic_mock.side_effect = [0.0, 1.0, 6.0]
        smtp_mock.side_effect = TimeoutError("Connection attempt timed out")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        with self.assertRaises(TimeoutError):
            self.mail.send(message)

        self.assertEqual(smtp_mock.call_count, 1)
        smtp_ssl_mock.assert_not_called()

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_timeout_retries_remaining_hosts(self, smtp_ssl_mock, smtp_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.primary.example",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=False,
            MAIL_ADDITIONAL_SERVERS=["smtp.backup.example"],
        )

        def _smtp_side_effect(host, port, timeout=10.0, **kwargs):
            if host == "smtp.primary.example":
                raise TimeoutError("Primary host timed out")
            server = MagicMock()
            connection = MagicMock()
            connection.ehlo.return_value = None
            connection.starttls.return_value = None
            connection.login.return_value = None
            connection.send_message.return_value = None
            server.__enter__.return_value = connection
            server.__exit__.return_value = False
            return server

        smtp_mock.side_effect = _smtp_side_effect
        smtp_ssl_mock.side_effect = AssertionError("SSL fallback should not be used")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        attempted_hosts = [call.args[0] for call in smtp_mock.call_args_list]
        self.assertEqual(
            attempted_hosts,
            ["smtp.primary.example", "smtp.backup.example"],
        )

        smtp_ssl_mock.assert_not_called()

    @patch("flask_mail.socket.getaddrinfo")
    @patch("flask_mail.smtplib.SMTP")
    def test_ipv6_resolution_error_retries_with_ipv4(self, smtp_mock, getaddrinfo_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=False,
        )

        server = MagicMock()
        connection = MagicMock()
        connection.ehlo.return_value = None
        connection.starttls.return_value = None
        connection.login.return_value = None
        connection.send_message.return_value = None
        server.__enter__.return_value = connection
        server.__exit__.return_value = False

        attempts = {"count": 0}

        def _smtp_side_effect(host, port, timeout=10.0, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise socket.gaierror(getattr(socket, "EAI_AGAIN", -1), "temporary failure")
            socket.getaddrinfo(host, port)
            return server

        smtp_mock.side_effect = _smtp_side_effect

        getaddrinfo_mock.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 587, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 587)),
        ]

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        self.assertGreaterEqual(attempts["count"], 2)
        self.assertGreaterEqual(getaddrinfo_mock.call_count, 1)
        self.assertIs(socket.getaddrinfo, getaddrinfo_mock)

    @patch("flask_mail.socket.getaddrinfo")
    @patch("flask_mail.smtplib.SMTP")
    def test_force_ipv4_restricts_address_resolution(self, smtp_mock, getaddrinfo_mock):
        self.app.config.update(
            MAIL_SERVER="smtp.example.com",
            MAIL_PORT=587,
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_FALLBACK_TO_TLS=False,
            MAIL_FORCE_IPV4=True,
        )

        getaddrinfo_mock.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 587, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 587)),
        ]

        def _smtp_side_effect(host, port, timeout=10.0, **kwargs):
            # Trigger address resolution to exercise the IPv4 filter.
            socket.getaddrinfo(host, port)
            server = MagicMock()
            connection = MagicMock()
            connection.ehlo.return_value = None
            connection.starttls.return_value = None
            connection.login.return_value = None
            connection.send_message.return_value = None
            server.__enter__.return_value = connection
            server.__exit__.return_value = False
            return server

        smtp_mock.side_effect = _smtp_side_effect

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")

        self.mail.send(message)

        assert getaddrinfo_mock.called
        for recorded_call in getaddrinfo_mock.call_args_list:
            args = recorded_call.args
            assert len(args) >= 3
            assert args[2] == socket.AF_INET


if __name__ == "__main__":
    unittest.main()
