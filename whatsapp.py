"""WhatsApp Cloud API integration helpers."""
from __future__ import annotations

import json as _json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict

try:  # pragma: no cover - exercised indirectly via tests
    import requests  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - fallback for offline test environments
    from urllib import error as _urllib_error, request as _urllib_request

    class _FallbackResponse:
        def __init__(self, status_code: int, body: bytes):
            self.status_code = status_code
            self._body = body

        @property
        def text(self) -> str:
            return self._body.decode("utf-8", "replace")

        def json(self) -> Dict[str, Any]:
            if not self._body:
                return {}
            return _json.loads(self.text)

    class _FallbackRequests:
        @staticmethod
        def post(url: str, headers: Dict[str, str] | None = None, json: Dict[str, Any] | None = None, timeout: float | None = None):
            headers = headers or {}
            data = None
            if json is not None:
                data = _json.dumps(json).encode("utf-8")
                headers = {**headers, "Content-Type": "application/json"}
            request = _urllib_request.Request(url, data=data, headers=headers, method="POST")
            try:
                with _urllib_request.urlopen(request, timeout=timeout) as response:
                    body = response.read()
                    return _FallbackResponse(response.getcode(), body)
            except _urllib_error.HTTPError as exc:
                body = exc.read()
                return _FallbackResponse(exc.code, body)

    requests = _FallbackRequests()  # type: ignore[assignment]

WA_BASE = "https://graph.facebook.com/v20.0"


class WhatsAppError(RuntimeError):
    """Error raised when the WhatsApp Cloud API request fails."""


@dataclass(frozen=True)
class _WAConfig:
    phone_number_id: str
    access_token: str


def _current_config() -> _WAConfig:
    phone_number_id = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
    access_token = os.getenv("WA_ACCESS_TOKEN", "").strip()
    if not phone_number_id or not access_token:
        missing = []
        if not phone_number_id:
            missing.append("WA_PHONE_NUMBER_ID")
        if not access_token:
            missing.append("WA_ACCESS_TOKEN")
        raise WhatsAppError(
            f"Missing WhatsApp credentials: {', '.join(missing)}"
        )
    return _WAConfig(phone_number_id=phone_number_id, access_token=access_token)


def _to_e164_lk(phone: str) -> str:
    """Normalise Sri Lankan phone numbers into WhatsApp E.164 format."""

    if phone is None:
        raise ValueError("Phone number is required")

    digits = re.sub(r"\D+", "", phone)
    if not digits:
        raise ValueError("Phone number contains no digits")

    if digits.startswith("0") and len(digits) == 10:
        digits = "94" + digits[1:]
    elif digits.startswith("0094"):
        digits = "94" + digits[4:]

    if digits.startswith("94") and len(digits) == 11:
        return digits

    raise ValueError("Phone number is not a valid Sri Lankan MSISDN")


def wa_send_text(to_phone: str, body: str) -> Dict[str, Any]:
    """Send a plain text WhatsApp message to a phone number.

    In Meta's Developer Mode you can only send messages to test numbers that were
    added via the API Setup page. Production apps with approved phone numbers
    and templates are required for business-initiated notifications.
    """

    config = _current_config()

    url = f"{WA_BASE}/{config.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": body},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=20)
    if response.status_code >= 400:
        raise WhatsAppError(
            f"{response.status_code}: {response.text}"
        )

    return response.json()


def wa_send_text_lk(any_format_phone: str, body: str) -> Dict[str, Any]:
    """Convenience helper that accepts a Sri Lankan phone number in any format."""

    normalized = _to_e164_lk(any_format_phone)
    return wa_send_text(normalized, body)


__all__ = [
    "WA_BASE",
    "WhatsAppError",
    "_to_e164_lk",
    "wa_send_text",
    "wa_send_text_lk",
]
