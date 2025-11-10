import importlib
import os
import sys
import unittest


class ProductionApiTestCase(unittest.TestCase):
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

        self.pm_user = User(
            name="Prod Manager",
            email="pm@example.com",
            role=RoleEnum.production_manager,
        )
        self.pm_user.set_password("Password!1")
        self.mm_user = User(
            name="Maint Manager",
            email="mm@example.com",
            role=RoleEnum.maintenance_manager,
        )
        self.mm_user.set_password("Password!1")
        self.app_module.db.session.add_all([self.pm_user, self.mm_user])
        self.app_module.db.session.commit()

        self.pm_token = self._login("pm@example.com")
        self.mm_token = self._login("mm@example.com")

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def _login(self, email):
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "Password!1"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _create_machine(self):
        payload = {
            "name": "Milling Machine",
            "category": "Plant & Machines",
            "location": "Plant A",
            "status": "Operational",
        }
        response = self.client.post(
            "/api/machines/assets",
            headers=self._auth_headers(self.pm_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_create_update_and_list_daily_production(self):
        asset = self._create_machine()

        create_payload = {
            "machine_code": asset["code"],
            "date": "2024-05-10",
            "hour_no": 1,
            "quantity_tons": 5.25,
        }
        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            json=create_payload,
        )
        self.assertEqual(response.status_code, 201)
        created_entry = response.get_json()
        self.assertEqual(created_entry["machine_code"], asset["code"])
        self.assertEqual(created_entry["hour_no"], 1)
        self.assertAlmostEqual(created_entry["quantity_tons"], 5.25)

        update_payload = {
            **create_payload,
            "quantity_tons": 6.5,
        }
        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        updated_entry = response.get_json()
        self.assertAlmostEqual(updated_entry["quantity_tons"], 6.5)

        response = self.client.get(
            "/api/production/daily",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "machine_code": asset["code"],
                "date": "2024-05-10",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["machine"]["code"], asset["code"])
        self.assertEqual(len(data["entries"]), 24)
        hour_one = next(entry for entry in data["entries"] if entry["hour_no"] == 1)
        self.assertAlmostEqual(hour_one["quantity_tons"], 6.5)
        total = data["total_quantity_tons"]
        self.assertAlmostEqual(total, 6.5)

    def test_only_production_manager_can_record_output(self):
        asset = self._create_machine()

        response = self.client.post(
            "/api/production/daily",
            headers=self._auth_headers(self.mm_token),
            json={
                "machine_code": asset["code"],
                "date": "2024-05-11",
                "hour_no": 2,
                "quantity_tons": 3,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission", response.get_json()["msg"].lower())

    def test_daily_summary_returns_machine_and_hour_totals(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()

        def save_output(machine_code, hour_no, quantity, date="2024-05-12"):
            response = self.client.post(
                "/api/production/daily",
                headers=self._auth_headers(self.pm_token),
                json={
                    "machine_code": machine_code,
                    "date": date,
                    "hour_no": hour_no,
                    "quantity_tons": quantity,
                },
            )
            self.assertIn(response.status_code, (200, 201))

        save_output(first_asset["code"], 1, 3.5)
        save_output(second_asset["code"], 1, 4.0)
        save_output(first_asset["code"], 2, 1.0)
        save_output(first_asset["code"], 1, 2.5, date="2024-05-01")

        response = self.client.get(
            "/api/production/daily/summary",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "date": "2024-05-12",
                "machine_codes": f"{first_asset['code']},{second_asset['code']}",
            },
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(len(data["hours"]), 24)
        self.assertAlmostEqual(data["total_quantity_tons"], 8.5)

        machines_meta = {machine["code"]: machine for machine in data["machines"]}
        self.assertIn(first_asset["code"], machines_meta)
        self.assertIn(second_asset["code"], machines_meta)

        hour_one = next(hour for hour in data["hours"] if hour["hour_no"] == 1)
        self.assertAlmostEqual(
            hour_one["machines"][first_asset["code"]]["quantity_tons"],
            3.5,
        )
        self.assertAlmostEqual(
            hour_one["machines"][second_asset["code"]]["quantity_tons"],
            4.0,
        )
        self.assertAlmostEqual(hour_one["hour_total_tons"], 7.5)

        hour_two = next(hour for hour in data["hours"] if hour["hour_no"] == 2)
        self.assertAlmostEqual(
            hour_two["machines"][first_asset["code"]]["quantity_tons"],
            1.0,
        )
        self.assertAlmostEqual(
            hour_two["machines"][second_asset["code"]]["quantity_tons"],
            0.0,
        )

    def test_daily_summary_excludes_mch_0003_from_totals(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()
        third_asset = self._create_machine()

        def save_output(machine_code, hour_no, quantity, date="2024-05-15"):
            response = self.client.post(
                "/api/production/daily",
                headers=self._auth_headers(self.pm_token),
                json={
                    "machine_code": machine_code,
                    "date": date,
                    "hour_no": hour_no,
                    "quantity_tons": quantity,
                },
            )
            self.assertIn(response.status_code, (200, 201))

        save_output(first_asset["code"], 3, 4.0)
        save_output(second_asset["code"], 3, 2.5)
        save_output(third_asset["code"], 3, 6.0)

        response = self.client.get(
            "/api/production/daily/summary",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "date": "2024-05-15",
                "machine_codes": ",".join(
                    [first_asset["code"], second_asset["code"], third_asset["code"]]
                ),
            },
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        hour_three = next(hour for hour in data["hours"] if hour["hour_no"] == 3)

        self.assertAlmostEqual(
            hour_three["machines"][first_asset["code"]]["quantity_tons"],
            4.0,
        )
        self.assertAlmostEqual(
            hour_three["machines"][second_asset["code"]]["quantity_tons"],
            2.5,
        )
        self.assertAlmostEqual(
            hour_three["machines"][third_asset["code"]]["quantity_tons"],
            6.0,
        )

        # Hour totals should only include MCH-0001 and MCH-0002 quantities.
        self.assertAlmostEqual(hour_three["hour_total_tons"], 6.5)

        today_totals = data["totals"]["today"]
        self.assertAlmostEqual(today_totals["machines"][first_asset["code"]], 4.0)
        self.assertAlmostEqual(today_totals["machines"][second_asset["code"]], 2.5)
        self.assertAlmostEqual(today_totals["machines"][third_asset["code"]], 6.0)

        # Daily total should exclude the MCH-0003 contribution.
        self.assertAlmostEqual(today_totals["total"], 6.5)

        mtd_totals = data["totals"]["mtd"]
        self.assertAlmostEqual(mtd_totals["machines"][third_asset["code"]], 6.0)
        self.assertAlmostEqual(mtd_totals["total"], 6.5)

    def test_create_update_and_fetch_forecast(self):
        asset = self._create_machine()

        create_payload = {
            "machine_code": asset["code"],
            "date": "2024-05-10",
            "forecast_hours": 10.0,
            "average_hourly_production": 1.85,
        }

        response = self.client.post(
            "/api/production/forecast",
            headers=self._auth_headers(self.pm_token),
            json=create_payload,
        )
        self.assertEqual(response.status_code, 201)
        created = response.get_json()
        self.assertAlmostEqual(created["forecast_tons"], 18.5)
        self.assertAlmostEqual(created["forecast_hours"], 10.0)
        self.assertAlmostEqual(created["average_hourly_production"], 1.85)

        update_payload = {
            **create_payload,
            "forecast_hours": 9.1,
            "average_hourly_production": 2.5,
        }
        response = self.client.post(
            "/api/production/forecast",
            headers=self._auth_headers(self.pm_token),
            json=update_payload,
        )
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()
        self.assertAlmostEqual(updated["forecast_tons"], 22.75)
        self.assertAlmostEqual(updated["forecast_hours"], 9.1)
        self.assertAlmostEqual(updated["average_hourly_production"], 2.5)

        response = self.client.get(
            "/api/production/forecast",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "machine_code": asset["code"],
                "period": "2024-05",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["machine"]["code"], asset["code"])
        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(len(data["entries"]), 31)
        day_entry = next(item for item in data["entries"] if item["date"] == "2024-05-10")
        self.assertAlmostEqual(day_entry["forecast_tons"], 22.75)
        self.assertAlmostEqual(day_entry["forecast_hours"], 9.1)
        self.assertAlmostEqual(day_entry["average_hourly_production"], 2.5)
        self.assertAlmostEqual(data["total_forecast_tons"], 22.75)

    def test_forecast_accepts_legacy_payload(self):
        asset = self._create_machine()

        payload = {
            "machine_code": asset["code"],
            "date": "2024-07-01",
            "forecast_tons": 12.5,
        }

        response = self.client.post(
            "/api/production/forecast",
            headers=self._auth_headers(self.pm_token),
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        created = response.get_json()
        self.assertAlmostEqual(created["forecast_tons"], 12.5)
        self.assertAlmostEqual(created["forecast_hours"], 0.0)
        self.assertAlmostEqual(created["average_hourly_production"], 0.0)

    def test_only_manager_can_save_forecast(self):
        asset = self._create_machine()

        response = self.client.post(
            "/api/production/forecast",
            headers=self._auth_headers(self.mm_token),
            json={
                "machine_code": asset["code"],
                "date": "2024-06-01",
                "forecast_tons": 5,
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_holidays_endpoint_returns_sri_lanka_dates(self):
        response = self.client.get(
            "/api/production/forecast/holidays",
            headers=self._auth_headers(self.pm_token),
            query_string={"year": 2024},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["year"], 2024)
        holidays = {item["date"]: item["name"] for item in data["holidays"]}
        self.assertIn("2024-02-04", holidays)


    def test_monthly_summary_returns_daily_totals(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()

        def record_output(machine_code, date, hour_no, quantity):
            response = self.client.post(
                "/api/production/daily",
                headers=self._auth_headers(self.pm_token),
                json={
                    "machine_code": machine_code,
                    "date": date,
                    "hour_no": hour_no,
                    "quantity_tons": quantity,
                },
            )
            self.assertIn(response.status_code, (200, 201))

        record_output(first_asset["code"], "2024-05-01", 1, 3.5)
        record_output(first_asset["code"], "2024-05-01", 2, 1.5)
        record_output(second_asset["code"], "2024-05-01", 1, 2.0)
        record_output(first_asset["code"], "2024-05-02", 1, 5.0)
        record_output(second_asset["code"], "2024-05-15", 5, 1.25)
        record_output(first_asset["code"], "2024-04-30", 1, 9.0)

        response = self.client.get(
            "/api/production/monthly/summary",
            headers=self._auth_headers(self.pm_token),
            query_string={"period": "2024-05"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["days"], 31)
        self.assertEqual(len(data["daily_totals"]), data["days"])

        totals_by_day = {item["day"]: item for item in data["daily_totals"]}
        may_first = totals_by_day[1]
        self.assertAlmostEqual(may_first["MCH1"], 5.0)
        self.assertAlmostEqual(may_first["MCH2"], 2.0)
        self.assertAlmostEqual(may_first["total_tons"], 7.0)

        may_second = totals_by_day[2]
        self.assertAlmostEqual(may_second["MCH1"], 5.0)
        self.assertAlmostEqual(may_second["MCH2"], 0.0)
        self.assertAlmostEqual(may_second["total_tons"], 5.0)

        may_fifteenth = totals_by_day[15]
        self.assertAlmostEqual(may_fifteenth["MCH1"], 0.0)
        self.assertAlmostEqual(may_fifteenth["MCH2"], 1.25)
        self.assertAlmostEqual(may_fifteenth["total_tons"], 1.25)

        self.assertAlmostEqual(totals_by_day.get(3, {"total_tons": 0.0})["total_tons"], 0.0)

        self.assertAlmostEqual(data["total_production"], 13.25)
        self.assertAlmostEqual(data["average_day_production"], round(13.25 / 31, 3))
        self.assertEqual(data["peak"]["day"], 1)
        self.assertAlmostEqual(data["peak"]["total_tons"], 7.0)
        self.assertIn("MCH-0001", data["machine_codes"])
        self.assertIn("MCH-0002", data["machine_codes"])


    def test_hourly_pulse_returns_hourly_series(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()
        third_asset = self._create_machine()

        def record_output(machine_code, date, hour_no, quantity):
            response = self.client.post(
                "/api/production/daily",
                headers=self._auth_headers(self.pm_token),
                json={
                    "machine_code": machine_code,
                    "date": date,
                    "hour_no": hour_no,
                    "quantity_tons": quantity,
                },
            )
            self.assertIn(response.status_code, (200, 201))

        record_output(first_asset["code"], "2024-05-01", 1, 1.5)
        record_output(second_asset["code"], "2024-05-01", 1, 2.5)
        record_output(third_asset["code"], "2024-05-01", 24, 3.0)
        record_output(first_asset["code"], "2024-05-15", 12, 4.2)
        record_output(second_asset["code"], "2024-05-31", 6, 1.0)
        record_output(first_asset["code"], "2024-04-30", 3, 9.0)

        response = self.client.get(
            "/api/production/monthly/hourly-pulse",
            headers=self._auth_headers(self.pm_token),
            query_string={"period": "2024-05"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(len(data["hourly_totals"]), data["hours"])
        self.assertEqual(data["hours"], data["days"] * 24)

        entries = {(item["day"], item["hour"]): item for item in data["hourly_totals"]}

        may_first_hour_one = entries[(1, 1)]
        self.assertAlmostEqual(may_first_hour_one["MCH1"], 1.5)
        self.assertAlmostEqual(may_first_hour_one["MCH2"], 2.5)
        self.assertAlmostEqual(may_first_hour_one["MCH3"], 0.0)

        may_first_hour_twenty_four = entries[(1, 24)]
        self.assertAlmostEqual(may_first_hour_twenty_four["MCH3"], 3.0)

        may_fifteenth_hour_twelve = entries[(15, 12)]
        self.assertAlmostEqual(may_fifteenth_hour_twelve["MCH1"], 4.2)

        may_thirty_first_hour_six = entries[(31, 6)]
        self.assertAlmostEqual(may_thirty_first_hour_six["MCH2"], 1.0)

        self.assertAlmostEqual(data["total_production"], 12.2)
        total_effective_hours = sum(data["effective_hours"].values())
        self.assertAlmostEqual(total_effective_hours, 5)
        expected_average = round(12.2 / total_effective_hours, 3)
        self.assertAlmostEqual(data["average_hour_production"], expected_average)
        self.assertAlmostEqual(data["total_effective_hours"], total_effective_hours)
        self.assertEqual(data["peak"]["day"], 15)
        self.assertEqual(data["peak"]["hour"], 12)
        self.assertAlmostEqual(data["peak"]["total_tons"], 4.2)
        self.assertIn(first_asset["code"], data["machine_codes"])
        self.assertIn(second_asset["code"], data["machine_codes"])
        self.assertIn(third_asset["code"], data["machine_codes"])


    def test_hourly_pulse_accepts_custom_date_range(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()

        def record_output(machine_code, date, hour_no, quantity):
            response = self.client.post(
                "/api/production/daily",
                headers=self._auth_headers(self.pm_token),
                json={
                    "machine_code": machine_code,
                    "date": date,
                    "hour_no": hour_no,
                    "quantity_tons": quantity,
                },
            )
            self.assertIn(response.status_code, (200, 201))

        record_output(first_asset["code"], "2024-05-01", 1, 1.0)
        record_output(first_asset["code"], "2024-05-02", 5, 2.0)
        record_output(second_asset["code"], "2024-05-02", 5, 3.5)
        record_output(first_asset["code"], "2024-05-03", 6, 4.0)

        response = self.client.get(
            "/api/production/monthly/hourly-pulse",
            headers=self._auth_headers(self.pm_token),
            query_string={"start_date": "2024-05-01", "end_date": "2024-05-02"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["start_date"], "2024-05-01")
        self.assertEqual(data["end_date"], "2024-05-02")
        self.assertEqual(data["days"], 2)
        self.assertEqual(data["hours"], 48)
        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["label"], "May 01, 2024 – May 02, 2024")

        dates = {item["date"] for item in data["hourly_totals"]}
        self.assertIn("2024-05-01", dates)
        self.assertIn("2024-05-02", dates)
        self.assertNotIn("2024-05-03", dates)

        entries = {(item["date"], item["hour"]): item for item in data["hourly_totals"]}
        may_first_hour_one = entries[("2024-05-01", 1)]
        self.assertAlmostEqual(may_first_hour_one["MCH1"], 1.0)
        self.assertAlmostEqual(may_first_hour_one["total_tons"], 1.0)

        may_second_hour_five = entries[("2024-05-02", 5)]
        self.assertAlmostEqual(may_second_hour_five["MCH1"], 2.0)
        self.assertAlmostEqual(may_second_hour_five["MCH2"], 3.5)
        self.assertAlmostEqual(may_second_hour_five["total_tons"], 5.5)

        self.assertAlmostEqual(data["total_production"], 6.5)
        total_effective_hours = sum(data["effective_hours"].values())
        self.assertEqual(total_effective_hours, data["total_effective_hours"])
        self.assertAlmostEqual(data["average_hour_production"], round(6.5 / total_effective_hours, 3))
        self.assertEqual(data["peak"]["date"], "2024-05-02")
        self.assertEqual(data["peak"]["hour"], 5)
        self.assertAlmostEqual(data["peak"]["total_tons"], 5.5)

        invalid_missing = self.client.get(
            "/api/production/monthly/hourly-pulse",
            headers=self._auth_headers(self.pm_token),
            query_string={"start_date": "2024-05-01"},
        )
        self.assertEqual(invalid_missing.status_code, 400)
        self.assertEqual(invalid_missing.get_json()["msg"], "Both start_date and end_date are required.")

        invalid_order = self.client.get(
            "/api/production/monthly/hourly-pulse",
            headers=self._auth_headers(self.pm_token),
            query_string={"start_date": "2024-05-03", "end_date": "2024-05-01"},
        )
        self.assertEqual(invalid_order.status_code, 400)
        self.assertEqual(
            invalid_order.get_json()["msg"], "end_date must be on or after start_date."
        )


    def test_monthly_idle_summary_accounts_for_shift_hours(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()
        third_asset = self._create_machine()

        def log_idle(asset_id, start, end):
            payload = {
                "asset_id": asset_id,
                "started_at": start,
                "ended_at": end,
            }
            response = self.client.post(
                "/api/machines/idle-events",
                headers=self._auth_headers(self.pm_token),
                json=payload,
            )
            self.assertEqual(response.status_code, 201)

        log_idle(first_asset["id"], "2024-05-01T08:00:00", "2024-05-01T09:30:00")
        log_idle(first_asset["id"], "2024-05-01T17:00:00", "2024-05-01T20:00:00")
        log_idle(first_asset["id"], "2024-05-02T18:00:00", "2024-05-03T08:00:00")

        log_idle(second_asset["id"], "2024-04-30T22:00:00", "2024-05-01T08:00:00")
        log_idle(second_asset["id"], "2024-05-20T06:00:00", "2024-05-20T07:30:00")

        response = self.client.get(
            "/api/production/monthly/idle-summary",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "period": "2024-05",
                "machine_codes": ",".join(
                    [
                        first_asset["code"],
                        second_asset["code"],
                        third_asset["code"],
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(data["days"], 31)
        self.assertAlmostEqual(data["scheduled_hours_per_day"], 11.0)

        day_entries = data["day_entries"]
        self.assertEqual(len(day_entries), 31)
        days_by_index = {item["day"]: item for item in day_entries}

        may_first = days_by_index[1]
        self.assertAlmostEqual(
            may_first["machines"][first_asset["code"]]["idle_hours"],
            3.5,
        )
        self.assertAlmostEqual(
            may_first["machines"][first_asset["code"]]["runtime_hours"],
            7.5,
        )
        self.assertAlmostEqual(
            may_first["machines"][second_asset["code"]]["idle_hours"],
            1.0,
        )

        may_second = days_by_index[2]
        self.assertAlmostEqual(
            may_second["machines"][first_asset["code"]]["idle_hours"],
            1.0,
        )
        self.assertAlmostEqual(
            may_second["machines"][first_asset["code"]]["runtime_hours"],
            10.0,
        )

        may_third = days_by_index[3]
        self.assertAlmostEqual(
            may_third["machines"][first_asset["code"]]["idle_hours"],
            1.0,
        )

        may_twentieth = days_by_index[20]
        self.assertAlmostEqual(
            may_twentieth["machines"][second_asset["code"]]["idle_hours"],
            0.5,
        )

        totals = data["totals"]
        self.assertAlmostEqual(totals[first_asset["code"]]["idle_hours"], 5.5)
        self.assertAlmostEqual(totals[second_asset["code"]]["idle_hours"], 1.5)
        self.assertAlmostEqual(totals[third_asset["code"]]["idle_hours"], 0.0)

        self.assertAlmostEqual(
            totals[first_asset["code"]]["runtime_hours"],
            31 * 11 - 5.5,
        )

    def test_monthly_idle_secondary_pareto_stacks_minutes(self):
        first_asset = self._create_machine()
        second_asset = self._create_machine()
        third_asset = self._create_machine()

        def log_idle(asset_id, start, end, reason="Breakdown", secondary=None):
            payload = {
                "asset_id": asset_id,
                "started_at": start,
                "ended_at": end,
                "reason": reason,
            }
            if secondary is not None:
                payload["secondary_reason"] = secondary

            response = self.client.post(
                "/api/machines/idle-events",
                headers=self._auth_headers(self.pm_token),
                json=payload,
            )
            self.assertEqual(response.status_code, 201)

        log_idle(
            first_asset["id"],
            "2024-05-01T07:30:00",
            "2024-05-01T09:30:00",
            secondary="Power trip",
        )
        log_idle(
            second_asset["id"],
            "2024-05-03T08:00:00",
            "2024-05-03T09:00:00",
            secondary="Power trip",
        )
        log_idle(
            first_asset["id"],
            "2024-05-05T10:00:00",
            "2024-05-05T12:00:00",
            secondary="Material delay",
        )
        log_idle(
            third_asset["id"],
            "2024-05-10T11:00:00",
            "2024-05-10T12:00:00",
            reason="Maintenance",
            secondary=None,
        )

        machine_codes = ",".join(
            [first_asset["code"], second_asset["code"], third_asset["code"]]
        )

        response = self.client.get(
            "/api/production/monthly/idle-secondary-pareto",
            headers=self._auth_headers(self.pm_token),
            query_string={"period": "2024-05", "machine_codes": machine_codes},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["period"], "2024-05")
        self.assertEqual(
            data["machine_codes"],
            [first_asset["code"], second_asset["code"], third_asset["code"]],
        )

        reasons = data["reasons"]
        self.assertGreaterEqual(len(reasons), 2)

        power_trip = reasons[0]
        self.assertEqual(power_trip["label"], "Power trip")
        self.assertAlmostEqual(power_trip["total_idle_hours"], 3.0)
        self.assertAlmostEqual(power_trip["machines"][first_asset["code"]], 2.0)
        self.assertAlmostEqual(power_trip["machines"][second_asset["code"]], 1.0)

        material_delay = next(
            (item for item in reasons if item["label"] == "Material delay"),
            None,
        )
        self.assertIsNotNone(material_delay)
        self.assertAlmostEqual(material_delay["total_idle_hours"], 2.0)
        self.assertAlmostEqual(material_delay["machines"][first_asset["code"]], 2.0)

        unspecified = next(
            (
                item
                for item in reasons
                if item["label"] == "Maintenance — unspecified secondary"
            ),
            None,
        )
        self.assertIsNotNone(unspecified)
        self.assertAlmostEqual(
            unspecified["machines"][third_asset["code"]],
            1.0,
        )

        self.assertAlmostEqual(data["total_idle_hours"], 6.0)

    def test_idle_summary_uses_forecast_hours_when_no_events(self):
        asset = self._create_machine()

        forecast_payload = {
            "machine_code": asset["code"],
            "date": "2025-11-01",
            "forecast_hours": 10,
            "average_hourly_production": 1.5,
        }

        response = self.client.post(
            "/api/production/forecast",
            headers=self._auth_headers(self.pm_token),
            json=forecast_payload,
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.get(
            "/api/production/monthly/idle-summary",
            headers=self._auth_headers(self.pm_token),
            query_string={
                "period": "2025-11",
                "machine_codes": asset["code"],
            },
        )
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data["period"], "2025-11")

        entries_by_day = {item["day"]: item for item in data["day_entries"]}
        november_first = entries_by_day[1]
        machine_entry = november_first["machines"][asset["code"]]

        self.assertAlmostEqual(machine_entry["idle_hours"], 1.0)
        self.assertAlmostEqual(machine_entry["runtime_hours"], 10.0)

        totals = data["totals"][asset["code"]]
        self.assertAlmostEqual(totals["idle_hours"], 1.0)
        scheduled_hours = data["scheduled_hours_per_day"]
        expected_runtime_total = data["days"] * scheduled_hours - totals["idle_hours"]
        self.assertAlmostEqual(totals["runtime_hours"], expected_runtime_total)


    def test_monthly_summary_validates_period(self):
        response = self.client.get(
            "/api/production/monthly/summary",
            headers=self._auth_headers(self.pm_token),
            query_string={"period": "2024-13"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
