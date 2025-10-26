import importlib
import os
import sys
import unittest
from datetime import datetime, timezone


class MaterialMRNApiTestCase(unittest.TestCase):
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

        from material import seed_material_defaults
        from material import create_supplier as create_supplier_service

        seed_material_defaults()

        self.client = self.app.test_client()
        self.Supplier = self.app_module.Supplier
        self.MaterialItem = self.app_module.MaterialItem
        self.MRNHeader = self.app_module.MRNHeader
        self.create_supplier_service = create_supplier_service

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _create_supplier(self, name="Acme Timber"):
        existing = self.Supplier.query.filter_by(name=name).first()
        if existing:
            return existing
        payload = {
            "name": name,
            "primary_phone": "011-555-1234",
            "secondary_phone": None,
            "category": "Raw Material",
            "vehicle_no_1": "TRK-001",
            "supplier_id_no": f"SID-{name.replace(' ', '-').upper()}",
            "credit_period": "Cash",
        }
        supplier = self.create_supplier_service(payload)
        return supplier

    def _default_payload(self):
        item = self.MaterialItem.query.filter_by(name="Wood Shaving").first()
        if not item:
            item = self.MaterialItem.query.first()
        supplier = self._create_supplier()
        weigh_in = datetime(2024, 8, 10, 9, 0, tzinfo=timezone.utc)
        weigh_out = datetime(2024, 8, 10, 10, 0, tzinfo=timezone.utc)
        return {
            "mrn_no": "MRN-001",
            "date": "2024-08-10",
            "supplier_id": str(supplier.id),
            "item_id": str(item.id),
            "qty_ton": 12.345,
            "unit_price": 95.5,
            "wet_factor": 1.1,
            "weighing_slip_no": "WS-9001",
            "weigh_in_time": weigh_in.isoformat(),
            "weigh_out_time": weigh_out.isoformat(),
            "security_officer_name": "Officer Jane",
            "authorized_person_name": "Manager John",
            "approved_unit_price": 5,  # should be ignored by server
            "amount": 1,
        }

    def _create_mrn(self, overrides=None):
        payload = self._default_payload()
        overrides = overrides or {}
        if "supplier_id" in overrides and overrides["supplier_id"] is None:
            payload.pop("supplier_id", None)
        payload.update({k: v for k, v in overrides.items() if k != "supplier_id"})
        if "supplier_id" in overrides and overrides["supplier_id"] is not None:
            payload["supplier_id"] = overrides["supplier_id"]
        response = self.client.post("/api/material/mrn", json=payload)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def test_create_mrn_success(self):
        payload = self._default_payload()
        response = self.client.post("/api/material/mrn", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.get_json()

        self.assertIn("id", data)
        self.assertEqual(data["mrn_no"], payload["mrn_no"])
        self.assertEqual(data["approved_unit_price"], "105.05")
        self.assertEqual(data["amount"], "1296.84")
        self.assertEqual(data["supplier_id"], payload["supplier_id"])

        mrn = self.MRNHeader.query.filter_by(mrn_no=payload["mrn_no"]).first()
        self.assertIsNotNone(mrn)
        self.assertAlmostEqual(float(mrn.approved_unit_price), 105.05)
        self.assertAlmostEqual(float(mrn.amount), 1296.84)

    def test_create_mrn_validation_errors(self):
        payload = self._default_payload()
        payload["mrn_no"] = ""
        payload["qty_ton"] = -1
        payload["weigh_out_time"] = datetime(2024, 8, 10, 8, 30, tzinfo=timezone.utc).isoformat()

        response = self.client.post("/api/material/mrn", json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("errors", data)
        self.assertIn("mrn_no", data["errors"])
        self.assertIn("qty_ton", data["errors"])
        self.assertNotIn("weigh_out_time", data["errors"])

        # weigh-out validation should trigger when other fields are valid
        valid_payload = self._default_payload()
        valid_payload["weigh_out_time"] = datetime(2024, 8, 10, 8, 30, tzinfo=timezone.utc).isoformat()
        response = self.client.post("/api/material/mrn", json=valid_payload)
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("weigh_out_time", data["errors"])

    def test_mrn_number_must_be_unique(self):
        payload = self._default_payload()
        response = self.client.post("/api/material/mrn", json=payload)
        self.assertEqual(response.status_code, 201)

        duplicate_response = self.client.post("/api/material/mrn", json=payload)
        self.assertEqual(duplicate_response.status_code, 400)
        errors = duplicate_response.get_json().get("errors")
        self.assertIn("mrn_no", errors)

    def test_list_mrn_returns_recent_entries(self):
        first = self._create_mrn()
        second = self._create_mrn(
            {
                "mrn_no": "MRN-002",
                "date": "2024-08-11",
                "weighing_slip_no": "WS-9002",
            }
        )

        response = self.client.get("/api/material/mrn")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)
        self.assertEqual(data[0]["mrn_no"], second["mrn_no"])
        self.assertEqual(data[1]["mrn_no"], first["mrn_no"])

        search_response = self.client.get("/api/material/mrn", query_string={"q": "MRN-002"})
        self.assertEqual(search_response.status_code, 200)
        search_data = search_response.get_json()
        self.assertEqual(len(search_data), 1)
        self.assertEqual(search_data[0]["mrn_no"], "MRN-002")

