import importlib
import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from requests import exceptions as requests_exceptions

from responsibility_metrics import denormalize_value


class ResponsibilityApiTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ.setdefault("RESEND_API_KEY", "test")
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
        self.sent_emails: list[dict] = []
        self._next_status_code = 202
        self._next_exception: Exception | None = None
        patcher = patch(
            "routes.responsibilities.requests.post",
            side_effect=self._fake_post,
        )
        self._requests_post_patcher = patcher
        self.mock_requests_post = patcher.start()

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("RESEND_API_KEY", None)
        self._requests_post_patcher.stop()
        if "app" in sys.modules:
            del sys.modules["app"]

    class _FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests_exceptions.HTTPError(
                    f"{self.status_code} Error",
                    response=self,
                )

    def _fake_post(self, url, *, headers=None, json=None, timeout=None):
        if self._next_exception is not None:
            error = self._next_exception
            self._next_exception = None
            raise error

        self.sent_emails.append(json or {})
        response = self._FakeResponse(self._next_status_code)
        self._next_status_code = 202
        return response

    def _set_next_response_status(self, status_code: int) -> None:
        self._next_status_code = status_code

    def _raise_next_exception(self, exception: Exception) -> None:
        self._next_exception = exception

    def _login(self, email: str) -> str:
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _make_base_payload(self, **overrides):
        payload = {
            "title": "Default responsibility",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "does_not_repeat",
            "recipientEmail": "owner@example.com",
            "action": "done",
            "perfUom": "percentage_pct",
            "perfResponsibleValue": "100",
            "perfActualValue": "100",
        }
        payload.update(overrides)
        return payload

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
            "perfUom": "kg",
            "perfResponsibleValue": "10.000",
            "perfActualValue": "8.500",
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
        self.assertEqual(data["progress"], 0)
        self.assertTrue(data["email_notification"]["sent"])
        self.assertEqual(data["perfUom"], "kg")
        self.assertAlmostEqual(data["perfMetricValue"], 85.0)

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertEqual(task.assignee_id, self.secondary_manager.id)
        self.assertEqual(task.recurrence.value, "weekly")
        self.assertEqual(task.recipient_email, "lead@example.com")
        self.assertEqual(task.detail, "Verify emergency exits and PPE logs.")
        self.assertEqual(task.action.value, "delegated")
        self.assertEqual(task.delegated_to_id, self.secondary_manager.id)
        self.assertIsNone(task.action_notes)
        self.assertEqual(task.number, "0001")
        self.assertEqual(task.progress, 0)
        self.assertEqual(task.perf_uom.value, "kg")
        self.assertAlmostEqual(float(task.perf_metric_value), 85.0)
        self.assertAlmostEqual(float(task.perf_responsible_value), 10.0)
        self.assertAlmostEqual(float(task.perf_actual_value), 8.5)

        self.assertEqual(len(self.sent_emails), 1)
        message = self.sent_emails[0]
        self.assertIn("Safety walkdown", message["subject"])
        self.assertIn("Weekly", message["html"])
        self.assertIn("5D Action: Delegated", message["html"])
        self.assertIn("Responsibility No: 0001", message["html"])

    def test_create_responsibility_currency_metric(self):
        payload = self._make_base_payload(
            title="Revenue review",
            perfUom="amount_lkr",
            perfResponsibleValue="100000",
            perfActualValue="125000",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        data = response.get_json()
        self.assertEqual(data["perfUom"], "amount_lkr")
        self.assertAlmostEqual(data["perfMetricValue"], 125.0)

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertEqual(task.perf_uom.value, "amount_lkr")
        self.assertAlmostEqual(float(task.perf_metric_value), 125.0)

    def test_create_responsibility_percentage_metric(self):
        payload = self._make_base_payload(
            title="Project completion",
            perfUom="completion_pct",
            perfResponsibleValue="100",
            perfActualValue="60",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertAlmostEqual(data["perfMetricValue"], 60.0)

    def test_negative_actual_allowed_for_profit(self):
        payload = self._make_base_payload(
            title="Loss review",
            perfUom="profit",
            perfResponsibleValue="50",
            perfActualValue="-10",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        data = response.get_json()
        self.assertLess(data["perfMetricValue"], 0)

    def test_negative_actual_rejected_for_kg(self):
        payload = self._make_base_payload(
            title="Inventory audit",
            perfUom="kg",
            perfResponsibleValue="10",
            perfActualValue="-2",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("perfActualValue", errors)

    def test_update_responsibility_updates_existing_task(self):
        scheduled_for = date.today().isoformat()
        create_payload = {
            "title": "Production sync",
            "description": "Review production metrics.",
            "detail": "Discuss line throughput and downtime logs.",
            "scheduledFor": scheduled_for,
            "recurrence": "custom",
            "customWeekdays": [0, 2],
            "recipientEmail": "owner@example.com",
            "action": "delegated",
            "delegatedToId": self.secondary_manager.id,
            "perfUom": "hours",
            "perfResponsibleValue": "8",
            "perfActualValue": "7",
        }

        create_response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=create_payload,
        )

        self.assertEqual(create_response.status_code, 201, create_response.get_json())
        created_data = create_response.get_json()
        task_id = created_data["id"]

        update_payload = {
            "title": "Production sync updated",
            "description": "Review metrics and staffing needs.",
            "detail": "Ensure corrective actions are documented.",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "customWeekdays": [],
            "recipientEmail": "team@example.com",
            "action": "done",
            "actionNotes": "Follow up next month.",
            "assigneeId": self.secondary_manager.id,
            "status": created_data.get("status", "planned"),
            "perfUom": "hours",
            "perfResponsibleValue": "8",
            "perfActualValue": "9.5",
        }

        update_response = self.client.put(
            f"/api/responsibilities/{task_id}",
            headers=self.auth_headers,
            json=update_payload,
        )

        self.assertEqual(update_response.status_code, 200)
        updated = update_response.get_json()
        self.assertEqual(updated["title"], "Production sync updated")
        self.assertEqual(updated["recurrence"], "does_not_repeat")
        self.assertEqual(updated["recipientEmail"], "team@example.com")
        self.assertEqual(updated["actionNotes"], "Follow up next month.")
        self.assertIsNotNone(updated["assignee"])
        self.assertIsNone(updated.get("delegatedTo"))
        self.assertEqual(updated["progress"], 100)
        self.assertEqual(updated["perfUom"], "hours")
        self.assertAlmostEqual(updated["perfMetricValue"], 118.8)
        notification = updated.get("email_notification")
        self.assertIsNotNone(notification)
        self.assertTrue(notification.get("sent"))

        task = self.app_module.ResponsibilityTask.query.get(task_id)
        self.assertEqual(task.title, "Production sync updated")
        self.assertEqual(task.detail, "Ensure corrective actions are documented.")
        self.assertEqual(task.recurrence.value, "does_not_repeat")
        self.assertIsNone(task.delegated_to_id)
        self.assertIsNone(task.custom_weekdays)
        self.assertEqual(task.action.value, "done")
        self.assertEqual(task.action_notes, "Follow up next month.")
        self.assertEqual(task.assignee_id, self.secondary_manager.id)
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.perf_uom.value, "hours")
        self.assertAlmostEqual(
            denormalize_value(task.perf_uom, task.perf_actual_value), 9.5
        )
        self.assertAlmostEqual(
            denormalize_value(task.perf_uom, task.perf_responsible_value), 8.0
        )
        self.assertAlmostEqual(float(task.perf_metric_value), 118.8)

        # Updating should send a fresh email notification
        self.assertEqual(len(self.sent_emails), 2)

    def test_update_responsibility_deleted_action_sets_progress_to_100(self):
        scheduled_for = date.today().isoformat()
        create_payload = self._make_base_payload(
            title="Waste audit",
            description="Review scrap handling.",
            detail="Check bins and documentation.",
            scheduledFor=scheduled_for,
            recurrence="does_not_repeat",
            recipientEmail="auditor@example.com",
            action="discussed",
            progress=25,
            actionNotes="Discussed cleanup approach.",
        )

        create_response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=create_payload,
        )

        self.assertEqual(create_response.status_code, 201)
        task_id = create_response.get_json()["id"]

        update_response = self.client.put(
            f"/api/responsibilities/{task_id}",
            headers=self.auth_headers,
            json=self._make_base_payload(
                title="Waste audit",
                description="Review scrap handling.",
                detail="Check bins and documentation.",
                scheduledFor=scheduled_for,
                recurrence="does_not_repeat",
                recipientEmail="auditor@example.com",
                action="deleted",
                progress=5,
                actionNotes="Task no longer required.",
            ),
        )

        self.assertEqual(update_response.status_code, 200)
        payload = update_response.get_json()
        self.assertEqual(payload["progress"], 100)

        task = self.app_module.ResponsibilityTask.query.get(task_id)
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.action.value, "deleted")

    def test_weekly_plan_email_summarizes_occurrences(self):
        monday = date.today()
        monday -= timedelta(days=monday.weekday())
        wednesday = monday + timedelta(days=2)

        payloads = [
            self._make_base_payload(
                title="Daily standup",
                scheduledFor=monday.isoformat(),
                recurrence="daily",
                recipientEmail="daily@example.com",
                action="done",
            ),
            self._make_base_payload(
                title="Quality audit",
                scheduledFor=wednesday.isoformat(),
                recurrence="does_not_repeat",
                recipientEmail="audit@example.com",
                action="done",
            ),
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
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["message"], "Weekly plan emailed successfully")
        self.assertEqual(payload["startDate"], monday.isoformat())
        self.assertEqual(payload["occurrenceCount"], 8)

        self.assertEqual(len(self.sent_emails), 3)
        weekly_summary = self.sent_emails[-1]["html"]
        self.assertIn("Daily standup", weekly_summary)
        self.assertIn("Quality audit", weekly_summary)
        self.assertIn("Responsible:", weekly_summary)
        self.assertIn("Achievement:", weekly_summary)

    def test_custom_recurrence_requires_weekdays(self):
        payload = self._make_base_payload(
            title="Maintenance sync",
            scheduledFor=date.today().isoformat(),
            recurrence="custom",
            recipientEmail="sync@example.com",
            action="done",
        )

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
        payload = self._make_base_payload(
            title="Vendor coordination",
            scheduledFor=date.today().isoformat(),
            recurrence="does_not_repeat",
            recipientEmail="coord@example.com",
            action="delegated",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("delegatedToId", errors)

    def test_create_responsibility_reports_email_failure(self):
        scheduled_for = date.today().isoformat()
        payload = self._make_base_payload(
            title="Line inspection",
            scheduledFor=scheduled_for,
            recurrence="weekly",
            recipientEmail="inspect@example.com",
            action="done",
        )

        self._raise_next_exception(requests_exceptions.Timeout("timed out"))
        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertFalse(data["email_notification"]["sent"])
        self.assertIn("timed out", data["email_notification"]["message"].lower())

    def test_send_weekly_plan_returns_error_when_email_fails(self):
        monday = date.today() - timedelta(days=date.today().weekday())

        payload = self._make_base_payload(
            title="Daily standup",
            scheduledFor=monday.isoformat(),
            recurrence="daily",
            recipientEmail="daily@example.com",
            action="done",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201)

        self._set_next_response_status(401)
        response = self.client.post(
            "/api/responsibilities/send-weekly",
            headers=self.auth_headers,
            json={
                "startDate": monday.isoformat(),
                "recipientEmail": "planner@example.com",
            },
        )

        self.assertEqual(response.status_code, 500)
        message = response.get_json()["msg"].lower()
        self.assertIn("authentication failed", message)

    def test_discussed_action_requires_notes(self):
        payload = self._make_base_payload(
            title="Budget review",
            scheduledFor=date.today().isoformat(),
            recurrence="does_not_repeat",
            recipientEmail="budget@example.com",
            action="discussed",
        )

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("actionNotes", errors)
        self.assertEqual(self.app_module.ResponsibilityTask.query.count(), 0)

    def test_create_responsibility_retries_when_number_conflict_occurs(self):
        scheduled_for = date.today().isoformat()
        payload = self._make_base_payload(
            title="Factory inspection",
            scheduledFor=scheduled_for,
            recurrence="does_not_repeat",
            recipientEmail="inspect@example.com",
            action="done",
        )

        original_commit = self.app_module.db.session.commit
        conflict_error = IntegrityError(
            "INSERT INTO responsibility_task",
            {},
            Exception('duplicate key value violates unique constraint "responsibility_task_number_key"'),
        )
        call_state = {"count": 0}

        def commit_with_conflict():
            if call_state["count"] == 0:
                call_state["count"] += 1
                raise conflict_error
            call_state["count"] += 1
            return original_commit()

        with patch.object(
            self.app_module.db.session,
            "commit",
            side_effect=commit_with_conflict,
        ) as mock_commit, patch.object(
            self.app_module.db.session,
            "flush",
            wraps=self.app_module.db.session.flush,
        ) as mock_flush:
            response = self.client.post(
                "/api/responsibilities",
                headers=self.auth_headers,
                json=payload,
            )

        self.assertEqual(response.status_code, 201)
        self.assertGreaterEqual(mock_commit.call_count, 2)
        self.assertTrue(mock_flush.called)

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertEqual(task.title, "Factory inspection")
        self.assertEqual(task.number, "0001")


if __name__ == "__main__":
    unittest.main()
