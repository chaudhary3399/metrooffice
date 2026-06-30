"""Thin wrapper around the WhatsApp Cloud API for sending messages."""
import httpx

from .config import GRAPH_API_VERSION, PHONE_NUMBER_ID, WHATSAPP_TOKEN


def _url() -> str:
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


def send_text(to: str, text: str) -> dict:
    """Send a plain text message."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    resp = httpx.post(_url(), headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_seat_list(to: str, route: dict, seats: list) -> dict:
    """Send an interactive list of the seats still available on a route."""
    rows = [
        {
            "id": f"seat_{route['id']}_{seat}",
            "title": f"Seat {seat}",
            "description": f"Rs {route['price']}",
        }
        for seat in seats[:10]
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": route["short_name"][:60]},
            "body": {"text": f"{len(seats)} seat(s) available. Pick one:"},
            "footer": {"text": "metrooffice.in"},
            "action": {
                "button": "Choose Seat",
                "sections": [{"title": "Available Seats", "rows": rows}],
            },
        },
    }

    resp = httpx.post(_url(), headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_route_list(to: str, routes: list) -> dict:
    """Send an interactive list so the user can tap to pick a route."""
    rows = []
    for route in routes:
        rows.append(
            {
                "id": route["id"],
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
                "sections": [{"title": "Available Routes", "rows": rows}],
            },
        },
    }
    resp = httpx.post(_url(), headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()
