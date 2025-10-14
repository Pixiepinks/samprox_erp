import importlib
import os
import sys
import unittest


class MachineApiTestCase(unittest.TestCase):
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

        self.pm_user = User(name="Prod Manager", email="pm@example.com", role=RoleEnum.production_manager)
        self.pm_user.set_password("Password!1")
        self.mm_user = User(name="Maint Manager", email="mm@example.com", role=RoleEnum.maintenance_manager)
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

    def test_asset_part_idle_and_supplier_flow(self):
        # Create an asset
        asset_payload = {
            "name": "CNC Machine",
            "category": "Plant & Machines",
            "location": "Plant A",
            "manufacturer": "ACME",
            "installed_on": "2024-01-10",
            "status": "Operational",
        }
        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json=asset_payload,
        )
        self.assertEqual(response.status_code, 201)
        asset = response.get_json()
        asset_id = asset["id"]
        self.assertEqual(asset["code"], "MCH-0001")

        # List assets and ensure part count is zero initially
        response = self.client.get(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
        )
        self.assertEqual(response.status_code, 200)
        assets = response.get_json()
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["part_count"], 0)

        # Add a part
        part_payload = {
            "name": "Hydraulic pump",
            "part_number": "P-100",
            "expected_life_hours": 1200,
            "description": "Primary pressure pump",
        }
        response = self.client.post(
            f"/api/machines/assets/{asset_id}/parts",
            headers=self._auth_headers(self.pm_token),
            json=part_payload,
        )
        self.assertEqual(response.status_code, 201)
        part_id = response.get_json()["id"]

        # Log a replacement via maintenance manager
        replacement_payload = {
            "replaced_on": "2024-02-01",
            "replaced_by": "Technician T",
            "reason": "Preventive maintenance",
            "notes": "Replaced due to scheduled service",
        }
        response = self.client.post(
            f"/api/machines/parts/{part_id}/replacements",
            headers=self._auth_headers(self.mm_token),
            json=replacement_payload,
        )
        self.assertEqual(response.status_code, 201)

        # Parts list should include history entry
        response = self.client.get(
            f"/api/machines/assets/{asset_id}/parts",
            headers=self._auth_headers(self.pm_token),
        )
        self.assertEqual(response.status_code, 200)
        parts = response.get_json()
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["replacement_history"][0]["reason"], "Preventive maintenance")

        # Record idle event
        idle_payload = {
            "asset_id": asset_id,
            "started_at": "2024-02-05T08:00",
            "ended_at": "2024-02-05T09:30",
            "reason": "Calibration",
        }
        response = self.client.post(
            "/api/machines/idle-events",
            headers=self._auth_headers(self.mm_token),
            json=idle_payload,
        )
        self.assertEqual(response.status_code, 201)

        # Idle events listing should include duration and asset details
        response = self.client.get(
            "/api/machines/idle-events",
            headers=self._auth_headers(self.pm_token),
        )
        self.assertEqual(response.status_code, 200)
        events = response.get_json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["asset"]["id"], asset_id)
        self.assertEqual(events[0]["duration_minutes"], 90)

        # Create service supplier
        supplier_payload = {
            "name": "Rapid Repairs",
            "contact_person": "Mr. Silva",
            "phone": "0771234567",
            "services_offered": "Breakdown repair",
        }
        response = self.client.post(
            "/api/machines/service-suppliers",
            headers=self._auth_headers(self.pm_token),
            json=supplier_payload,
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.get(
            "/api/machines/service-suppliers",
            headers=self._auth_headers(self.pm_token),
        )
        self.assertEqual(response.status_code, 200)
        suppliers = response.get_json()
        self.assertEqual(len(suppliers), 1)
        self.assertEqual(suppliers[0]["name"], "Rapid Repairs")

    def test_maintenance_manager_cannot_create_assets(self):
        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.mm_token),
            json={"name": "Lathe", "category": "Plant & Machines"},
        )
        self.assertEqual(response.status_code, 403)

    def test_preview_asset_code_endpoint(self):
        response = self.client.get(
            "/api/machines/assets/code",
            headers=self._auth_headers(self.pm_token),
            query_string={"category": "Plant & Machines"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["code"], "MCH-0001")

        create_response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json={"name": "Laser Cutter", "category": "Plant & Machines"},
        )
        self.assertEqual(create_response.status_code, 201)

        response = self.client.get(
            "/api/machines/assets/code",
            headers=self._auth_headers(self.pm_token),
            query_string={"category": "Plant & Machines"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["code"], "MCH-0002")

        response = self.client.get(
            "/api/machines/assets/code",
            headers=self._auth_headers(self.pm_token),
            query_string={"category": "Unknown"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.get(
            "/api/machines/assets/code",
            headers=self._auth_headers(self.mm_token),
            query_string={"category": "Plant & Machines"},
        )
        self.assertEqual(response.status_code, 403)

    def test_codes_increment_per_category(self):
        payload = {
            "name": "Forklift",
            "category": "Vehicles",
        }
        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["code"], "VEH0001")

        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json={"name": "Electric Car", "category": "Vehicles"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["code"], "VEH0002")


if __name__ == "__main__":
    unittest.main()
