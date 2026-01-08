import importlib
import os
import sys
import unittest


class ExsolInventoryCreateTestCase(unittest.TestCase):
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

        from models import Company, RoleEnum, User

        self.company = Company(
            key="exsol-engineering",
            name="Exsol Engineering (Pvt) Ltd",
            company_code_prefix="E",
        )
        self.app_module.db.session.add(self.company)
        self.app_module.db.session.commit()

        self.admin = User(
            name="Admin User",
            email="admin@example.com",
            role=RoleEnum.admin,
            active=True,
        )
        self.admin.set_password("Password!1")
        self.app_module.db.session.add(self.admin)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("admin@example.com")

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

    def test_create_inventory_item_returns_json(self):
        payload = {
            "item_code": "TEST-001",
            "item_name": "Test Item",
            "uom": "PCS",
            "is_active": True,
        }

        resp = self.client.post("/api/exsol/inventory-items", headers=self._auth(), json=payload)
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))

        data = resp.get_json()
        self.assertEqual(data["item_code"], payload["item_code"])
        self.assertEqual(data["item_name"], payload["item_name"])
        self.assertEqual(data["uom"], payload["uom"])
        self.assertTrue(data["is_active"])
        self.assertIsNotNone(data.get("created_at"))

        from models import ExsolInventoryItem

        self.assertEqual(ExsolInventoryItem.query.filter_by(item_code="TEST-001").count(), 1)


if __name__ == "__main__":
    unittest.main()
