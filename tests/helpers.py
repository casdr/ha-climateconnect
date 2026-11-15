"""Helpers for the ClimateConnect tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"

EMAIL = "user@example.com"
PASSWORD = "correct horse battery staple"
SERIAL = "GRB000000001"
STATE = "00000000-0000-4000-8000-00000000000a"
REFRESH_TOKEN = "test-refresh-token"


def load_json(name: str) -> Any:
    """Return a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    """Return a text fixture."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def _b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(
    *,
    email: str = EMAIL,
    state: str = STATE,
    refresh_token: str = REFRESH_TOKEN,
    expires_at: str = "2027-11-15T16:45:24.000+01:00",
) -> str:
    """Return an unsigned JWT shaped like the app's token."""
    payload = {
        "redirect_url": "climateconnect://login",
        "session": {
            "state": state,
            "application_id": "2da3d256-ca0a-4041-84ba-93856efceef9",
            "issued_at": "2026-11-15T16:45:24.000+01:00",
            "expires_at": expires_at,
            "refresh_token": refresh_token,
        },
        "user": {"name": "Test User", "mailaddress": email, "type": "Bewoner"},
        "consumer": {"name": "Test User", "mailaddress": email},
    }
    return f"{_b64({'typ': 'JWT', 'alg': 'HS256'})}.{_b64(payload)}.signature"
