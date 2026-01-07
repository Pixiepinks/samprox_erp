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

    def test_sales_manager_can_access_sales_visits_resources(self):
        resp = self.client.get("/api/sales-visits", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        visits_payload = resp.get_json()
        self.assertTrue(visits_payload.get("ok"))

        resp_companies = self.client.get("/api/companies", headers=self._auth())
        self.assertEqual(resp_companies.status_code, 200, resp_companies.get_data(as_text=True))
        companies_payload = resp_companies.get_json()
        self.assertTrue(companies_payload.get("ok"))

        resp_customers = self.client.get("/api/non-samprox-customers", headers=self._auth())
        self.assertEqual(resp_customers.status_code, 200, resp_customers.get_data(as_text=True))
        customers_payload = resp_customers.get_json()
        self.assertTrue(customers_payload.get("ok"))

    def test_sales_manager_can_access_exsol_production(self):
        resp = self.client.get("/sales/production", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        api_resp = self.client.get("/api/exsol/production/entries", headers=self._auth())
        self.assertEqual(api_resp.status_code, 200, api_resp.get_data(as_text=True))

    def test_sales_manager_blocked_from_non_sales_endpoint(self):
        resp = self.client.get("/api/financial-statements/trial-balance", headers=self._auth())
        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertFalse(data.get("ok", False))


class SalesExecutiveAccessTestCase(unittest.TestCase):
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

        self.sales_executive = User(
            name="Sales Executive",
            email="sales_executive@example.com",
            role=RoleEnum.sales_executive,
            active=True,
        )
        self.sales_executive.set_password("Password!1")
        self.app_module.db.session.add(self.sales_executive)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("sales_executive@example.com")

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

    def test_login_returns_sales_executive_role(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"email": "sales_executive@example.com", "password": "Password!1"},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["user"]["role"], "sales_executive")

    def test_sales_executive_can_access_sales_customers(self):
        resp = self.client.get("/api/market/customers", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        payload = resp.get_json()
        self.assertIn("customers", payload)

    def test_sales_executive_can_access_sales_visits_resources(self):
        resp = self.client.get("/api/sales-visits", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        visits_payload = resp.get_json()
        self.assertTrue(visits_payload.get("ok"))

        resp_companies = self.client.get("/api/companies", headers=self._auth())
        self.assertEqual(resp_companies.status_code, 200, resp_companies.get_data(as_text=True))
        companies_payload = resp_companies.get_json()
        self.assertTrue(companies_payload.get("ok"))

        resp_customers = self.client.get("/api/non-samprox-customers", headers=self._auth())
        self.assertEqual(resp_customers.status_code, 200, resp_customers.get_data(as_text=True))
        customers_payload = resp_customers.get_json()
        self.assertTrue(customers_payload.get("ok"))

    def test_sales_executive_can_access_exsol_production(self):
        resp = self.client.get("/sales/production", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        api_resp = self.client.get("/api/exsol/production/entries", headers=self._auth())
        self.assertEqual(api_resp.status_code, 200, api_resp.get_data(as_text=True))

    def test_sales_executive_blocked_from_non_sales_endpoint(self):
        resp = self.client.get("/api/financial-statements/trial-balance", headers=self._auth())
        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertFalse(data.get("ok", False))


class SalesUserProductionAccessTestCase(unittest.TestCase):
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

        self.sales_user = User(
            name="Sales User",
            email="sales@example.com",
            role=RoleEnum.sales,
            active=True,
        )
        self.sales_user.set_password("Password!1")
        self.app_module.db.session.add(self.sales_user)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("sales@example.com")

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

    def test_sales_user_blocked_from_exsol_production(self):
        resp = self.client.get("/sales/production", headers=self._auth())
        self.assertEqual(resp.status_code, 403, resp.get_data(as_text=True))

        api_resp = self.client.get("/api/exsol/production/entries", headers=self._auth())
        self.assertEqual(api_resp.status_code, 403, api_resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
