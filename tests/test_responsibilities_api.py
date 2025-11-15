import importlib
import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from requests import exceptions as requests_exceptions


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
        self.assistant_manager_one = User(
            name="Sudara", email="sudara@example.com", role=RoleEnum.production_manager
        )
        self.assistant_manager_one.set_password("Password!1")
        self.assistant_manager_two = User(
            name="Prasad", email="prasad@example.com", role=RoleEnum.production_manager
        )
        self.assistant_manager_two.set_password("Password!1")
        self.assistant_manager_three = User(
            name="Sumudu", email="sumudu@example.com", role=RoleEnum.production_manager
        )
        self.assistant_manager_three.set_password("Password!1")

        TeamMember = self.app_module.TeamMember
        TeamMemberStatus = self.app_module.TeamMemberStatus
        PayCategory = self.app_module.PayCategory
        today = date.today()
        self.team_member_primary = TeamMember(
            reg_number="TM001",
            name="Kasun Worker",
            join_date=today,
            status=TeamMemberStatus.ACTIVE,
            pay_category=PayCategory.FACTORY,
        )
        self.team_member_delegate_one = TeamMember(
            reg_number="TM002",
            name="Ishara Loader",
            join_date=today,
            status=TeamMemberStatus.ACTIVE,
            pay_category=PayCategory.LOADING,
        )
        self.team_member_delegate_two = TeamMember(
            reg_number="TM003",
            name="Ruwan Helper",
            join_date=today,
            status=TeamMemberStatus.ACTIVE,
            pay_category=PayCategory.LOADING,
        )
        self.app_module.db.session.add_all(
            [
                self.primary_manager,
                self.secondary_manager,
                self.assistant_manager_one,
                self.assistant_manager_two,
                self.assistant_manager_three,
                self.team_member_primary,
                self.team_member_delegate_one,
                self.team_member_delegate_two,
            ]
        )
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
            "performanceUnit": "amount_lkr",
            "performanceResponsible": "3000000",
            "performanceActual": "2500000",
            "delegations": [
                {"delegateId": self.assistant_manager_one.id, "allocatedValue": "1000000"},
                {"delegateId": self.assistant_manager_two.id, "allocatedValue": "1000000"},
                {"delegateId": self.assistant_manager_three.id, "allocatedValue": "1000000"},
            ],
        }

        with patch(
            "routes.responsibilities.random.choice",
            return_value=(
                "Great achievements begin with clear responsibilities. "
                "Let’s make this task a success!"
            ),
        ):
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
        self.assertEqual(len(data.get("delegations", [])), 3)

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertEqual(task.assignee_id, self.secondary_manager.id)
        self.assertEqual(task.recurrence.value, "weekly")
        self.assertEqual(task.recipient_email, "lead@example.com")
        self.assertEqual(task.detail, "Verify emergency exits and PPE logs.")
        self.assertEqual(task.action.value, "delegated")
        self.assertEqual(task.delegated_to_id, self.assistant_manager_one.id)
        self.assertEqual(len(task.delegations), 3)
        delegate_ids = {delegation.delegate_id for delegation in task.delegations}
        self.assertSetEqual(
            delegate_ids,
            {
                self.assistant_manager_one.id,
                self.assistant_manager_two.id,
                self.assistant_manager_three.id,
            },
        )
        allocated_values = sorted(str(delegation.allocated_value) for delegation in task.delegations)
        self.assertListEqual(allocated_values, ["1000000.0000", "1000000.0000", "1000000.0000"])
        self.assertIsNone(task.action_notes)
        self.assertEqual(task.number, "0001")
        self.assertEqual(task.progress, 0)
        self.assertEqual(task.perf_uom.value, "amount_lkr")
        self.assertEqual(float(task.perf_responsible_value), 3_000_000.0)
        self.assertEqual(float(task.perf_actual_value), 2_500_000.0)
        self.assertEqual(float(task.perf_metric_value), -500_000.0)

        self.assertEqual(len(self.sent_emails), 4)
        recipients = [message.get("to", [None])[0] for message in self.sent_emails]
        self.assertIn("lead@example.com", recipients)
        self.assertIn("sudara@example.com", recipients)
        self.assertIn("prasad@example.com", recipients)
        self.assertIn("sumudu@example.com", recipients)
        general_email = next(
            message
            for message in self.sent_emails
            if message.get("to", [None])[0] == "lead@example.com"
        )
        self.assertIn("Safety walkdown", general_email["subject"])
        self.assertIn(
            "Hi Bob,<br>A new responsibility has been assigned to you.",
            general_email["html"],
        )
        self.assertIn("Responsibility overview:", general_email["html"])
        self.assertIn("Delegated allocations:", general_email["html"])
        self.assertIn(
            "Great achievements begin with clear responsibilities. Let’s make this task a success!",
            general_email["html"],
        )
        self.assertIn("Maximus — Your AICEO", general_email["html"])
        delegate_email = next(
            message
            for message in self.sent_emails
            if message.get("to", [None])[0] == "sudara@example.com"
        )
        self.assertIn(
            "Hi Sudara,<br>A new responsibility has been delegated to you.",
            delegate_email["html"],
        )
        self.assertIn("Delegated allocation assigned to you", delegate_email["html"])
        self.assertIn("Maximus — Your AICEO", delegate_email["html"])

    def test_create_responsibility_accepts_team_member_assignee(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Packing supervision",
            "description": "Ensure shift coverage.",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "assigneeId": self.team_member_primary.id,
            "assigneeType": "team_member",
            "delegatedToId": self.team_member_delegate_one.id,
            "delegatedToType": "team_member",
            "recipientEmail": "supervisor@example.com",
            "action": "delegated",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "80",
            "delegations": [
                {
                    "delegateId": self.team_member_delegate_one.id,
                    "delegateType": "team_member",
                    "allocatedValue": "60",
                },
                {
                    "delegateId": self.team_member_delegate_two.id,
                    "delegateType": "team_member",
                    "allocatedValue": "40",
                },
            ],
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertEqual(data["assigneeId"], self.team_member_primary.id)
        self.assertEqual(data["assigneeName"], "Kasun Worker")
        self.assertEqual(data["delegatedToId"], self.team_member_delegate_one.id)
        self.assertEqual(data["delegatedToName"], "Ishara Loader")
        delegate_ids = {entry["delegateId"] for entry in data.get("delegations", [])}
        self.assertSetEqual(
            delegate_ids,
            {self.team_member_delegate_one.id, self.team_member_delegate_two.id},
        )

        task = self.app_module.ResponsibilityTask.query.one()
        self.assertIsNone(task.assignee_id)
        self.assertEqual(task.assignee_member_id, self.team_member_primary.id)
        self.assertIsNone(task.delegated_to_id)
        self.assertEqual(task.delegated_to_member_id, self.team_member_delegate_one.id)
        self.assertEqual(len(task.delegations), 2)
        self.assertSetEqual(
            {delegation.delegate_member_id for delegation in task.delegations},
            {self.team_member_delegate_one.id, self.team_member_delegate_two.id},
        )
        self.assertTrue(all(delegation.delegate_id is None for delegation in task.delegations))

        self.assertEqual(len(self.sent_emails), 1)
        self.assertEqual(self.sent_emails[0]["to"], ["supervisor@example.com"])

    def test_create_responsibility_without_actual_metric_is_allowed(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Team briefing",
            "description": "Coordinate daily maintenance goals.",
            "detail": "Highlight safety priorities and pending actions.",
            "scheduledFor": scheduled_for,
            "recurrence": "weekly",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "lead@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "75",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        data = response.get_json()
        self.assertEqual(data["performanceUnit"], "percentage_pct")
        self.assertEqual(data["performanceResponsible"], "75")
        self.assertIsNone(data.get("performanceActual"))
        self.assertIsNone(data.get("performanceMetric"))

        task = self.app_module.ResponsibilityTask.query.get(data["id"])
        self.assertEqual(task.perf_uom.value, "percentage_pct")
        self.assertEqual(float(task.perf_responsible_value), 75.0)
        self.assertIsNone(task.perf_actual_value)
        self.assertIsNone(task.perf_metric_value)

    def test_create_responsibility_requires_assignee(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Warehouse inspection",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "recipientEmail": "lead@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "90",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422, response.get_json())
        data = response.get_json()
        self.assertIn("errors", data)
        self.assertIn("assigneeId", data["errors"])

    def test_update_responsibility_updates_existing_task(self):
        scheduled_for = date.today().isoformat()
        create_payload = {
            "title": "Production sync",
            "description": "Review production metrics.",
            "detail": "Discuss line throughput and downtime logs.",
            "scheduledFor": scheduled_for,
            "recurrence": "custom",
            "customWeekdays": [0, 2],
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "owner@example.com",
            "ccEmail": "owner.cc@example.com",
            "action": "delegated",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "95",
            "delegations": [
                {"delegateId": self.assistant_manager_one.id, "allocatedValue": "50"},
            ],
        }

        create_response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=create_payload,
        )

        self.assertEqual(create_response.status_code, 201, create_response.get_json())
        created_data = create_response.get_json()
        task_id = created_data["id"]
        self.assertEqual(created_data.get("ccEmail"), "owner.cc@example.com")

        update_payload = {
            "title": "Production sync updated",
            "description": "Review metrics and staffing needs.",
            "detail": "Ensure corrective actions are documented.",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "customWeekdays": [],
            "recipientEmail": "team@example.com",
            "ccEmail": "opslead@example.com",
            "action": "done",
            "actionNotes": "Follow up next month.",
            "assigneeId": self.secondary_manager.id,
            "status": created_data.get("status", "planned"),
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "110",
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
        self.assertEqual(updated["performanceUnit"], "percentage_pct")
        self.assertEqual(updated["performanceResponsible"], "100")
        self.assertEqual(updated["performanceActual"], "110")
        self.assertEqual(updated.get("ccEmail"), "opslead@example.com")
        self.assertIsNotNone(updated["assignee"])
        self.assertIsNone(updated.get("delegatedTo"))
        self.assertEqual(updated.get("delegations"), [])
        self.assertEqual(updated["progress"], 100)
        notification = updated.get("email_notification")
        self.assertIsNotNone(notification)
        self.assertTrue(notification.get("sent"))

        task = self.app_module.ResponsibilityTask.query.get(task_id)
        self.assertEqual(task.title, "Production sync updated")
        self.assertEqual(task.detail, "Ensure corrective actions are documented.")
        self.assertEqual(task.recurrence.value, "does_not_repeat")
        self.assertIsNone(task.delegated_to_id)
        self.assertEqual(len(task.delegations), 0)
        self.assertIsNone(task.custom_weekdays)
        self.assertEqual(task.action.value, "done")
        self.assertEqual(task.action_notes, "Follow up next month.")
        self.assertEqual(task.assignee_id, self.secondary_manager.id)
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.cc_email, "opslead@example.com")

        # Updating should send a fresh email notification
        self.assertEqual(len(self.sent_emails), 3)
        for message in self.sent_emails:
            self.assertIn("prakash@rainbowsholdings.com", message.get("bcc", []))
        self.assertEqual(self.sent_emails[-1].get("cc"), ["opslead@example.com"])

    def test_update_responsibility_deleted_action_sets_progress_to_100(self):
        scheduled_for = date.today().isoformat()
        create_payload = {
            "title": "Waste audit",
            "description": "Review scrap handling.",
            "detail": "Check bins and documentation.",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "auditor@example.com",
            "action": "discussed",
            "progress": 25,
            "actionNotes": "Discussed cleanup approach.",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "25",
        }

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
            json={
                "title": "Waste audit",
                "description": "Review scrap handling.",
                "detail": "Check bins and documentation.",
                "scheduledFor": scheduled_for,
                "recurrence": "does_not_repeat",
                "assigneeId": self.secondary_manager.id,
                "recipientEmail": "auditor@example.com",
                "action": "deleted",
                "progress": 5,
                "actionNotes": "Task no longer required.",
                "performanceUnit": "percentage_pct",
                "performanceResponsible": "100",
                "performanceActual": "0",
            },
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
            {
                "title": "Daily standup",
                "scheduledFor": monday.isoformat(),
                "recurrence": "daily",
                "assigneeId": self.secondary_manager.id,
                "recipientEmail": "daily@example.com",
                "action": "done",
                "performanceUnit": "percentage_pct",
                "performanceResponsible": "100",
                "performanceActual": "100",
            },
            {
                "title": "Quality audit",
                "scheduledFor": wednesday.isoformat(),
                "recurrence": "does_not_repeat",
                "assigneeId": self.secondary_manager.id,
                "recipientEmail": "audit@example.com",
                "action": "done",
                "performanceUnit": "percentage_pct",
                "performanceResponsible": "100",
                "performanceActual": "90",
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
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["message"], "Weekly plan emailed successfully")
        self.assertEqual(payload["startDate"], monday.isoformat())
        self.assertEqual(payload["occurrenceCount"], 8)

        self.assertEqual(len(self.sent_emails), 3)
        weekly_summary = self.sent_emails[-1]["html"]
        self.assertIn("Daily standup", weekly_summary)
        self.assertIn("Quality audit", weekly_summary)

    def test_custom_recurrence_requires_weekdays(self):
        payload = {
            "title": "Maintenance sync",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "custom",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "sync@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "100",
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
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "coord@example.com",
            "action": "delegated",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "90",
        }

        response = self.client.post(
            "/api/responsibilities",
            headers=self.auth_headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        errors = response.get_json().get("errors", {})
        self.assertIn("delegations", errors)

    def test_create_responsibility_reports_email_failure(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Line inspection",
            "scheduledFor": scheduled_for,
            "recurrence": "weekly",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "inspect@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "90",
        }

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

        payload = {
            "title": "Daily standup",
            "scheduledFor": monday.isoformat(),
            "recurrence": "daily",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "daily@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "100",
        }

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
        payload = {
            "title": "Budget review",
            "scheduledFor": date.today().isoformat(),
            "recurrence": "does_not_repeat",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "budget@example.com",
            "action": "discussed",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "100",
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

    def test_create_responsibility_retries_when_number_conflict_occurs(self):
        scheduled_for = date.today().isoformat()
        payload = {
            "title": "Factory inspection",
            "scheduledFor": scheduled_for,
            "recurrence": "does_not_repeat",
            "assigneeId": self.secondary_manager.id,
            "recipientEmail": "inspect@example.com",
            "action": "done",
            "performanceUnit": "percentage_pct",
            "performanceResponsible": "100",
            "performanceActual": "100",
        }

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
