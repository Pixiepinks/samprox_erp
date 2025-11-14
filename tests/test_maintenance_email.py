import importlib
import os
import sys
import unittest
from unittest.mock import patch

import requests
from requests import exceptions as requests_exceptions


class MaintenanceEmailTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ["RESEND_API_KEY"] = "test-key"
        if "app" in sys.modules:
            self.app_module = importlib.reload(sys.modules["app"])
        else:
            self.app_module = importlib.import_module("app")

        self.app = self.app_module.create_app()
        self.app.testing = True
        self.app.config["MAIL_SUPPRESS_SEND"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.app_module.db.create_all()

        User = self.app_module.User
        RoleEnum = self.app_module.RoleEnum
        self.user = User(name="Prod", email="prod@example.com", role=RoleEnum.production_manager)
        self.user.set_password("Password!1")
        self.app_module.db.session.add(self.user)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        response = self.client.post(
            "/api/auth/login",
            json={"email": "prod@example.com", "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        self.token = response.get_json()["access_token"]

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RESEND_API_KEY", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_timeout_error_returns_descriptive_message(self):
        with patch(
            "routes.maintenance_jobs._send_email_via_resend",
            side_effect=requests_exceptions.Timeout(),
        ):
            response = self.client.post(
                "/api/maintenance-jobs",
                headers=self._auth_headers(),
                json={"title": "Timeout test", "maint_email": "maint@example.com"},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        notification = payload["email_notification"]
        self.assertFalse(notification["sent"])
        self.assertIn("timed out", notification["message"].lower())

    def test_authentication_error_returns_descriptive_message(self):
        response_obj = requests.Response()
        response_obj.status_code = 403
        auth_error = requests_exceptions.HTTPError(response=response_obj)
        with patch(
            "routes.maintenance_jobs._send_email_via_resend",
            side_effect=auth_error,
        ):
            response = self.client.post(
                "/api/maintenance-jobs",
                headers=self._auth_headers(),
                json={"title": "Auth test", "maint_email": "maint@example.com"},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        notification = payload["email_notification"]
        self.assertFalse(notification["sent"])
        self.assertIn("authentication failed", notification["message"].lower())

    def test_ssl_error_returns_descriptive_message(self):
        ssl_error = requests_exceptions.SSLError("handshake failed")
        with patch(
            "routes.maintenance_jobs._send_email_via_resend",
            side_effect=ssl_error,
        ):
            response = self.client.post(
                "/api/maintenance-jobs",
                headers=self._auth_headers(),
                json={"title": "SSL test", "maint_email": "maint@example.com"},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        notification = payload["email_notification"]
        self.assertFalse(notification["sent"])
        self.assertIn("secure connection", notification["message"].lower())


if __name__ == "__main__":
    unittest.main()
