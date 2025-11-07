import importlib
import os
import sys
import unittest


class AdminSeedTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        for var in (
            "RUN_SEED_ADMIN",
            "ADMIN_EMAIL",
            "ADMIN_PASSWORD",
            "ADMIN_NAME",
            "RUN_SEED_RAINBOWS_ADMIN",
            "RAINBOWS_ADMIN_EMAIL",
            "RAINBOWS_ADMIN_PASSWORD",
            "RAINBOWS_ADMIN_NAME",
            "RUN_SEED_FINANCE",
            "FINANCE_EMAIL",
            "FINANCE_PASSWORD",
            "FINANCE_NAME",
        ):
            os.environ.pop(var, None)

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

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        for var in (
            "DATABASE_URL",
            "RUN_SEED_ADMIN",
            "ADMIN_EMAIL",
            "ADMIN_PASSWORD",
            "ADMIN_NAME",
            "RUN_SEED_RAINBOWS_ADMIN",
            "RAINBOWS_ADMIN_EMAIL",
            "RAINBOWS_ADMIN_PASSWORD",
            "RAINBOWS_ADMIN_NAME",
            "RUN_SEED_FINANCE",
            "FINANCE_EMAIL",
            "FINANCE_PASSWORD",
            "FINANCE_NAME",
        ):
            os.environ.pop(var, None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def test_default_admin_created_and_login_succeeds(self):
        status, email = self.app_module._ensure_admin_user(flask_app=self.app)
        self.assertEqual(status, "created")
        self.assertEqual(email, "admin@samprox.lk")

        admin = self.app_module.User.query.filter_by(role=self.app_module.RoleEnum.admin).one()
        self.assertTrue(admin.check_password("Admin@123"))

        client = self.app.test_client()
        response = client.post(
            "/api/auth/login",
            json={"email": email, "password": "Admin@123"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["role"], "admin")

    def test_force_reset_updates_password(self):
        status, email = self.app_module._ensure_admin_user(flask_app=self.app)
        self.assertEqual(status, "created")
        self.assertEqual(email, "admin@samprox.lk")

        admin = self.app_module.User.query.filter_by(email=email).one()
        admin.set_password("OldPassword!1")
        self.app_module.db.session.commit()

        status, _ = self.app_module._ensure_admin_user(
            flask_app=self.app,
            password="NewPassword!2",
            force_reset=True,
        )
        self.assertEqual(status, "reset")

        refreshed = self.app_module.db.session.get(self.app_module.User, admin.id)
        self.assertTrue(refreshed.check_password("NewPassword!2"))

    def test_allow_multiple_admins_created_with_custom_name(self):
        status, email = self.app_module._ensure_admin_user(flask_app=self.app)
        self.assertEqual(status, "created")
        self.assertEqual(email, "admin@samprox.lk")

        status, second_email = self.app_module._ensure_admin_user(
            flask_app=self.app,
            email="uresha@rainbowsholdings.com",
            password="123",
            name="Uresha",
            allow_multiple=True,
        )

        self.assertEqual(status, "created")
        self.assertEqual(second_email, "uresha@rainbowsholdings.com")

        admins = (
            self.app_module.User.query.filter_by(role=self.app_module.RoleEnum.admin)
            .order_by(self.app_module.User.email)
            .all()
        )
        self.assertEqual(len(admins), 2)
        additional = next(user for user in admins if user.email == second_email)
        self.assertTrue(additional.check_password("123"))
        self.assertEqual(additional.name, "Uresha")

    def test_finance_user_created_with_defaults(self):
        status, email = self.app_module._ensure_finance_user(flask_app=self.app)
        self.assertEqual(status, "created")
        self.assertEqual(email, "finance@samprox.lk")

        finance_user = self.app_module.User.query.filter_by(email=email).one()
        self.assertEqual(finance_user.role, self.app_module.RoleEnum.finance_manager)
        self.assertTrue(finance_user.check_password("123"))
        self.assertTrue(finance_user.active)


if __name__ == "__main__":
    unittest.main()
