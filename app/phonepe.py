"""PhonePe Standard Checkout v2 (OAuth) client for the booking flow."""
import hashlib
import json
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from .config import (
    PHONEPE_AUTH_BASE_URL,
    PHONEPE_BASE_URL,
    PHONEPE_CLIENT_ID,
    PHONEPE_CLIENT_SECRET,
    PHONEPE_CLIENT_VERSION,
    PHONEPE_REDIRECT_URL,
    PHONEPE_WEBHOOK_PASSWORD,
    PHONEPE_WEBHOOK_USERNAME,
)

_token_cache: Dict[str, Any] = {"access_token": None, "expires_at": 0}


def is_configured() -> bool:
    return bool(PHONEPE_CLIENT_ID and PHONEPE_CLIENT_SECRET and PHONEPE_REDIRECT_URL)


def _get_access_token() -> str:
    if _token_cache["access_token"] and _token_cache["expires_at"] > time.time() + 30:
        return _token_cache["access_token"]
    response = httpx.post(
        f"{PHONEPE_AUTH_BASE_URL.rstrip('/')}/v1/oauth/token",
        data={
            "client_id": PHONEPE_CLIENT_ID,
            "client_version": PHONEPE_CLIENT_VERSION,
            "client_secret": PHONEPE_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    _token_cache["access_token"] = payload["access_token"]
    _token_cache["expires_at"] = float(payload.get("expires_at", time.time() + 300))
    return _token_cache["access_token"]


def create_payment(amount_rupees: int, customer_phone: str) -> Dict[str, str]:
    merchant_order_id = f"METRO_{uuid.uuid4().hex[:24]}"
    token = _get_access_token()
    response = httpx.post(
        f"{PHONEPE_BASE_URL.rstrip('/')}/checkout/v2/pay",
        json={
            "merchantOrderId": merchant_order_id,
            "amount": int(amount_rupees) * 100,
            "expireAfter": 1200,
            "paymentFlow": {
                "type": "PG_CHECKOUT",
                "merchantUrls": {"redirectUrl": PHONEPE_REDIRECT_URL},
            },
        },
        headers={"Content-Type": "application/json", "Authorization": f"O-Bearer {token}"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    url = payload.get("redirectUrl")
    if not url:
        raise RuntimeError("PhonePe response did not include a redirect URL")
    return {"merchant_order_id": merchant_order_id, "payment_url": url}


def _expected_webhook_auth() -> str:
    return hashlib.sha256(f"{PHONEPE_WEBHOOK_USERNAME}:{PHONEPE_WEBHOOK_PASSWORD}".encode("utf-8")).hexdigest()


def verify_callback(response_body: str, authorization_header: Optional[str]) -> Optional[Dict[str, Any]]:
    if not authorization_header or not PHONEPE_WEBHOOK_USERNAME or not PHONEPE_WEBHOOK_PASSWORD:
        return None
    if authorization_header != _expected_webhook_auth():
        return None
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return None