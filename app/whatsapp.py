"""Thin wrapper around the WhatsApp Cloud API for sending messages."""
import logging

import httpx

from .config import GRAPH_API_VERSION, PHONE_NUMBER_ID, WHATSAPP_TOKEN

logger = logging.getLogger("metrooffice.whatsapp")


def _url() -> str:
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


def _post(payload: dict) -> dict:
    resp = httpx.post(_url(), headers=_headers(), json=payload, timeout=30)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "WhatsApp API request failed: status=%s url=%s payload=%s response=%s",
            resp.status_code,
            _url(),
            payload,
            resp.text,
        )
        raise
    return resp.json()


def send_text(to: str, text: str) -> dict:
    """Send a plain text message."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    return _post(payload)


def _make_sections(rows: list, base_title: str) -> list:
    sections = []
    for idx in range(0, min(len(rows), 30), 10):
        chunk = rows[idx : idx + 10]
        section_title = f"{base_title} {idx + 1}-{idx + len(chunk)}"
        sections.append({"title": section_title, "rows": chunk})
    return sections


def send_route_list(to: str, routes: list) -> dict:
    """Send an interactive list so the user can tap to pick a route."""
    rows = []
    for route in routes:
        rows.append(
            {
                "id": f"route_{route['route_id']}",
                "title": route["short_name"][:24],
                "description": f"Rs {route['price']} per seat"[:72],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Metro to Office"},
            "body": {"text": "Hi! Please choose your route"},
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Select Route",
                "sections": _make_sections(rows, "Available Routes")[:1],
            },
        },
    }
    return _post(payload)


def send_service_list(to: str, route: dict, services: list) -> dict:
    """Send an interactive list of available date/time services for a route."""
    rows = []
    for service in services:
        date_text = service["service_date"].strftime("%b %d")
        time_text = service["service_time"].strftime("%I:%M %p").lstrip("0")
        rows.append(
            {
                "id": f"service_{service['id']}",
                "title": f"{date_text} {time_text}"[:24],
                "description": f"{route['short_name']}"[:72],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": route["short_name"][:60]},
            "body": {"text": "Select date and time for this route."},
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Select Time",
                "sections": _make_sections(rows, "Available Services")[:1],
            },
        },
    }
    return _post(payload)


def send_seat_list(to: str, service: dict, seats: list) -> dict:
    """Send an interactive list of the seats still available on a service."""
    rows = [
        {
            "id": f"seat_{service['id']}_{seat}",
            "title": f"Seat {seat}",
            "description": f"Rs {service['price']}" if service.get("price") is not None else "Available",
        }
        for seat in seats[:30]
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": service["short_name"][:60]},
            "body": {"text": f"{len(seats)} seat(s) available. Pick one:"},
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Choose Seat",
                "sections": _make_sections(rows, "Available Seats")[:1],
            },
        },
    }

    return _post(payload)
