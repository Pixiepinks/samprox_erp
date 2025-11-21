import importlib
import os
import sys
import unittest


class MaterialDefaultsSeedTestCase(unittest.TestCase):
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

        from material import seed_material_defaults

        self.seed_material_defaults = seed_material_defaults
        self.MaterialItem = self.app_module.MaterialItem
        self.db = self.app_module.db

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def test_seed_reactivates_default_items(self):
        inactive_item = self.MaterialItem(name="Briquettes", is_active=False)
        self.db.session.add(inactive_item)
        self.db.session.commit()

        self.seed_material_defaults()

        refreshed = self.MaterialItem.query.filter_by(name="Briquettes").all()
        self.assertEqual(len(refreshed), 1)
        self.assertTrue(refreshed[0].is_active)


if __name__ == "__main__":
    unittest.main()
