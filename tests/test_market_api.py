import importlib
import os
import sys
import unittest


class MarketApiTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"

        if "app" in sys.modules:
            self.app_module = importlib.reload(sys.modules["app"])
        else:
            self.app_module = importlib.import_module("app")

        self.app = self.app_module.create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.app_module.db.create_all()

        User = self.app_module.User
        RoleEnum = self.app_module.RoleEnum

        admin = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        admin.set_password("Password!1")
        self.app_module.db.session.add(admin)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login()

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _create_customer_payload(self, **overrides):
        payload = dict(
            name="ACME Holdings",
            category="Industrial",
            credit_term="30 Days",
            transport_mode="Customer lorry",
            customer_type="Regular",
            sales_coordinator_name="Alex",
            sales_coordinator_phone="0710000000",
            store_keeper_name="Sam",
            store_keeper_phone="0711111111",
            payment_coordinator_name="Chris",
            payment_coordinator_phone="0712222222",
            special_note="Key account",
        )
        payload.update(overrides)
        return payload

    def test_create_customer_accepts_human_friendly_enum_labels(self):
        response = self.client.post(
            "/api/market/customers",
            json=self._create_customer_payload(),
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["customer"]["category"], "industrial")
        self.assertEqual(data["customer"]["credit_term"], "30_days")
        self.assertEqual(data["customer"]["transport_mode"], "customer_lorry")
        self.assertEqual(data["customer"]["customer_type"], "regular")

    def test_create_customer_allows_empty_special_note(self):
        payload = self._create_customer_payload(special_note="   ")
        response = self.client.post(
            "/api/market/customers",
            json=payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["customer"]["special_note"], "")


if __name__ == "__main__":
    unittest.main()
