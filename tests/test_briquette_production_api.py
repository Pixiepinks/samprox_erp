import importlib
import os
import sys
import unittest
from datetime import date, datetime, time, timezone

from models import DailyProductionEntry


class BriquetteProductionApiTestCase(unittest.TestCase):
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

        from material import seed_material_defaults

        seed_material_defaults()

        self.client = self.app.test_client()
        self.db = self.app_module.db
        self.Supplier = self.app_module.Supplier
        self.MaterialItem = self.app_module.MaterialItem
        self.MRNHeader = self.app_module.MRNHeader
        self.MRNLine = self.app_module.MRNLine
        self.MachineAsset = self.app_module.MachineAsset

        self._ensure_material_item("Fire Cut")
        self.supplier = self._create_supplier()
        self.assets = self._create_assets()
        self.production_date = date(2025, 10, 1)
        self._create_daily_production()
        self._create_receipts()

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _ensure_material_item(self, name):
        existing = self.MaterialItem.query.filter_by(name=name).first()
        if existing:
            return existing
        item = self.MaterialItem(name=name, is_active=True)
        self.db.session.add(item)
        self.db.session.commit()
        return item

    def _create_supplier(self):
        supplier = self.Supplier(
            name="Acme Biomass",
            primary_phone="011-0000000",
            category="Raw Material",
            supplier_id_no="SUP-001",
            supplier_reg_no="SR0001",
            credit_period="Cash",
            vehicle_no_1="TRK-001",
        )
        self.db.session.add(supplier)
        self.db.session.commit()
        return supplier

    def _create_assets(self):
        assets = {}
        for code, name in {
            "MCH-0001": "Briquette Line 1",
            "MCH-0002": "Briquette Line 2",
            "MCH-0003": "Dryer",
        }.items():
            asset = self.MachineAsset(code=code, name=name, status="Running")
            self.db.session.add(asset)
            assets[code] = asset
        self.db.session.commit()
        return assets

    def _create_daily_production(self):
        entries = [
            ("MCH-0001", 1, 2.0),
            ("MCH-0002", 1, 1.0),
            ("MCH-0003", 1, 0.5),
        ]
        for code, hour_no, quantity in entries:
            asset = self.assets[code]
            entry = DailyProductionEntry(
                date=self.production_date,
                hour_no=hour_no,
                quantity_tons=quantity,
                asset_id=asset.id,
            )
            self.db.session.add(entry)
        self.db.session.commit()

    def _create_receipts(self):
        self._add_receipt(
            item_name="Wood Powder",
            qty_kg=1000,
            unit_price_per_kg=12.0,
            receipt_date=date(2025, 9, 30),
        )
        self._add_receipt(
            item_name="Peanut Husk",
            qty_kg=500,
            unit_price_per_kg=7.0,
            receipt_date=date(2025, 9, 29),
        )

    def _add_receipt(self, *, item_name, qty_kg, unit_price_per_kg, receipt_date):
        item = self.MaterialItem.query.filter_by(name=item_name).first()
        if not item:
            item = self._ensure_material_item(item_name)

        qty_kg = float(qty_kg)
        qty_ton = qty_kg / 1000
        unit_price_ton = unit_price_per_kg * 1000
        amount = qty_ton * unit_price_ton

        header = self.MRNHeader(
            mrn_no=f"MRN-{item_name[:3].upper()}-{qty_kg}",
            date=receipt_date,
            supplier_id=self.supplier.id,
            qty_ton=qty_ton,
            amount=amount,
            weighing_slip_no="WS-001",
            weigh_in_time=datetime.combine(receipt_date, time(8, 0), tzinfo=timezone.utc),
            weigh_out_time=datetime.combine(receipt_date, time(9, 0), tzinfo=timezone.utc),
            security_officer_name="Officer",
            authorized_person_name="Manager",
            vehicle_no="TRK-001",
        )
        self.db.session.add(header)
        self.db.session.flush()

        line = self.MRNLine(
            mrn_id=header.id,
            item_id=item.id,
            first_weight_kg=qty_kg + 100,
            second_weight_kg=100,
            qty_ton=qty_ton,
            unit_price=unit_price_ton,
            approved_unit_price=unit_price_ton,
            amount=amount,
            wet_factor=1,
        )
        self.db.session.add(line)
        self.db.session.commit()

    def test_update_mix_and_fifo_costing(self):
        response = self.client.get("/api/material/briquette-production")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("entries", data)
        self.assertTrue(any(entry["date"] == self.production_date.isoformat() for entry in data["entries"]))

        payload = {
            "dry_factor": 0.4,
            "wood_shaving_ton": 0.0,
            "wood_powder_ton": 0.2,
            "peanut_husk_ton": 0.1,
            "fire_cut_ton": 0.05,
        }
        response = self.client.post(
            f"/api/material/briquette-production/{self.production_date.isoformat()}",
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        mix_data = response.get_json()

        self.assertAlmostEqual(mix_data["sawdust_ton"], 1.25, places=3)
        self.assertAlmostEqual(mix_data["wood_shaving_ton"], 2.2, places=3)
        self.assertAlmostEqual(mix_data["dry_material_ton"], 3.0, places=3)
        self.assertAlmostEqual(mix_data["dryer_actual_running_hours"], 1.0, places=1)
        self.assertAlmostEqual(mix_data["total_material_cost"], 35254.5, places=2)
        self.assertAlmostEqual(mix_data["unit_cost_per_kg"], 11.7515, places=4)

        breakdown = {item["key"]: item for item in mix_data["cost_breakdown"]}
        self.assertIn("wood_powder", breakdown)
        self.assertAlmostEqual(breakdown["wood_powder"]["quantity_ton"], 0.2, places=3)
        self.assertAlmostEqual(breakdown["wood_powder"]["total_cost"], 2400.0, places=2)

        list_response = self.client.get("/api/material/briquette-production")
        self.assertEqual(list_response.status_code, 200)
        list_data = list_response.get_json()
        day_entry = next(
            (entry for entry in list_data.get("entries", []) if entry["date"] == self.production_date.isoformat()),
            None,
        )
        self.assertIsNotNone(day_entry)
        self.assertAlmostEqual(day_entry["total_material_cost"], 35254.5, places=2)
        self.assertAlmostEqual(day_entry["unit_cost_per_kg"], 11.7515, places=4)

    def test_update_mix_rejects_negative_wood_shaving(self):
        payload = {
            "dry_factor": 0.4,
            "wood_shaving_ton": 0.0,
            "wood_powder_ton": 3.0,
            "peanut_husk_ton": 1.0,
            "fire_cut_ton": 0.0,
        }
        response = self.client.post(
            f"/api/material/briquette-production/{self.production_date.isoformat()}",
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(
            data["msg"],
            "Invalid mix: Wood shaving quantity cannot be negative. Please check inputs.",
        )

    def test_update_mix_rejects_insufficient_stock(self):
        payload = {
            "dry_factor": 0.4,
            "wood_shaving_ton": 0.0,
            "wood_powder_ton": 1.5,
            "peanut_husk_ton": 0.2,
            "fire_cut_ton": 0.05,
        }
        response = self.client.post(
            f"/api/material/briquette-production/{self.production_date.isoformat()}",
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(
            data["msg"],
            "Insufficient stock for Wood Powder. Entry not saved. System does not allow negative stock.",
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
