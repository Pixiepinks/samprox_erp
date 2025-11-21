import importlib
import os
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch


class ReportsApiTestCase(unittest.TestCase):
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

        acme_forecast_day1 = SalesForecastEntry(
            customer_id=acme.id,
            date=date(2024, 5, 1),
            amount=100.0,
            unit_price=10.0,
            quantity_tons=10.0,
        )
        acme_forecast_day2 = SalesForecastEntry(
            customer_id=acme.id,
            date=date(2024, 5, 2),
            amount=150.0,
            unit_price=10.0,
            quantity_tons=15.0,
        )
        beta_forecast_day3 = SalesForecastEntry(
            customer_id=beta.id,
            date=date(2024, 5, 3),
            amount=250.0,
            unit_price=10.0,
            quantity_tons=25.0,
        )
        acme_actual_day1_primary = SalesActualEntry(
            customer_id=acme.id,
            date=date(2024, 5, 1),
            amount=90.0,
            unit_price=10.0,
            quantity_tons=9.0,
            delivery_note_number="DN-100",
        )
        acme_actual_day1_secondary = SalesActualEntry(
            customer_id=acme.id,
            date=date(2024, 5, 1),
            amount=30.0,
            unit_price=10.0,
            quantity_tons=3.0,
            delivery_note_number="DN-101",
        )
        acme_actual_day3 = SalesActualEntry(
            customer_id=acme.id,
            date=date(2024, 5, 3),
            amount=200.0,
            unit_price=10.0,
            quantity_tons=20.0,
            delivery_note_number="DN-200",
        )
        beta_actual_day3 = SalesActualEntry(
            customer_id=beta.id,
            date=date(2024, 5, 3),
            amount=300.0,
            unit_price=10.0,
            quantity_tons=30.0,
            delivery_note_number="DN-300",
        )

        entries = [
            acme_forecast_day1,
            acme_forecast_day2,
            beta_forecast_day3,
            acme_actual_day1_primary,
            acme_actual_day1_secondary,
            acme_actual_day3,
            beta_actual_day3,
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

        self.assertEqual(acme_report["customer_category"], "plantation")
        self.assertEqual(beta_report["customer_category"], "plantation")

        self.assertAlmostEqual(acme_report["monthly_forecast_total"], 250.0)
        self.assertAlmostEqual(acme_report["monthly_actual_total"], 320.0)
        self.assertAlmostEqual(
            acme_report["monthly_forecast_quantity_tons"], 25.0
        )
        self.assertAlmostEqual(acme_report["monthly_actual_quantity_tons"], 32.0)
        self.assertAlmostEqual(acme_report["monthly_average_unit_price"], 10.0)
        self.assertAlmostEqual(acme_report["monthly_total_sales_amount"], 320.0)

        expected_acme_dates = [
            {
                "date": "2024-05-01",
                "forecast_amount": 100.0,
                "actual_amount": 90.0,
                "forecast_quantity_tons": 10.0,
                "actual_quantity_tons": 9.0,
                "has_forecast_entry": True,
                "has_actual_entry": True,
                "delivery_note_number": "DN-100",
                "actual_entry_id": acme_actual_day1_primary.id,
            },
            {
                "date": "2024-05-01",
                "forecast_amount": 100.0,
                "actual_amount": 30.0,
                "forecast_quantity_tons": 10.0,
                "actual_quantity_tons": 3.0,
                "has_forecast_entry": True,
                "has_actual_entry": True,
                "delivery_note_number": "DN-101",
                "actual_entry_id": acme_actual_day1_secondary.id,
            },
            {
                "date": "2024-05-02",
                "forecast_amount": 150.0,
                "actual_amount": 0.0,
                "forecast_quantity_tons": 15.0,
                "actual_quantity_tons": 0.0,
                "has_forecast_entry": True,
                "has_actual_entry": False,
                "delivery_note_number": None,
                "actual_entry_id": None,
            },
            {
                "date": "2024-05-03",
                "forecast_amount": 0.0,
                "actual_amount": 200.0,
                "forecast_quantity_tons": 0.0,
                "actual_quantity_tons": 20.0,
                "has_forecast_entry": False,
                "has_actual_entry": True,
                "delivery_note_number": "DN-200",
                "actual_entry_id": acme_actual_day3.id,
            },
        ]

        self.assertEqual(acme_report["dates"], expected_acme_dates)

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
                    "has_forecast_entry": True,
                    "has_actual_entry": True,
                    "delivery_note_number": "DN-300",
                    "actual_entry_id": beta_actual_day3.id,
                }
            ],
        )

    def test_sales_summary_returns_monthly_snapshots(self):
        Customer = self.app_module.Customer
        CustomerCategory = self.app_module.CustomerCategory
        CustomerCreditTerm = self.app_module.CustomerCreditTerm
        CustomerTransportMode = self.app_module.CustomerTransportMode
        CustomerType = self.app_module.CustomerType
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
            SalesActualEntry(
                customer_id=acme.id,
                date=date(2024, 1, 10),
                amount=400.0,
                unit_price=10.0,
                quantity_tons=40.0,
            ),
            SalesActualEntry(
                customer_id=beta.id,
                date=date(2024, 2, 5),
                amount=500.0,
                unit_price=10.0,
                quantity_tons=50.0,
            ),
            SalesActualEntry(
                customer_id=acme.id,
                date=date(2024, 3, 1),
                amount=600.0,
                unit_price=10.0,
                quantity_tons=60.0,
            ),
            SalesActualEntry(
                customer_id=beta.id,
                date=date(2024, 3, 3),
                amount=700.0,
                unit_price=10.0,
                quantity_tons=70.0,
            ),
            SalesActualEntry(
                customer_id=beta.id,
                date=date(2024, 3, 10),
                amount=800.0,
                unit_price=10.0,
                quantity_tons=80.0,
            ),
        ]

        self.app_module.db.session.add_all(entries)
        self.app_module.db.session.commit()

        response = self.client.get(
            "/api/reports/sales-summary",
            headers=self._auth_headers(),
            query_string={"as_of": "2024-03-21"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["year"], 2024)
        self.assertEqual(data["month"], 3)
        self.assertEqual(data["as_of"], "2024-03-21")

        ytd = data["year_to_date"]
        self.assertAlmostEqual(ytd["quantity_tons"], 300.0)
        self.assertAlmostEqual(ytd["sales_value"], 3000.0)
        self.assertEqual(len(ytd["monthly_values"]), 12)
        self.assertEqual(ytd["monthly_values"][0], 400.0)
        self.assertEqual(ytd["monthly_values"][1], 500.0)
        self.assertEqual(ytd["monthly_values"][2], 2100.0)
        self.assertEqual(len(ytd["monthly_quantities"]), 12)
        self.assertEqual(ytd["monthly_quantities"][0], 40.0)
        self.assertEqual(ytd["monthly_quantities"][1], 50.0)
        self.assertEqual(ytd["monthly_quantities"][2], 210.0)

        mtd = data["month_to_date"]
        self.assertAlmostEqual(mtd["sales_value"], 2100.0)
        self.assertAlmostEqual(mtd["quantity_tons"], 210.0)
        self.assertEqual(len(mtd["daily_values"]), 31)
        self.assertEqual(mtd["daily_values"][0], 600.0)
        self.assertEqual(mtd["daily_values"][2], 700.0)
        self.assertEqual(mtd["daily_values"][9], 800.0)

        self.assertAlmostEqual(data["monthly_average_unit_price"], 10.0)

        top_customer = data["top_customer"]
        self.assertIsNotNone(top_customer)
        self.assertEqual(top_customer["name"], "Beta Industries")
        self.assertAlmostEqual(top_customer["quantity_tons"], 200.0)
        self.assertAlmostEqual(top_customer["sales_value"], 2000.0)

    def test_monthly_sales_summary_groups_top_customers(self):
        Customer = self.app_module.Customer
        CustomerCategory = self.app_module.CustomerCategory
        CustomerCreditTerm = self.app_module.CustomerCreditTerm
        CustomerTransportMode = self.app_module.CustomerTransportMode
        CustomerType = self.app_module.CustomerType
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

        customers = [
            Customer(name=f"Customer {index + 1}", **customer_kwargs)
            for index in range(6)
        ]
        self.app_module.db.session.add_all(customers)
        self.app_module.db.session.commit()

        daily_inputs = [
            (1, [100.0, 150.0, 80.0, 60.0, 40.0, 25.0]),
            (2, [120.0, 160.0, 90.0, 50.0, 70.0, 30.0]),
            (3, [130.0, 0.0, 95.0, 85.0, 50.0, 20.0]),
        ]

        entries = []
        for day, values in daily_inputs:
            for index, amount in enumerate(values):
                if amount <= 0:
                    continue
                entries.append(
                    SalesActualEntry(
                        customer_id=customers[index].id,
                        date=date(2024, 5, day),
                        amount=amount,
                        unit_price=10.0,
                        quantity_tons=amount / 10.0,
                    )
                )

        self.app_module.db.session.add_all(entries)
        self.app_module.db.session.commit()

        response = self.client.get(
            "/api/reports/sales/monthly-summary",
            headers=self._auth_headers(),
            query_string={"period": "2024-05"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["days"], 31)
        self.assertEqual(len(data["daily_totals"]), 31)

        self.assertEqual(len(data["customers"]), 6)
        customers_by_name = {item["name"]: item for item in data["customers"]}
        self.assertIn("Other customers", customers_by_name)
        other_field = customers_by_name["Other customers"]["field"]
        self.assertAlmostEqual(customers_by_name["Other customers"]["total_sales"], 75.0)

        fields_by_name = {
            name: meta["field"] for name, meta in customers_by_name.items()
        }

        day1 = next(item for item in data["daily_totals"] if item["day"] == 1)
        self.assertAlmostEqual(day1[fields_by_name["Customer 1"]], 100.0)
        self.assertAlmostEqual(day1[fields_by_name["Customer 2"]], 150.0)
        self.assertAlmostEqual(day1[fields_by_name["Customer 3"]], 80.0)
        self.assertAlmostEqual(day1[fields_by_name["Customer 4"]], 60.0)
        self.assertAlmostEqual(day1[fields_by_name["Customer 5"]], 40.0)
        self.assertAlmostEqual(day1[other_field], 25.0)
        self.assertAlmostEqual(day1["total_value"], 455.0)
        self.assertAlmostEqual(day1["total_quantity_tons"], 45.5)

        day3 = next(item for item in data["daily_totals"] if item["day"] == 3)
        self.assertAlmostEqual(day3[fields_by_name["Customer 1"]], 130.0)
        self.assertAlmostEqual(day3[fields_by_name["Customer 4"]], 85.0)
        self.assertAlmostEqual(day3[other_field], 20.0)

        day4 = next(item for item in data["daily_totals"] if item["day"] == 4)
        self.assertAlmostEqual(day4["total_value"], 0.0)
        self.assertAlmostEqual(day4.get(fields_by_name["Customer 1"], 0.0), 0.0)

        total_expected = 455.0 + 520.0 + 380.0
        total_quantity_expected = (455.0 + 520.0 + 380.0) / 10.0
        self.assertAlmostEqual(data["total_sales"], total_expected)
        self.assertAlmostEqual(
            data["average_day_sales"], round(total_expected / 31, 2)
        )
        self.assertAlmostEqual(data["total_quantity_tons"], round(total_quantity_expected, 2))
        self.assertAlmostEqual(
            data["average_day_quantity_tons"],
            round(total_quantity_expected / 31, 2),
        )

        peak = data["peak"]
        self.assertEqual(peak["day"], 2)
        self.assertAlmostEqual(peak["total_value"], 520.0)
        self.assertAlmostEqual(peak["total_quantity_tons"], 52.0)

        top_customer = data["top_customer"]
        self.assertIsNotNone(top_customer)
        self.assertEqual(top_customer["name"], "Customer 1")
        self.assertAlmostEqual(top_customer["sales_value"], 350.0)
        self.assertAlmostEqual(top_customer["quantity_tons"], 35.0)

    def test_material_monthly_summary_returns_daily_stacks(self):
        from material import seed_material_defaults

        seed_material_defaults()

        MaterialItem = self.app_module.MaterialItem
        MRNHeader = self.app_module.MRNHeader
        MRNLine = self.app_module.MRNLine
        db = self.app_module.db

        default_names = [
            "Wood Shaving",
            "Saw Dust",
            "Wood Powder",
            "Peanut Husk",
            "Briquettes",
        ]

        items = {
            item.name: item
            for item in MaterialItem.query.filter(MaterialItem.name.in_(default_names)).all()
        }

        self.assertEqual(len(items), len(default_names))

        other_item = MaterialItem(name="Rice Husk", is_active=True)
        db.session.add(other_item)
        db.session.commit()

        def add_mrn(day, item, qty):
            qty_decimal = Decimal(str(qty))
            amount_decimal = qty_decimal * Decimal("100.00")

            mrn = MRNHeader(
                mrn_no=f"MRN-{item.name[:3].upper()}-{day}",
                date=date(2024, 5, day),
                qty_ton=qty_decimal,
                amount=amount_decimal,
                weighing_slip_no=f"WS-{day:02d}",
                weigh_in_time=datetime(2024, 5, day, 8, 0, tzinfo=timezone.utc),
                weigh_out_time=datetime(2024, 5, day, 9, 0, tzinfo=timezone.utc),
                security_officer_name="Guard",
                authorized_person_name="Manager",
            )

            MRNLine(
                mrn=mrn,
                item=item,
                first_weight_kg=Decimal("20000.000"),
                second_weight_kg=Decimal("10000.000"),
                qty_ton=qty_decimal,
                unit_price=Decimal("100.00"),
                wet_factor=Decimal("1.000"),
                approved_unit_price=Decimal("100.00"),
                amount=amount_decimal,
            )

            db.session.add(mrn)

        add_mrn(1, items["Wood Shaving"], 10)
        add_mrn(1, items["Saw Dust"], 5)
        add_mrn(2, items["Wood Powder"], 7)
        add_mrn(2, items["Peanut Husk"], 3)
        add_mrn(2, other_item, 2)
        add_mrn(3, items["Wood Shaving"], 4)

        db.session.commit()

        response = self.client.get(
            "/api/reports/materials/monthly-summary",
            headers=self._auth_headers(),
            query_string={"period": "2024-05"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["days"], 31)
        self.assertEqual(len(data["daily_totals"]), 31)

        materials_by_name = {item["name"]: item for item in data["materials"]}
        self.assertIn("Wood Shaving", materials_by_name)
        self.assertIn("Saw Dust", materials_by_name)
        self.assertIn("Wood Powder", materials_by_name)
        self.assertIn("Peanut Husk", materials_by_name)
        self.assertIn("Other materials", materials_by_name)

        self.assertAlmostEqual(materials_by_name["Wood Shaving"]["total_quantity_tons"], 14.0)
        self.assertAlmostEqual(materials_by_name["Saw Dust"]["total_quantity_tons"], 5.0)
        self.assertAlmostEqual(materials_by_name["Wood Powder"]["total_quantity_tons"], 7.0)
        self.assertAlmostEqual(materials_by_name["Peanut Husk"]["total_quantity_tons"], 3.0)
        self.assertAlmostEqual(materials_by_name["Other materials"]["total_quantity_tons"], 2.0)

        field_map = {item["name"]: item["field"] for item in data["materials"]}

        day1 = next(entry for entry in data["daily_totals"] if entry["day"] == 1)
        self.assertAlmostEqual(day1[field_map["Wood Shaving"]], 10.0)
        self.assertAlmostEqual(day1[field_map["Saw Dust"]], 5.0)
        self.assertAlmostEqual(day1["total_quantity_tons"], 15.0)

        day2 = next(entry for entry in data["daily_totals"] if entry["day"] == 2)
        self.assertAlmostEqual(day2[field_map["Wood Powder"]], 7.0)
        self.assertAlmostEqual(day2[field_map["Peanut Husk"]], 3.0)
        self.assertAlmostEqual(day2.get(field_map.get("Other materials", ""), 0.0), 2.0)
        self.assertAlmostEqual(day2["total_quantity_tons"], 12.0)

        day4 = next(entry for entry in data["daily_totals"] if entry["day"] == 4)
        self.assertAlmostEqual(day4["total_quantity_tons"], 0.0)

        self.assertAlmostEqual(data["total_quantity_tons"], 31.0)
        self.assertAlmostEqual(data["average_day_quantity_tons"], round(31.0 / 31, 2))

        self.assertEqual(data["peak"]["day"], 1)
        self.assertAlmostEqual(data["peak"]["total_quantity_tons"], 15.0)

        top_material = data["top_material"]
        self.assertIsNotNone(top_material)
        self.assertEqual(top_material["name"], "Wood Shaving")
        self.assertAlmostEqual(top_material["quantity_tons"], 14.0)

    def test_labor_daily_production_cost_stacks_members(self):
        models_module = importlib.import_module("models")
        TeamMember = models_module.TeamMember
        TeamMemberStatus = models_module.TeamMemberStatus
        PayCategory = models_module.PayCategory
        TeamSalaryRecord = models_module.TeamSalaryRecord
        TeamAttendanceRecord = models_module.TeamAttendanceRecord
        db = self.app_module.db

        factory_member = TeamMember(
            reg_number="E100",
            name="Factory One",
            pay_category=PayCategory.FACTORY.value,
            status=TeamMemberStatus.ACTIVE.value,
            join_date=date(2023, 1, 1),
        )
        casual_member = TeamMember(
            reg_number="C200",
            name="Casual One",
            pay_category=PayCategory.CASUAL.value,
            status=TeamMemberStatus.ACTIVE.value,
            join_date=date(2023, 2, 1),
        )

        db.session.add_all([factory_member, casual_member])
        db.session.commit()

        factory_salary = TeamSalaryRecord(
            team_member_id=factory_member.id,
            month="2024-05",
            components={
                "basicSalary": "52000",
                "attendanceAllowance": "11000",
                "targetAllowance": "6000",
            },
        )
        casual_salary = TeamSalaryRecord(
            team_member_id=casual_member.id,
            month="2024-05",
            components={
                "daySalary": "3000",
                "casualOtRate": "200",
            },
        )

        db.session.add_all([factory_salary, casual_salary])
        db.session.commit()

        factory_attendance = TeamAttendanceRecord(
            team_member_id=factory_member.id,
            month="2024-05",
            entries={
                "2024-05-01": {"onTime": "07:00", "offTime": "19:00"},
                "2024-05-02": {"dayStatus": "Work Day"},
            },
        )
        casual_attendance = TeamAttendanceRecord(
            team_member_id=casual_member.id,
            month="2024-05",
            entries={
                "2024-05-01": {"onTime": "08:00", "offTime": "18:00"},
                "2024-05-02": {"dayStatus": "Work Day"},
                "2024-05-03": {"dayStatus": "No Pay Leave"},
            },
        )

        db.session.add_all([factory_attendance, casual_attendance])
        db.session.commit()

        response = self.client.get(
            "/api/reports/labor/daily-production-cost",
            headers=self._auth_headers(),
            query_string={"period": "2024-05"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["days"], 31)
        self.assertEqual(data["work_day_count"], 31)
        self.assertEqual(len(data["daily_totals"]), 31)

        members_by_reg = {member["regNumber"]: member for member in data["members"]}
        self.assertIn("E100", members_by_reg)
        self.assertIn("C200", members_by_reg)

        day1 = next(entry for entry in data["daily_totals"] if entry["day"] == 1)
        day2 = next(entry for entry in data["daily_totals"] if entry["day"] == 2)
        day3 = next(entry for entry in data["daily_totals"] if entry["day"] == 3)

        factory_id = members_by_reg["E100"]["id"]
        casual_id = members_by_reg["C200"]["id"]

        self.assertAlmostEqual(day1["member_costs"][str(factory_id)], 3395.81, places=2)
        self.assertAlmostEqual(day1["member_costs"][str(casual_id)], 3200.0, places=2)
        self.assertAlmostEqual(day1["total_cost"], 6595.81, places=2)

        self.assertAlmostEqual(day2["member_costs"][str(factory_id)], 2225.81, places=2)
        self.assertAlmostEqual(day2["member_costs"][str(casual_id)], 3000.0, places=2)
        self.assertAlmostEqual(day2["total_cost"], 5225.81, places=2)

        self.assertAlmostEqual(day3["member_costs"].get(str(factory_id), 0.0), 2225.81, places=2)
        self.assertNotIn(str(casual_id), day3["member_costs"])
        self.assertAlmostEqual(day3["total_cost"], 2225.81, places=2)

        self.assertAlmostEqual(data["monthly_total"], 76370.11, places=2)
        self.assertAlmostEqual(data["average_work_day_cost"], 2463.55, places=2)
        self.assertEqual(data["peak_day"]["day"], 1)
        self.assertAlmostEqual(data["peak_day"]["total_cost"], 6595.81, places=2)

        top_member = data["top_member"]
        self.assertIsNotNone(top_member)
        self.assertEqual(top_member["regNumber"], "E100")
        self.assertAlmostEqual(top_member["total_cost"], 70170.11, places=2)

    def test_labor_daily_production_cost_ongoing_month_uses_month_work_days(self):
        models_module = importlib.import_module("models")
        TeamMember = models_module.TeamMember
        TeamMemberStatus = models_module.TeamMemberStatus
        PayCategory = models_module.PayCategory
        TeamSalaryRecord = models_module.TeamSalaryRecord
        TeamAttendanceRecord = models_module.TeamAttendanceRecord
        TeamWorkCalendarDay = models_module.TeamWorkCalendarDay

        db = self.app_module.db

        factory_member = TeamMember(
            reg_number="E300",
            name="Factory Ongoing",
            pay_category=PayCategory.FACTORY.value,
            status=TeamMemberStatus.ACTIVE.value,
            join_date=date(2023, 1, 1),
        )

        db.session.add(factory_member)
        db.session.commit()

        salary_record = TeamSalaryRecord(
            team_member_id=factory_member.id,
            month="2024-10",
            components={
                "basicSalary": "30000",
                "attendanceAllowance": "9000",
            },
        )

        attendance_record = TeamAttendanceRecord(
            team_member_id=factory_member.id,
            month="2024-10",
            entries={},
        )

        non_work_days = [5, 6, 12, 13, 19, 20, 27]
        calendar_entries = [
            TeamWorkCalendarDay(date=date(2024, 10, day), is_work_day=False)
            for day in non_work_days
        ]

        db.session.add_all([salary_record, attendance_record, *calendar_entries])
        db.session.commit()

        class FixedDate(date):
            @classmethod
            def today(cls):
                return date(2024, 10, 10)

        with patch("routes.reports.date", FixedDate):
            response = self.client.get(
                "/api/reports/labor/daily-production-cost",
                headers=self._auth_headers(),
                query_string={"period": "2024-10"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-10")
        self.assertEqual(data["days"], 10)
        self.assertEqual(data["work_day_count"], 8)

        members_by_reg = {member["regNumber"]: member for member in data["members"]}
        self.assertIn("E300", members_by_reg)
        member_id = members_by_reg["E300"]["id"]

        day1 = next(entry for entry in data["daily_totals"] if entry["day"] == 1)
        expected_daily = Decimal("39000") / Decimal(24)
        self.assertAlmostEqual(
            day1["member_costs"][str(member_id)],
            float(expected_daily.quantize(Decimal("0.01"))),
            places=2,
        )

        day5 = next(entry for entry in data["daily_totals"] if entry["day"] == 5)
        self.assertNotIn(str(member_id), day5["member_costs"])
        self.assertEqual(day5["total_cost"], 0.0)

        expected_monthly_total = float(
            (expected_daily * Decimal(8)).quantize(Decimal("0.01"))
        )
        self.assertAlmostEqual(data["monthly_total"], expected_monthly_total, places=2)
