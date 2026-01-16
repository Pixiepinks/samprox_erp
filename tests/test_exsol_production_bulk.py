import importlib
import os
import sys
import time
import unittest
from datetime import date


class ExsolProductionBulkTestCase(unittest.TestCase):
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

        from models import Company, ExsolInventoryItem, RoleEnum, User

        self.company = Company(
            key="exsol-engineering",
            name="Exsol Engineering (Pvt) Ltd",
            company_code_prefix="E",
        )
        self.app_module.db.session.add(self.company)
        self.app_module.db.session.commit()

        self.item = ExsolInventoryItem(
            company_id=self.company.id,
            item_code="EX-ITEM-001",
            item_name="Exsol Item",
            is_active=True,
        )
        self.app_module.db.session.add(self.item)
        self.app_module.db.session.commit()

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

    def test_bulk_insert_serial_range(self):
        start_time = time.monotonic()
        payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 50,
                    "production_shift": "Morning",
                    "serial_mode": "SerialRange",
                    "start_serial": "00001000",
                }
            ]
        }
        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=payload)
        elapsed = time.monotonic() - start_time

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["inserted_production"], 1)
        self.assertEqual(data["inserted_serials"], 50)
        self.assertLess(elapsed, 5.0)

        from models import ExsolProductionSerial

        serial_count = ExsolProductionSerial.query.count()
        self.assertEqual(serial_count, 50)

    def test_bulk_insert_single_serial_range(self):
        payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "SerialRange",
                    "start_serial": "00000001",
                }
            ]
        }

        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=payload)

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["inserted_production"], 1)
        self.assertEqual(data["inserted_serials"], 1)

        from models import ExsolProductionSerial

        serials = ExsolProductionSerial.query.all()
        self.assertEqual(len(serials), 1)
        self.assertEqual(serials[0].serial_no, "00000001")

    def test_bulk_manual_serials_trim_and_require(self):
        payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "Manual",
                    "serial_numbers": " 24123007 ",
                }
            ]
        }

        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=payload)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["inserted_serials"], 1)

    def test_bulk_duplicate_serial_in_payload(self):
        payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "Manual",
                    "serial_numbers": "24123007",
                },
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "Manual",
                    "serial_numbers": "24123007",
                },
            ]
        }

        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=payload)
        self.assertEqual(resp.status_code, 409, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["duplicate_serial"], "24123007")
        self.assertEqual(data["conflict_source"], "payload")

    def test_bulk_duplicate_serial_existing(self):
        initial_payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "SerialRange",
                    "start_serial": "00001000",
                }
            ]
        }
        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=initial_payload)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        payload = {
            "rows": [
                {
                    "production_date": date.today().isoformat(),
                    "item_code": "EX-ITEM-001",
                    "quantity": 1,
                    "production_shift": "Morning",
                    "serial_mode": "Manual",
                    "serial_numbers": "00001000",
                }
            ]
        }
        resp = self.client.post("/api/exsol/production/bulk", headers=self._auth(), json=payload)
        self.assertEqual(resp.status_code, 409, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["duplicate_serial"], "00001000")
        self.assertEqual(data["conflict_source"], "production_serials")


if __name__ == "__main__":
    unittest.main()
