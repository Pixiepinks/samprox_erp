import smtplib
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
