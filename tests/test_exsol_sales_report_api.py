import importlib
import os
import sys
import unittest
from datetime import date


class ExsolSalesReportApiTestCase(unittest.TestCase):
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

        from models import Company, RoleEnum, User

        self.company = Company(
            key="exsol-engineering",
            name="Exsol Engineering (Pvt) Ltd",
            company_code_prefix="E",
        )
        self.app_module.db.session.add(self.company)
        self.app_module.db.session.commit()

        self.user = User(
            name="Sales Manager",
            email="sales@example.com",
            role=RoleEnum.sales_manager,
            active=True,
            company_key="exsol-engineering",
        )
        self.user.set_password("Password!1")
        self.app_module.db.session.add(self.user)
        self.app_module.db.session.commit()

        self.client = self.app.test_client()
        self.token = self._login("sales@example.com")

        self._seed_invoices()

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

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _seed_invoices(self):
        from models import ExsolSalesInvoice, ExsolSalesReceipt, NonSamproxCustomer

        customer = NonSamproxCustomer(
            customer_code="E260001",
            customer_name="Exsol Customer",
            managed_by_user_id=self.user.id,
            company_id=self.company.id,
        )
        self.app_module.db.session.add(customer)
        self.app_module.db.session.flush()

        invoice_one = ExsolSalesInvoice(
            company_key="EXSOL",
            invoice_no="INV-001",
            invoice_date=date(2024, 1, 5),
            customer_id=customer.id,
            sales_rep_id=self.user.id,
            subtotal=100,
            discount_total=0,
            grand_total=100,
            created_by_user_id=self.user.id,
        )
        invoice_two = ExsolSalesInvoice(
            company_key="EXSOL",
            invoice_no="INV-002",
            invoice_date=date(2024, 1, 6),
            customer_id=customer.id,
            sales_rep_id=self.user.id,
            subtotal=200,
            discount_total=0,
            grand_total=200,
            created_by_user_id=self.user.id,
        )
        self.app_module.db.session.add_all([invoice_one, invoice_two])
        self.app_module.db.session.flush()

        receipt = ExsolSalesReceipt(
            company_key="EXSOL",
            invoice_id=invoice_two.id,
            receipt_date=date(2024, 1, 10),
            amount=200,
        )
        self.app_module.db.session.add(receipt)
        self.app_module.db.session.commit()

    def test_invoice_report_returns_kpis(self):
        resp = self.client.get("/api/exsol/reports/sales/invoices", headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        payload = resp.get_json()
        self.assertEqual(payload["kpis"]["invoice_count"], 2)
        self.assertEqual(payload["kpis"]["gross_sales"], 300.0)
        self.assertEqual(payload["kpis"]["paid"], 200.0)
        self.assertEqual(payload["kpis"]["due"], 100.0)

        rows = {row["invoice_no"]: row for row in payload["rows"]}
        self.assertEqual(rows["INV-001"]["status"], "Unpaid")
        self.assertEqual(rows["INV-002"]["status"], "Paid")


if __name__ == "__main__":
    unittest.main()
