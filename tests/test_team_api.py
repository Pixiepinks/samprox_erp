import importlib
import os
import sys
import unittest


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
        self.viewer_user = User(
            name="Viewer",
            email="viewer@example.com",
            role=RoleEnum.production_manager,
        )
        self.viewer_user.set_password("Password!1")
        self.app_module.db.session.add_all([self.admin_user, self.viewer_user])
        self.app_module.db.session.commit()

        self.admin_token = self._login("admin@example.com")
        self.viewer_token = self._login("viewer@example.com")

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

        list_response = self.client.get(
            "/api/team/members",
            headers=self._auth_headers(self.admin_token),
        )
        self.assertEqual(list_response.status_code, 200)
        members = list_response.get_json()
        self.assertTrue(any(m["regNumber"] == payload["regNumber"] for m in members))

    def test_non_admin_cannot_register_member(self):
        payload = {
            "regNumber": "TM-002",
            "name": "John Smith",
            "joinDate": "2024-07-02",
        }

        response = self.client.post(
            "/api/team/members",
            headers=self._auth_headers(self.viewer_token),
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
            json={"status": "On Leave", "position": "Shift Lead"},
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.get_json()
        self.assertEqual(updated["status"], "On Leave")
        self.assertEqual(updated["position"], "Shift Lead")

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
