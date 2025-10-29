import importlib
import os
import sys
import unittest

from sqlalchemy import text


class TeamApiTestCase(unittest.TestCase):
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

        self.admin_user = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        self.admin_user.set_password("Password!1")
        self.production_user = User(
            name="Production Manager",
            email="pm@example.com",
            role=RoleEnum.production_manager,
        )
        self.production_user.set_password("Password!1")
        self.maintenance_user = User(
            name="Maintenance Manager",
            email="maint@example.com",
            role=RoleEnum.maintenance_manager,
        )
        self.maintenance_user.set_password("Password!1")
        self.app_module.db.session.add_all([
            self.admin_user,
            self.production_user,
            self.maintenance_user,
        ])
        self.app_module.db.session.commit()

        self.admin_token = self._login("admin@example.com")
        self.production_token = self._login("pm@example.com")
        self.maintenance_token = self._login("maint@example.com")

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

    def test_admin_can_register_and_list_team_members(self):
        payload = {
            "regNumber": "TM-001",
            "name": "Jane Doe",
            "joinDate": "2024-07-01",
            "status": "Active",
            "payCategory": "Factory",
            "personalDetail": "Jane's personal detail",
            "assignments": "Line A",
            "trainingRecords": "Forklift certified",
            "employmentLog": "Joined 2020",
            "files": "ID copy",
            "assets": "Safety kit",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        member = response.get_json()
        self.assertEqual(member["regNumber"], payload["regNumber"])
        self.assertEqual(member["status"], "Active")
        self.assertEqual(member["payCategory"], payload["payCategory"])
        self.assertEqual(member["personalDetail"], payload["personalDetail"])
        self.assertEqual(member["assignments"], payload["assignments"])
        self.assertEqual(member["trainingRecords"], payload["trainingRecords"])
        self.assertEqual(member["employmentLog"], payload["employmentLog"])
        self.assertEqual(member["files"], payload["files"])
        self.assertEqual(member["assets"], payload["assets"])

        list_response = self.client.get(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
        )
        self.assertEqual(list_response.status_code, 200)
        members = list_response.get_json()
        stored = next((m for m in members if m["regNumber"] == payload["regNumber"]), None)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["personalDetail"], payload["personalDetail"])
        self.assertEqual(stored["payCategory"], payload["payCategory"])

    def test_production_manager_can_register_member(self):
        payload = {
            "regNumber": "TM-020",
            "name": "Production Manager Member",
            "joinDate": "2024-07-08",
            "status": "Active",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.production_token),
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["regNumber"], payload["regNumber"])
        self.assertEqual(body["name"], payload["name"])
        self.assertEqual(body["payCategory"], "Office")

    def test_list_members_recovers_from_legacy_status_values(self):
        payload = {
            "regNumber": "TM-030",
            "name": "Legacy Status",
            "joinDate": "2024-07-10",
            "status": "Active",
        }

        create_response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )
        self.assertEqual(create_response.status_code, 201)
        member = create_response.get_json()

        # Simulate legacy data that stored the status in a non-standard format.
        self.app_module.db.session.execute(
            text("UPDATE team_member SET status = :status WHERE id = :id"),
            {"status": "on leave", "id": member["id"]},
        )
        self.app_module.db.session.commit()

        list_response = self.client.get(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
        )
        self.assertEqual(list_response.status_code, 200)
        items = list_response.get_json()
        stored = next((item for item in items if item["id"] == member["id"]), None)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "On Leave")

    def test_non_privileged_roles_cannot_register_member(self):
        payload = {
            "regNumber": "TM-002",
            "name": "John Smith",
            "joinDate": "2024-07-02",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.maintenance_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 403)

    def test_register_member_accepts_common_date_formats(self):
        examples = [
            ("TM-010", "02/01/2015", "2015-01-02"),
            ("TM-011", "02-01-2015", "2015-01-02"),
            ("TM-012", "2015/01/02", "2015-01-02"),
            ("TM-013", "2 Jan 2015", "2015-01-02"),
            ("TM-014", "2015-Jan-02", "2015-01-02"),
            ("TM-015", "10\\10\\2025", "2025-10-10"),
            ("TM-016", "10 / 18 / 2025", "2025-10-18"),
            ("TM-017", "18 Oct. 2025", "2025-10-18"),
            ("TM-018", "18.10.2025", "2025-10-18"),
            ("TM-019", "2025.10.18", "2025-10-18"),
            ("TM-021", "2025 10 18", "2025-10-18"),
            ("TM-022", "18 10 2025", "2025-10-18"),
            ("TM-023", "18th Oct 2025", "2025-10-18"),
            ("TM-024", "October 18th, 2025", "2025-10-18"),
            ("TM-025", "18/10/2025.", "2025-10-18"),
            ("TM-026", "18th of Oct 2025", "2025-10-18"),
            ("TM-027", "18 of October, 2025", "2025-10-18"),
            ("TM-028", "the 18th of October 2025", "2025-10-18"),
        ]

        for reg_number, provided, expected in examples:
            with self.subTest(provided=provided):
                response = self.client.post(
                    "/api/team/members",
                    headers=self._auth_headers(self.admin_token),
                    json={
                        "regNumber": reg_number,
                        "name": f"Example {reg_number}",
                        "joinDate": provided,
                    },
                )

                self.assertEqual(response.status_code, 201)
                body = response.get_json()
                self.assertEqual(body["joinDate"], expected)

    def test_register_member_rejects_overlong_fields(self):
        payload = {
            "regNumber": "T" * 41,
            "name": "Valid Name",
            "joinDate": "2024-07-04",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )

        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertIn("Registration number must be at most 40 characters.", body["msg"])

    def test_admin_can_update_member_details(self):
        payload = {
            "regNumber": "TM-003",
            "name": "Priya Silva",
            "joinDate": "2024-07-03",
            "status": "Active",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        member = response.get_json()
        member_id = member["id"]

        update_response = self.client.patch(
            f"/api/team/members/{member_id}",
            headers=self._auth_headers(self.admin_token),
            json={
                "status": "On Leave",
                "position": "Shift Lead",
                "assignments": "Updated assignment",
                "personalDetail": "Updated detail",
                "payCategory": "Casual",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.get_json()
        self.assertEqual(updated["status"], "On Leave")
        self.assertEqual(updated["position"], "Shift Lead")
        self.assertEqual(updated["assignments"], "Updated assignment")
        self.assertEqual(updated["personalDetail"], "Updated detail")
        self.assertEqual(updated["payCategory"], "Casual")

        # Fetch list to ensure persistence
        list_response = self.client.get(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
        )
        self.assertEqual(list_response.status_code, 200)
        members = list_response.get_json()
        stored = next((m for m in members if m["id"] == member_id), None)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "On Leave")
        self.assertEqual(stored["position"], "Shift Lead")
        self.assertEqual(stored["assignments"], "Updated assignment")
        self.assertEqual(stored["personalDetail"], "Updated detail")
        self.assertEqual(stored["payCategory"], "Casual")

    def test_update_member_validates_field_lengths(self):
        payload = {
            "regNumber": "TM-004",
            "name": "Kamal Perera",
            "joinDate": "2024-07-05",
            "status": "Active",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        member_id = response.get_json()["id"]

        update_response = self.client.patch(
            f"/api/team/members/{member_id}",
            headers=self._auth_headers(self.admin_token),
            json={"nickname": "N" * 130},
        )

        self.assertEqual(update_response.status_code, 400)
        body = update_response.get_json()
        self.assertIn("Nickname must be at most 120 characters.", body["msg"])

    def test_update_member_trims_status_values(self):
        payload = {
            "regNumber": "TM-005",
            "name": "Suresh Wijesinghe",
            "joinDate": "2024-07-06",
            "status": "Active",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        member_id = response.get_json()["id"]

        update_response = self.client.patch(
            f"/api/team/members/{member_id}",
            headers=self._auth_headers(self.admin_token),
            json={"status": " On Leave "},
        )

        self.assertEqual(update_response.status_code, 200)
        updated = update_response.get_json()
        self.assertEqual(updated["status"], "On Leave")


if __name__ == "__main__":
    unittest.main()
