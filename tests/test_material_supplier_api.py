import importlib
import os
import sys
import unittest


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

        response = self.client.get(
            "/api/material/suppliers",
            query_string={"search": str(supplier.id)[:8]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(any(row["id"] == str(supplier.id) for row in data))

        response = self.client.get(
            "/api/material/suppliers",
            query_string={"search": supplier.supplier_reg_no[-2:]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(any(row["id"] == str(supplier.id) for row in data))
