import csv
import importlib
import io
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import models as models_module


class NonSamproxCustomersApiTestCase(unittest.TestCase):
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
        Company = self.app_module.Company

        self.company = Company(key="samprox", name="Samprox International", company_code_prefix="")
        self.exsol = Company(key="exsol-engineering", name="Exsol Engineering (Pvt) Ltd", company_code_prefix="E")
        self.trading = Company(key="rainbows-end-trading", name="Rainbow Trading (Pvt) Ltd", company_code_prefix="T")
        self.sales = User(name="Sales One", email="sales@example.com", role=RoleEnum.sales)
        self.sales.set_password("Password!1")
        self.manager = User(name="Manager", email="manager@example.com", role=RoleEnum.outside_manager)
        self.manager.set_password("Password!1")
        self.admin = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        self.admin.set_password("Password!1")

        self.app_module.db.session.add_all(
            [self.company, self.exsol, self.trading, self.sales, self.manager, self.admin]
        )
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.sales_token = self._login("sales@example.com")
        self.admin_token = self._login("admin@example.com")
        self.NonSamproxCustomer = models_module.NonSamproxCustomer

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

    def _create_customer(self, token, **overrides):
        payload = {
            "customer_name": overrides.pop("customer_name", "Customer"),
            "company_id": overrides.pop("company_id", self.company.id),
            "city": overrides.pop("city", "Colombo"),
        }
        payload.update(overrides)
        resp = self.client.post("/api/non-samprox-customers", json=payload, headers=self._auth(token))
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        return resp.get_json()["data"]

    def test_customer_code_sequence_resets_each_year(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            first = self._create_customer(self.sales_token, customer_name="Alpha")
            self.assertEqual(first["customer_code"], "260001")

            second = self._create_customer(self.sales_token, customer_name="Beta")
            self.assertEqual(second["customer_code"], "260002")

            mock_now.return_value = datetime(2027, 1, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            third = self._create_customer(self.sales_token, customer_name="Gamma")
            self.assertEqual(third["customer_code"], "270001")

    def test_customer_code_ignores_payload_input(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 2, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            created = self._create_customer(
                self.sales_token, customer_name="Delta", customer_code="OVERRIDE", city="Galle"
            )
            self.assertEqual(created["customer_code"], "260001")
            self.assertNotEqual(created["customer_code"], "OVERRIDE")

    def test_preview_next_code_reflects_upcoming_sequence(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            resp = self.client.get(
                f"/api/non-samprox-customers/next-code?company_id={self.company.id}",
                headers=self._auth(self.sales_token),
            )
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            payload = resp.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["next_code"], "260001")

            self._create_customer(self.sales_token, customer_name="Epsilon")
            resp = self.client.get(
                f"/api/non-samprox-customers/next-code?company_id={self.company.id}",
                headers=self._auth(self.sales_token),
            )
            next_payload = resp.get_json()
            self.assertEqual(next_payload["next_code"], "260002")

    def test_retry_on_unique_constraint_violation(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 4, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            existing = self._create_customer(self.sales_token, customer_name="Zeta")
            self.assertEqual(existing["customer_code"], "260001")

        with patch(
            "routes.non_samprox_customers.generate_non_samprox_customer_code", side_effect=["260001", "260002"]
        ):
            created = self._create_customer(self.sales_token, customer_name="Eta")
            self.assertEqual(created["customer_code"], "260002")

    def test_next_code_requires_company_id(self):
        resp = self.client.get("/api/non-samprox-customers/next-code", headers=self._auth(self.sales_token))
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))

    def test_prefixed_next_code_and_creation_use_company_sequence(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 5, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            resp = self.client.get(
                f"/api/non-samprox-customers/next-code?company_id={self.exsol.id}",
                headers=self._auth(self.sales_token),
            )
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            preview = resp.get_json()
            self.assertEqual(preview["next_code"], "E260001")

            created = self._create_customer(
                self.sales_token,
                customer_name="Exsol One",
                company_id=self.exsol.id,
                customer_code=preview["next_code"],
            )
            self.assertEqual(created["customer_code"], "E260001")

            second = self._create_customer(self.sales_token, customer_name="Exsol Two", company_id=self.exsol.id)
            self.assertEqual(second["customer_code"], "E260002")

    def test_sequences_are_per_company(self):
        with patch("routes.non_samprox_customers._now_colombo") as mock_now:
            mock_now.return_value = datetime(2026, 6, 1, tzinfo=ZoneInfo("Asia/Colombo"))
            exsol = self._create_customer(self.sales_token, company_id=self.exsol.id, customer_name="A")
            trading = self._create_customer(self.sales_token, company_id=self.trading.id, customer_name="B")
            self.assertEqual(exsol["customer_code"], "E260001")
            self.assertEqual(trading["customer_code"], "T260001")

            exsol_second = self._create_customer(self.sales_token, company_id=self.exsol.id, customer_name="C")
            self.assertEqual(exsol_second["customer_code"], "E260002")

    def _csv_bytes(self, rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["customer_code", "customer_name", "area_code", "city", "district", "province", "managed_by"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue().encode("utf-8")

    def _bulk_import(self, rows, strict=False, expected_status=200):
        payload = {
            "company_id": str(self.exsol.id),
            "strict_mode": "true" if strict else "false",
            "file": (io.BytesIO(self._csv_bytes(rows)), "bulk.csv"),
        }
        resp = self.client.post(
            "/api/dealers/bulk-import",
            data=payload,
            content_type="multipart/form-data",
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, expected_status, resp.get_data(as_text=True))
        return resp.get_json()

    def _bulk_validate(self, rows, expected_status=200):
        payload = {
            "company_id": str(self.exsol.id),
            "file": (io.BytesIO(self._csv_bytes(rows)), "bulk.csv"),
        }
        resp = self.client.post(
            "/api/dealers/bulk-validate",
            data=payload,
            content_type="multipart/form-data",
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, expected_status, resp.get_data(as_text=True))
        return resp.get_json()

    def test_bulk_validation_detects_errors_and_counts(self):
        rows = [
            {
                "customer_code": "",
                "customer_name": "Dealer A",
                "area_code": "100",
                "city": "Colombo",
                "district": "Colombo",
                "province": "Western",
                "managed_by": str(self.sales.id),
            },
            {
                "customer_code": "",
                "customer_name": "",
                "area_code": "",
                "city": "",
                "district": "",
                "province": "",
                "managed_by": "unknown",
            },
        ]
        payload = self._bulk_validate(rows)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["valid_count"], 1)
        self.assertEqual(payload["data"]["failed_count"], 1)
        preview_errors = [row for row in payload["data"]["rows"] if row.get("error")]
        self.assertEqual(len(preview_errors), 1)

    def test_bulk_import_inserts_valid_rows_and_generates_codes(self):
        rows = [
            {
                "customer_code": "",
                "customer_name": "Dealer A",
                "area_code": "100",
                "city": "Colombo",
                "district": "Colombo",
                "province": "Western",
                "managed_by": str(self.sales.id),
            },
            {
                "customer_code": "E250123",
                "customer_name": "Dealer B",
                "area_code": "200",
                "city": "Galle",
                "district": "Galle",
                "province": "Southern",
                "managed_by": self.sales.name,
            },
        ]
        payload = self._bulk_import(rows)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["inserted_count"], 2)
        self.assertEqual(payload["data"]["failed_count"], 0)
        self.assertEqual(payload["data"]["generated_codes_count"], 1)
        total = self.app_module.db.session.query(self.NonSamproxCustomer).count()
        self.assertEqual(total, 2)

    def test_bulk_import_partial_success_when_not_strict(self):
        rows = [
            {
                "customer_code": "",
                "customer_name": "Dealer A",
                "area_code": "100",
                "city": "Colombo",
                "district": "Colombo",
                "province": "Western",
                "managed_by": str(self.sales.id),
            },
            {
                "customer_code": "E250999",
                "customer_name": "",
                "area_code": "",
                "city": "",
                "district": "",
                "province": "",
                "managed_by": "missing",
            },
        ]
        payload = self._bulk_import(rows)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["inserted_count"], 1)
        self.assertEqual(payload["data"]["failed_count"], 1)
        self.assertIsNotNone(payload["data"]["error_report_token"])
        total = self.app_module.db.session.query(self.NonSamproxCustomer).count()
        self.assertEqual(total, 1)

    def test_bulk_import_strict_mode_aborts_on_error(self):
        rows = [
            {
                "customer_code": "E250555",
                "customer_name": "Dealer A",
                "area_code": "100",
                "city": "Colombo",
                "district": "Colombo",
                "province": "Western",
                "managed_by": str(self.sales.id),
            },
            {
                "customer_code": "E250555",
                "customer_name": "Dealer B",
                "area_code": "200",
                "city": "Galle",
                "district": "Galle",
                "province": "Southern",
                "managed_by": str(self.sales.id),
            },
        ]
        payload = self._bulk_import(rows, strict=True, expected_status=400)
        self.assertFalse(payload["ok"])
        total = self.app_module.db.session.query(self.NonSamproxCustomer).count()
        self.assertEqual(total, 0)

    def test_bulk_import_rejects_duplicate_customer_code(self):
        existing = self._create_customer(self.sales_token, company_id=self.exsol.id, customer_name="Existing")
        rows = [
            {
                "customer_code": existing["customer_code"],
                "customer_name": "Dealer A",
                "area_code": "100",
                "city": "Colombo",
                "district": "Colombo",
                "province": "Western",
                "managed_by": str(self.sales.id),
            }
        ]
        payload = self._bulk_import(rows, expected_status=400, strict=True)
        self.assertFalse(payload["ok"])
        total = self.app_module.db.session.query(self.NonSamproxCustomer).count()
        self.assertEqual(total, 1)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
