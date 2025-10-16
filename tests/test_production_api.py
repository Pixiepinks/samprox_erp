import importlib
import os
import sys
import unittest


class ProductionApiTestCase(unittest.TestCase):
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

        self.client = self.app.test_client()
        User = self.app_module.User
        RoleEnum = self.app_module.RoleEnum

        self.pm_user = User(
            name="Prod Manager",
            email="pm@example.com",
            role=RoleEnum.production_manager,
        )
        self.pm_user.set_password("Password!1")
        self.mm_user = User(
            name="Maint Manager",
            email="mm@example.com",
            role=RoleEnum.maintenance_manager,
        )
        self.mm_user.set_password("Password!1")
        self.app_module.db.session.add_all([self.pm_user, self.mm_user])
        self.app_module.db.session.commit()

        self.pm_token = self._login("pm@example.com")
        self.mm_token = self._login("mm@example.com")

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self, email):
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _create_machine(self):
        payload = {
            "name": "Milling Machine",
            "category": "Plant & Machines",
            "location": "Plant A",
            "status": "Operational",
        }
        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_create_update_and_list_daily_production(self):
        asset = self._create_machine()

        create_payload = {
            "machine_code": asset["code"],
            "date": "2024-05-10",
            "hour_no": 1,
            "quantity_tons": 5.25,
        }
        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            json=create_payload,
        )
        self.assertEqual(response.status_code, 201)
        created_entry = response.get_json()
        self.assertEqual(created_entry["machine_code"], asset["code"])
        self.assertEqual(created_entry["hour_no"], 1)
        self.assertAlmostEqual(created_entry["quantity_tons"], 5.25)

        update_payload = {
            **create_payload,
            "quantity_tons": 6.5,
        }
        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        updated_entry = response.get_json()
        self.assertAlmostEqual(updated_entry["quantity_tons"], 6.5)

        response = self.client.get(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "machine_code": asset["code"],
                "date": "2024-05-10",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["machine"]["code"], asset["code"])
        self.assertEqual(len(data["entries"]), 24)
        hour_one = next(entry for entry in data["entries"] if entry["hour_no"] == 1)
        self.assertAlmostEqual(hour_one["quantity_tons"], 6.5)
        total = data["total_quantity_tons"]
        self.assertAlmostEqual(total, 6.5)

    def test_only_production_manager_can_record_output(self):
        asset = self._create_machine()

        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.mm_token),
            json={
                "machine_code": asset["code"],
                "date": "2024-05-11",
                "hour_no": 2,
                "quantity_tons": 3,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission", response.get_json()["msg"].lower())


if __name__ == "__main__":
    unittest.main()
