import importlib
import os
import sys
import unittest
from unittest import mock


class MaterialSupplierApiTestCase(unittest.TestCase):
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

        from material import create_supplier as create_supplier_service

        self.create_supplier_service = create_supplier_service
        self.client = self.app.test_client()

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _create_supplier(self, **overrides):
        payload = {
            "name": "Sample Supplier",
            "primary_phone": "0770000000",
            "category": "Raw Material",
            "vehicle_no_1": "ABC-1234",
            "supplier_id_no": "SID-001",
            "credit_period": "Cash",
        }
        payload.update(overrides)
        return self.create_supplier_service(payload)

    def test_search_supplier_matches_multiple_fields(self):
        supplier = self._create_supplier(
            name="Taxed Supplier",
            secondary_phone="0711234567",
            vehicle_no_2="DEF-5678",
            email="supplier@example.com",
            address="123 Example Street",
            tax_id="VAT-123",
        )

        response = self.client.get(
            "/api/material/suppliers",
            query_string={"search": "vat"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(any(row["id"] == str(supplier.id) for row in data))

    def test_create_supplier_retries_registration_conflict(self):
        with mock.patch(
            "material.services.get_next_supplier_registration_no",
            side_effect=["SR0001", "SR0001", "SR0002"],
        ):
            first = self._create_supplier(name="Retry Supplier", supplier_id_no="SID-010")
            self.assertEqual(first.supplier_reg_no, "SR0001")

            second = self.create_supplier_service(
                {
                    "name": "Retry Supplier B",
                    "primary_phone": "0771234567",
                    "category": "Raw Material",
                    "vehicle_no_1": "ABC-2345",
                    "supplier_id_no": "SID-011",
                    "credit_period": "Cash",
                }
            )
            self.assertEqual(second.supplier_reg_no, "SR0002")

    def test_create_supplier_api_returns_created_supplier(self):
        payload = {
            "name": "API Supplier",
            "primary_phone": "0779999999",
            "category": "Packing Material",
            "supplier_id_no": "SID-100",
            "credit_period": "Cash",
        }

        response = self.client.post("/api/material/suppliers", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], payload["name"])
        self.assertEqual(data["primaryPhone"], payload["primary_phone"])
        self.assertTrue(data["supplierRegNo"].startswith("SR"))

        supplier_id = data["id"]
        registration_no = data["supplierRegNo"]

        response = self.client.get(
            "/api/material/suppliers",
            query_string={"search": supplier_id[:8]},
        )
        self.assertEqual(response.status_code, 200)
        search_data = response.get_json()
        self.assertTrue(any(row["id"] == supplier_id for row in search_data))

        response = self.client.get(
            "/api/material/suppliers",
            query_string={"search": registration_no[-2:]},
        )
        self.assertEqual(response.status_code, 200)
        search_data = response.get_json()
        self.assertTrue(any(row["id"] == supplier_id for row in search_data))
