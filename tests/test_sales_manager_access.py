import importlib
import os
import sys
import unittest


class SalesManagerAccessTestCase(unittest.TestCase):
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

        User = self.app_module.User
        RoleEnum = self.app_module.RoleEnum

        self.sales_manager = User(
            name="Sales Manager",
            email="sales_manager@example.com",
            role=RoleEnum.sales_manager,
            active=True,
        )
        self.sales_manager.set_password("Password!1")
        self.app_module.db.session.add(self.sales_manager)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("sales_manager@example.com")

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

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_login_returns_sales_manager_role(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"email": "sales_manager@example.com", "password": "Password!1"},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["user"]["role"], "sales_manager")

    def test_sales_manager_can_access_sales_customers(self):
        resp = self.client.get("/api/market/customers", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        payload = resp.get_json()
        self.assertIn("customers", payload)

    def test_sales_manager_blocked_from_non_sales_endpoint(self):
        resp = self.client.get("/api/financial-statements/trial-balance", headers=self._auth())
        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertFalse(data.get("ok", False))


if __name__ == "__main__":
    unittest.main()
