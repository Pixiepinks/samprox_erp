import importlib
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch


class NonSamproxCustomersApiTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        if "app" in sys.modules:
            self.app_module = importlib.reload(sys.modules["app"])
        else:
            self.app_module = importlib.import_module("app")

        self.app = self.app_module.create_app()
        self.app.testing = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.app_module.db.create_all()

        RoleEnum = self.app_module.RoleEnum
        User = self.app_module.User
        Company = self.app_module.Company

        self.company = Company(key="samprox", name="Samprox International")
        self.sales = User(name="Sales One", email="sales@example.com", role=RoleEnum.sales)
        self.sales.set_password("Password!1")
        self.manager = User(name="Manager", email="manager@example.com", role=RoleEnum.outside_manager)
        self.manager.set_password("Password!1")
        self.admin = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        self.admin.set_password("Password!1")

        self.app_module.db.session.add_all([self.company, self.sales, self.manager, self.admin])
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.sales_token = self._login("sales@example.com")
        self.admin_token = self._login("admin@example.com")

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self, email):
        resp = self.client.post("/api/auth/login", json={"email": email, "password": "Password!1"})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["access_token"]

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _create_customer(self, token, **overrides):
        payload = {
            "customer_name": overrides.pop("customer_name", "Customer"),
            "company_id": self.company.id,
            "city": overrides.pop("city", "Colombo"),
        }
        payload.update(overrides)
        resp = self.client.post("/api/non-samprox-customers", json=payload, headers=self._auth(token))
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        return resp.get_json()["data"]

    def test_customer_code_sequence_resets_each_year(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            first = self._create_customer(self.sales_token, customer_name="Alpha")
            self.assertEqual(first["customer_code"], "260001")

            second = self._create_customer(self.sales_token, customer_name="Beta")
            self.assertEqual(second["customer_code"], "260002")

            mock_now.return_value = datetime(2027, 1, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            third = self._create_customer(self.sales_token, customer_name="Gamma")
            self.assertEqual(third["customer_code"], "270001")

    def test_customer_code_ignores_payload_input(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 2, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            created = self._create_customer(
                self.sales_token, customer_name="Delta", customer_code="OVERRIDE", city="Galle"
            )
            self.assertEqual(created["customer_code"], "260001")
            self.assertNotEqual(created["customer_code"], "OVERRIDE")

    def test_preview_next_code_reflects_upcoming_sequence(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            resp = self.client.get("/api/non-samprox-customers/next-code", headers=self._auth(self.sales_token))
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            payload = resp.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["next_code"], "260001")

            self._create_customer(self.sales_token, customer_name="Epsilon")
            resp = self.client.get("/api/non-samprox-customers/next-code", headers=self._auth(self.sales_token))
            next_payload = resp.get_json()
            self.assertEqual(next_payload["next_code"], "260002")

    def test_retry_on_unique_constraint_violation(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 4, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            existing = self._create_customer(self.sales_token, customer_name="Zeta")
            self.assertEqual(existing["customer_code"], "260001")

        with patch(
            "routes.non_samprox_customers.generate_non_samprox_customer_code", side_effect=["260001", "260002"]
        ):
            created = self._create_customer(self.sales_token, customer_name="Eta")
            self.assertEqual(created["customer_code"], "260002")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
