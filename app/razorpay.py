"""Razorpay Payment Links client for the booking flow."""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import httpx

from .config import (
    RAZORPAY_CALLBACK_URL,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
)

RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def create_payment(amount_rupees: int, customer_phone: str) -> Dict[str, str]:
    reference_id = f"METRO_{uuid.uuid4().hex[:24]}"
    amount_paise = int(
        (Decimal(str(amount_rupees)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    response = httpx.post(
        f"{RAZORPAY_BASE_URL}/payment_links",
        json={
            "amount": amount_paise,
            "currency": "INR",
            "reference_id": reference_id,
            "customer": {"contact": f"+{customer_phone}"},
            "notify": {"sms": False, "email": False},
            "callback_url": RAZORPAY_CALLBACK_URL,
            "callback_method": "get",
        },
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    url = payload.get("short_url")
    if not url:
        raise RuntimeError("Razorpay response did not include a payment URL")
    return {"merchant_order_id": reference_id, "payment_url": url}


def verify_callback(response_body: str, signature_header: Optional[str]) -> Optional[Dict[str, Any]]:
    if not signature_header or not RAZORPAY_WEBHOOK_SECRET:
        return None
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), response_body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        return None
    try:
        return json.loads(response_body)
    except json.JSONDecodeError:
        return None
