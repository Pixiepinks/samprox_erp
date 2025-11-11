import importlib
import os
import sys
import unittest
from datetime import date

from models import SalesActualEntry, TeamMember, TeamMemberStatus


class MarketApiTestCase(unittest.TestCase):
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

        admin = User(name="Admin", email="admin@example.com", role=RoleEnum.admin)
        admin.set_password("Password!1")
        self.app_module.db.session.add(admin)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login()
        self.db = self.app_module.db
        self.TeamMember = TeamMember
        self.TeamMemberStatus = TeamMemberStatus
        self.SalesActualEntry = SalesActualEntry

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _create_customer_payload(self, **overrides):
        payload = dict(
            name="ACME Holdings",
            category="Industrial",
            credit_term="30 Days",
            transport_mode="Customer lorry",
            customer_type="Regular",
            sales_coordinator_name="Alex",
            sales_coordinator_phone="0710000000",
            store_keeper_name="Sam",
            store_keeper_phone="0711111111",
            payment_coordinator_name="Chris",
            payment_coordinator_phone="0712222222",
            special_note="Key account",
        )
        payload.update(overrides)
        return payload

    def _create_team_member(self, reg_number="EMP-001", name="Employee"):
        existing = self.TeamMember.query.filter_by(reg_number=reg_number).first()
        if existing:
            return existing

        member = self.TeamMember(
            reg_number=reg_number,
            name=name,
            join_date=date(2020, 1, 1),
            status=self.TeamMemberStatus.ACTIVE,
        )
        self.db.session.add(member)
        self.db.session.commit()
        return member

    def test_create_customer_accepts_human_friendly_enum_labels(self):
        response = self.client.post(
            "/api/market/customers",
            json=self._create_customer_payload(),
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["customer"]["category"], "industrial")
        self.assertEqual(data["customer"]["credit_term"], "30_days")
        self.assertEqual(data["customer"]["transport_mode"], "customer_lorry")
        self.assertEqual(data["customer"]["customer_type"], "regular")

    def test_create_customer_allows_empty_special_note(self):
        payload = self._create_customer_payload(special_note="   ")
        response = self.client.post(
            "/api/market/customers",
            json=payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["customer"]["special_note"], "")

    def test_create_customer_persists_enum_values_for_days_credit_terms(self):
        payload = self._create_customer_payload(
            name="Noyan Lanka Pvt Ltd",
            category="industrial",
            credit_term="14_days",
            transport_mode="samprox_lorry",
            customer_type="regular",
        )

        response = self.client.post(
            "/api/market/customers",
            json=payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["customer"]["credit_term"], "14_days")

        Customer = self.app_module.Customer
        CustomerCreditTerm = self.app_module.CustomerCreditTerm
        created = Customer.query.filter_by(name="Noyan Lanka Pvt Ltd").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.credit_term, CustomerCreditTerm.days14)

    def test_record_actual_sale_requires_transport_fields_for_samprox_customer(self):
        customer_payload = self._create_customer_payload(
            name="Samprox Logistics",
            transport_mode="Samprox lorry",
        )
        response = self.client.post(
            "/api/market/customers",
            json=customer_payload,
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        customer_id = response.get_json()["customer"]["id"]

        loader = self._create_team_member("LD-001", "Loader One")

        sale_payload = {
            "date": "2024-12-01",
            "customer_id": str(customer_id),
            "sale_type": "actual",
            "unit_price": "2500",
            "quantity_tons": "5",
            "delivery_note_number": "DN-100",
            "weigh_slip_number": "WS-100",
            "loader1_id": str(loader.id),
        }

        response = self.client.post(
            "/api/market/sales",
            json=sale_payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("Vehicle No", data.get("msg", ""))

    def test_record_actual_sale_saves_transport_fields_for_samprox_customer(self):
        customer_payload = self._create_customer_payload(
            name="Samprox Partner",
            transport_mode="Samprox lorry",
        )
        response = self.client.post(
            "/api/market/customers",
            json=customer_payload,
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        customer_id = response.get_json()["customer"]["id"]

        loader1 = self._create_team_member("LD-101", "Loader One")
        loader2 = self._create_team_member("LD-102", "Loader Two")
        driver = self._create_team_member("DRV-201", "Driver Example")
        helper1 = self._create_team_member("HLP-301", "Helper One")
        helper2 = self._create_team_member("HLP-302", "Helper Two")

        sale_payload = {
            "date": "2024-12-05",
            "customer_id": str(customer_id),
            "sale_type": "actual",
            "unit_price": "3200",
            "quantity_tons": "3.5",
            "delivery_note_number": "DN-200",
            "weigh_slip_number": "WS-200",
            "loader1_id": str(loader1.id),
            "loader2_id": str(loader2.id),
            "vehicle_number": "LI-1795",
            "driver_id": str(driver.id),
            "helper1_id": str(helper1.id),
            "helper2_id": str(helper2.id),
            "mileage_km": "85.5",
        }

        response = self.client.post(
            "/api/market/sales",
            json=sale_payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        entry_data = data.get("entry", {})
        self.assertEqual(entry_data.get("vehicle_number"), "LI-1795")
        self.assertEqual(int(entry_data.get("driver_id")), driver.id)
        self.assertEqual(int(entry_data.get("helper1_id")), helper1.id)
        self.assertEqual(int(entry_data.get("helper2_id")), helper2.id)
        self.assertAlmostEqual(entry_data.get("mileage_km"), 85.5)

        created_entry = self.SalesActualEntry.query.get(entry_data.get("id"))
        self.assertIsNotNone(created_entry)
        self.assertEqual(created_entry.vehicle_number, "LI-1795")
        self.assertEqual(created_entry.driver_id, driver.id)
        self.assertEqual(created_entry.helper1_id, helper1.id)
        self.assertEqual(created_entry.helper2_id, helper2.id)
        self.assertAlmostEqual(created_entry.mileage_km, 85.5)

    def test_record_actual_sale_allows_missing_transport_for_customer_lorry(self):
        customer_payload = self._create_customer_payload(
            name="Customer Logistics",
            transport_mode="Customer lorry",
        )
        response = self.client.post(
            "/api/market/customers",
            json=customer_payload,
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        customer_id = response.get_json()["customer"]["id"]

        loader = self._create_team_member("LD-501", "Loader Solo")

        sale_payload = {
            "date": "2024-12-10",
            "customer_id": str(customer_id),
            "sale_type": "actual",
            "unit_price": "2100",
            "quantity_tons": "4",
            "delivery_note_number": "DN-250",
            "weigh_slip_number": "WS-250",
            "loader1_id": str(loader.id),
        }

        response = self.client.post(
            "/api/market/sales",
            json=sale_payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()
        entry_data = data.get("entry", {})
        self.assertIsNone(entry_data.get("vehicle_number"))
        self.assertIsNone(entry_data.get("driver_id"))
        self.assertIsNone(entry_data.get("helper1_id"))
        self.assertIsNone(entry_data.get("helper2_id"))
        self.assertIsNone(entry_data.get("mileage_km"))

        created_entry = self.SalesActualEntry.query.get(entry_data.get("id"))
        self.assertIsNotNone(created_entry)
        self.assertIsNone(created_entry.vehicle_number)
        self.assertIsNone(created_entry.driver_id)
        self.assertIsNone(created_entry.helper1_id)
        self.assertIsNone(created_entry.helper2_id)
        self.assertIsNone(created_entry.mileage_km)


if __name__ == "__main__":
    unittest.main()
