import importlib
import os
import sys
import unittest
from datetime import datetime, timedelta


class SalesVisitsApiTestCase(unittest.TestCase):
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

        self.sales = User(name="Sales One", email="sales@example.com", role=RoleEnum.sales)
        self.sales.set_password("Password!1")
        self.manager = User(name="Manager", email="manager@example.com", role=RoleEnum.outside_manager)
        self.manager.set_password("Password!1")
        self.admin = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        self.admin.set_password("Password!1")
        self.other_sales = User(name="Sales Two", email="sales2@example.com", role=RoleEnum.sales)
        self.other_sales.set_password("Password!1")
        self.app_module.db.session.add_all([self.sales, self.manager, self.admin, self.other_sales])
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.sales_token = self._login("sales@example.com")
        self.manager_token = self._login("manager@example.com")
        self.admin_token = self._login("admin@example.com")

        # map manager to sales
        self.client.post(
            "/api/sales-visits/team",
            json={"sales_user_id": self.sales.id},
            headers=self._auth(self.manager_token),
        )

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

    def _create_visit(self, token, **overrides):
        payload = {"prospect_name": "Prospect", "planned": False}
        payload.update(overrides)
        resp = self.client.post("/api/sales-visits", json=payload, headers=self._auth(token))
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        return resp.get_json()["data"]

    def test_sales_visit_happy_path(self):
        visit = self._create_visit(self.sales_token)
        start = datetime.now().isoformat()
        end = (datetime.now() + timedelta(minutes=10)).isoformat()

        resp = self.client.post(
            f"/api/sales-visits/{visit['id']}/check-in",
            json={"lat": 6.91, "lng": 79.86, "timestamp": start},
            headers=self._auth(self.sales_token),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        resp = self.client.post(
            f"/api/sales-visits/{visit['id']}/check-out",
            json={"lat": 6.91, "lng": 79.86, "timestamp": end},
            headers=self._auth(self.sales_token),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()["data"]
        self.assertEqual(data["approval_status"], "NOT_REQUIRED")
        self.assertGreaterEqual(data["duration_minutes"], 10)

    def test_gps_mismatch_triggers_pending(self):
        visit = self._create_visit(self.sales_token)
        # mark manual override to simulate exception
        resp = self.client.put(
            f"/api/sales-visits/{visit['id']}",
            json={"manual_location_override": True},
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        resp = self.client.post(
            f"/api/sales-visits/{visit['id']}/check-in",
            json={"lat": 0.0, "lng": 0.0},
            headers=self._auth(self.sales_token),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()["data"]
        self.assertEqual(data["approval_status"], "PENDING")

    def test_outside_manager_approves(self):
        visit = self._create_visit(self.sales_token)
        self.client.post(
            f"/api/sales-visits/{visit['id']}/check-in",
            json={"lat": 0.0, "lng": 0.0},
            headers=self._auth(self.sales_token),
        )
        self.client.post(
            f"/api/sales-visits/{visit['id']}/check-out",
            json={"lat": 0.0, "lng": 0.0, "timestamp": (datetime.now() + timedelta(minutes=1)).isoformat()},
            headers=self._auth(self.sales_token),
        )
        resp = self.client.post(
            f"/api/sales-visits/{visit['id']}/approve",
            json={"action": "APPROVE"},
            headers=self._auth(self.manager_token),
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json()["data"]["approval_status"], "APPROVED")

    def test_sales_cannot_approve(self):
        visit = self._create_visit(self.sales_token)
        self.client.post(
            f"/api/sales-visits/{visit['id']}/check-in",
            json={"lat": 0.0, "lng": 0.0},
            headers=self._auth(self.sales_token),
        )
        resp = self.client.post(
            f"/api/sales-visits/{visit['id']}/approve",
            json={"action": "APPROVE"},
            headers=self._auth(self.sales_token),
        )
        self.assertEqual(resp.status_code, 403)

    def test_manager_restricted_to_team(self):
        other_visit_resp = self.client.post(
            "/api/sales-visits",
            json={"prospect_name": "Other", "sales_user_id": self.other_sales.id},
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(other_visit_resp.status_code, 201)
        resp = self.client.get("/api/sales-visits", headers=self._auth(self.manager_token))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        # should not include other sales user
        self.assertTrue(all(v["sales_user_id"] == self.sales.id for v in data["data"]))

    def test_admin_can_view_all(self):
        self._create_visit(self.sales_token)
        self._create_visit(self.admin_token, prospect_name="Admin visit")
        resp = self.client.get("/api/sales-visits", headers=self._auth(self.admin_token))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["data"]), 2)


if __name__ == "__main__":
    unittest.main()
