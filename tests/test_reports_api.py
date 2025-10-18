import importlib
import os
import sys
import unittest
from datetime import date


class ReportsApiTestCase(unittest.TestCase):
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

        self.user = User(name="Planner", email="planner@example.com", role=RoleEnum.production_manager)
        self.user.set_password("Password!1")
        self.app_module.db.session.add(self.user)
        self.app_module.db.session.commit()

        self.token = self._login()

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
            json={"email": "planner@example.com", "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_customer_sales_report_combines_forecast_and_actual(self):
        Customer = self.app_module.Customer
        CustomerCategory = self.app_module.CustomerCategory
        CustomerCreditTerm = self.app_module.CustomerCreditTerm
        CustomerTransportMode = self.app_module.CustomerTransportMode
        CustomerType = self.app_module.CustomerType
        SalesForecastEntry = self.app_module.SalesForecastEntry
        SalesActualEntry = self.app_module.SalesActualEntry

        customer_kwargs = dict(
            category=CustomerCategory.plantation,
            credit_term=CustomerCreditTerm.cash,
            transport_mode=CustomerTransportMode.samprox_lorry,
            customer_type=CustomerType.regular,
            sales_coordinator_name="Alex",
            sales_coordinator_phone="0710000000",
            store_keeper_name="Sam",
            store_keeper_phone="0711111111",
            payment_coordinator_name="Chris",
            payment_coordinator_phone="0712222222",
            special_note="Key account",
        )

        acme = Customer(name="ACME Corp", **customer_kwargs)
        beta = Customer(name="Beta Industries", **customer_kwargs)
        self.app_module.db.session.add_all([acme, beta])
        self.app_module.db.session.commit()

        entries = [
            SalesForecastEntry(
                customer_id=acme.id,
                date=date(2024, 5, 1),
                amount=100.0,
                unit_price=10.0,
                quantity_tons=10.0,
            ),
            SalesForecastEntry(
                customer_id=acme.id,
                date=date(2024, 5, 2),
                amount=150.0,
                unit_price=10.0,
                quantity_tons=15.0,
            ),
            SalesForecastEntry(
                customer_id=beta.id,
                date=date(2024, 5, 3),
                amount=250.0,
                unit_price=10.0,
                quantity_tons=25.0,
            ),
            SalesActualEntry(
                customer_id=acme.id,
                date=date(2024, 5, 1),
                amount=90.0,
                unit_price=10.0,
                quantity_tons=9.0,
            ),
            SalesActualEntry(
                customer_id=acme.id,
                date=date(2024, 5, 3),
                amount=200.0,
                unit_price=10.0,
                quantity_tons=20.0,
            ),
            SalesActualEntry(
                customer_id=beta.id,
                date=date(2024, 5, 3),
                amount=300.0,
                unit_price=10.0,
                quantity_tons=30.0,
            ),
        ]
        self.app_module.db.session.add_all(entries)
        self.app_module.db.session.commit()

        response = self.client.get(
            "/api/reports/customer-sales",
            headers=self._auth_headers(),
            query_string={"year": 2024, "month": 5},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["year"], 2024)
        self.assertEqual(data["month"], 5)
        self.assertEqual(len(data["customers"]), 2)

        acme_report = next(item for item in data["customers"] if item["customer_name"] == "ACME Corp")
        beta_report = next(item for item in data["customers"] if item["customer_name"] == "Beta Industries")

        self.assertAlmostEqual(acme_report["monthly_forecast_total"], 250.0)
        self.assertAlmostEqual(acme_report["monthly_actual_total"], 290.0)
        self.assertAlmostEqual(
            acme_report["monthly_forecast_quantity_tons"], 25.0
        )
        self.assertAlmostEqual(acme_report["monthly_actual_quantity_tons"], 29.0)
        self.assertAlmostEqual(acme_report["monthly_average_unit_price"], 10.0)
        self.assertAlmostEqual(acme_report["monthly_total_sales_amount"], 290.0)
        self.assertEqual(
            acme_report["dates"],
            [
                {
                    "date": "2024-05-01",
                    "forecast_amount": 100.0,
                    "actual_amount": 90.0,
                    "forecast_quantity_tons": 10.0,
                    "actual_quantity_tons": 9.0,
                },
                {
                    "date": "2024-05-02",
                    "forecast_amount": 150.0,
                    "actual_amount": 0.0,
                    "forecast_quantity_tons": 15.0,
                    "actual_quantity_tons": 0.0,
                },
                {
                    "date": "2024-05-03",
                    "forecast_amount": 0.0,
                    "actual_amount": 200.0,
                    "forecast_quantity_tons": 0.0,
                    "actual_quantity_tons": 20.0,
                },
            ],
        )

        self.assertAlmostEqual(beta_report["monthly_forecast_total"], 250.0)
        self.assertAlmostEqual(beta_report["monthly_actual_total"], 300.0)
        self.assertAlmostEqual(beta_report["monthly_forecast_quantity_tons"], 25.0)
        self.assertAlmostEqual(beta_report["monthly_actual_quantity_tons"], 30.0)
        self.assertAlmostEqual(beta_report["monthly_average_unit_price"], 10.0)
        self.assertAlmostEqual(beta_report["monthly_total_sales_amount"], 300.0)
        self.assertEqual(
            beta_report["dates"],
            [
                {
                    "date": "2024-05-03",
                    "forecast_amount": 250.0,
                    "actual_amount": 300.0,
                    "forecast_quantity_tons": 25.0,
                    "actual_quantity_tons": 30.0,
                }
            ],
        )

