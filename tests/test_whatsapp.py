import os
import unittest
from unittest.mock import MagicMock, patch

import whatsapp
from whatsapp import WhatsAppError, _to_e164_lk, wa_send_text


class WhatsAppHelpersTestCase(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            "WA_PHONE_NUMBER_ID": os.environ.get("WA_PHONE_NUMBER_ID"),
            "WA_ACCESS_TOKEN": os.environ.get("WA_ACCESS_TOKEN"),
        }
        os.environ["WA_PHONE_NUMBER_ID"] = "123456789012345"
        os.environ["WA_ACCESS_TOKEN"] = "test-token"

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_to_e164_lk_examples(self):
        self.assertEqual(_to_e164_lk("0712345678"), "94712345678")
        self.assertEqual(_to_e164_lk("+94 71 234 5678"), "94712345678")
        self.assertEqual(_to_e164_lk("94712345678"), "94712345678")

    @patch("whatsapp.requests.post")
    def test_wa_send_text_payload(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"messages": [{"id": "wamid.sample"}]}
        mock_post.return_value = mock_response

        result = wa_send_text("94712345678", "Hello there!")

        self.assertEqual(result, {"messages": [{"id": "wamid.sample"}]})
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f"{whatsapp.WA_BASE}/123456789012345/messages")
        self.assertEqual(kwargs["headers"], {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        })
        self.assertEqual(kwargs["json"], {
            "messaging_product": "whatsapp",
            "to": "94712345678",
            "type": "text",
            "text": {"body": "Hello there!"},
        })
        self.assertEqual(kwargs["timeout"], 20)

    @patch("whatsapp.requests.post")
    def test_wa_send_text_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Error"
        mock_post.return_value = mock_response

        with self.assertRaises(WhatsAppError) as ctx:
            wa_send_text("94712345678", "Hello")

        self.assertIn("500", str(ctx.exception))
        self.assertIn("Internal Error", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
