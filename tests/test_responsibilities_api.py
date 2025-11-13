import importlib
import os
import sys
import unittest
from datetime import date, timedelta


class ResponsibilityApiTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        if "app" in sys.modules:
            self.app_module = importlib.reload(sys.modules["app"])
        else:
            self.app_module = importlib.import_module("app")

        self.app = self.app_module.create_app()
        self.app.testing = True
        self.app.config["MAIL_SUPPRESS_SEND"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.app_module.db.create_all()

        User = self.app_module.User
        RoleEnum = self.app_module.RoleEnum

        self.primary_manager = User(
            name="Alice Manager",
            email="alice@example.com",
            role=RoleEnum.production_manager,
        )
        self.primary_manager.set_password("Password!1")
        self.secondary_manager = User(
            name="Bob Manager",
            email="bob@example.com",
            role=RoleEnum.maintenance_manager,
        )
        self.secondary_manager.set_password("Password!1")
        self.app_module.db.session.add_all([self.primary_manager, self.secondary_manager])
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("alice@example.com")
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.mail_extension = self.app.extensions["mail"]
        self.mail_extension.sent_messages.clear()

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self, email: str) -> str:
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def test_create_responsibility_records_task_and_sends_email(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Safety walkdown",
            "description": "Complete safety walk before shift begins.",
            "detail": "Verify emergency exits and PPE logs.",
            "scheduledFor": scheduled_for,
            "recurrence": "weekly",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "lead@example.com",
            "action": "delegated",
            "delegatedToId": self.secondary_manager.id,
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertEqual(data["title"], "Safety walkdown")
        self.assertEqual(data["recurrence"], "weekly")
        self.assertTrue(data["email_notification"]["sent"])

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertEqual(task.assignee_id, self.secondary_manager.id)
        self.assertEqual(task.recurrence.value, "weekly")
        self.assertEqual(task.recipient_email, "lead@example.com")
        self.assertEqual(task.detail, "Verify emergency exits and PPE logs.")
        self.assertEqual(task.action.value, "delegated")
        self.assertEqual(task.delegated_to_id, self.secondary_manager.id)
        self.assertIsNone(task.action_notes)
        self.assertEqual(task.number, "0001")

        self.assertEqual(len(self.mail_extension.sent_messages), 1)
        self.assertIn("Safety walkdown", self.mail_extension.sent_messages[0].subject)
        self.assertIn("Weekly", self.mail_extension.sent_messages[0].body)
        self.assertIn("5D Action: Delegated", self.mail_extension.sent_messages[0].body)
        self.assertIn("Responsibility No: 0001", self.mail_extension.sent_messages[0].body)

    def test_weekly_plan_email_summarizes_occurrences(self):
        monday = date.today()
        monday -= timedelta(days=monday.weekday())
        wednesday = monday + timedelta(days=2)

        payloads = [
            {
                "title": "Daily standup",
                "scheduledFor": monday.isoformat(),
                "recurrence": "daily",
                "recipientEmail": "daily@example.com",
                "action": "done",
            },
            {
                "title": "Quality audit",
                "scheduledFor": wednesday.isoformat(),
                "recurrence": "does_not_repeat",
                "recipientEmail": "audit@example.com",
                "action": "done",
            },
        ]

        for payload in payloads:
            response = self.client.post(
                "/api/responsibilities",
                headers=self.auth_headers,
                json=payload,
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.post(
            "/api/responsibilities/send-weekly",
            headers=self.auth_headers,
            json={
                "startDate": monday.isoformat(),
                "recipientEmail": "planner@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["startDate"], monday.isoformat())
        self.assertEqual(payload["occurrenceCount"], 8)

        self.assertEqual(len(self.mail_extension.sent_messages), 3)
        weekly_summary = self.mail_extension.sent_messages[-1].body
        self.assertIn("Daily standup", weekly_summary)
        self.assertIn("Quality audit", weekly_summary)

    def test_custom_recurrence_requires_weekdays(self):
        payload = {
            "title": "Maintenance sync",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "custom",
            "recipientEmail": "sync@example.com",
            "action": "done",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        data = response.get_json()
        self.assertIn("customWeekdays", data.get("errors", {}))
        self.assertEqual(self.app_module.ResponsibilityTask.query.count(), 0)

    def test_delegated_action_requires_manager(self):
        payload = {
            "title": "Vendor coordination",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "does_not_repeat",
            "recipientEmail": "coord@example.com",
            "action": "delegated",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("delegatedToId", errors)
        self.assertEqual(self.app_module.ResponsibilityTask.query.count(), 0)

    def test_discussed_action_requires_notes(self):
        payload = {
            "title": "Budget review",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "does_not_repeat",
            "recipientEmail": "budget@example.com",
            "action": "discussed",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("actionNotes", errors)
        self.assertEqual(self.app_module.ResponsibilityTask.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
