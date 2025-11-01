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

        smtp_ssl_mock.assert_called_once_with("smtp.example.com", 465, timeout=10.0)
        smtp_mock.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
        tls_server.starttls.assert_called_once()
        tls_server.send_message.assert_called_once()

    @patch("flask_mail.smtplib.SMTP")
    @patch("flask_mail.smtplib.SMTP_SSL")
    def test_fallback_uses_configured_port(self, smtp_ssl_mock, smtp_mock):
        self.app.config["MAIL_FALLBACK_PORT"] = 2525
        smtp_ssl_mock.side_effect = smtplib.SMTPConnectError(421, b"Connection failed")

        message = Message(subject="Hello", recipients=["recipient@example.com"], body="Test")
        self.mail.send(message)

        smtp_mock.assert_called_once_with("smtp.example.com", 2525, timeout=10.0)


if __name__ == "__main__":
    unittest.main()
