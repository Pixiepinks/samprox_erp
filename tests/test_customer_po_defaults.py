import importlib
import os
import sys
import unittest


class CustomerPODefaultItemsTestCase(unittest.TestCase):
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

        self.client = self.app.test_client()
        self.MaterialItem = self.app_module.MaterialItem

    def tearDown(self):
        self.app_module.db.session.remove()
        self.app_module.db.drop_all()
        self.ctx.pop()
        os.environ.pop("DATABASE_URL", None)
        if "app" in sys.modules:
            del sys.modules["app"]

    def test_new_po_page_seeds_default_items(self):
        response = self.client.get("/customer-pos/new")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn(b"Briquettes", response.data)

        briquettes = self.MaterialItem.query.filter_by(name="Briquettes").all()
        self.assertEqual(len(briquettes), 1)
        self.assertTrue(briquettes[0].is_active)


if __name__ == "__main__":
    unittest.main()
