"""Thin wrapper around the WhatsApp Cloud API for sending messages."""
import logging
from typing import Optional

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


def send_button_message(to: str, body_text: str, buttons: list[dict]) -> dict:
    """Send a WhatsApp interactive button message."""
    formatted_buttons = [
        {
            "type": "reply",
            "reply": {
                "id": button["id"],
                "title": button["title"],
            },
        }
        for button in buttons[:3]
    ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": formatted_buttons},
        },
    }
    return _post(payload)


def send_quick_template(to: str, template_name: str, **kwargs) -> dict:
    """Send a quick-reply template by name.

    Supported templates:
      - who_booking: expects `display_name` in kwargs; buttons: book_self, book_other
      - booking_confirm: expects `body` in kwargs and optional `buttons` list
    """
    if template_name == "who_booking":
        display_name = kwargs.get("display_name", "You")
        buttons = [
            {"id": "book_self", "title": display_name},
            {"id": "book_other", "title": "Other"},
        ]
        return send_button_message(to, f"Who are you booking for?\n{display_name}", buttons)

    if template_name == "booking_confirm":
        body = kwargs.get("body", "Your booking is confirmed.")
        buttons = kwargs.get("buttons", [{"id": "route_more", "title": "View another route"}])
        return send_button_message(to, body, buttons)

    # fallback: simple button
    buttons = kwargs.get("buttons", [{"id": "route_help", "title": "Help"}])
    return send_button_message(to, kwargs.get("body", ""), buttons)


def build_route_selection_buttons(routes: list, max_buttons: int = 3) -> list[dict]:
    """Create a compact button list for route selection, capped at WhatsApp's button limit."""
    buttons = []
    for route in routes[:2]:
        title = (route.get("short_name") or route.get("name") or route.get("route_id") or route.get("id") or "Route")[:20]
        route_id = route.get("route_id") or route.get("id")
        buttons.append({"id": f"route_{route_id}", "title": title})

    if len(routes) > 2:
        buttons.append({"id": "view_all_routes", "title": "More routes"})
    buttons.append({"id": "main_menu", "title": "Main menu"})
    return buttons[:max_buttons]


def send_welcome_menu(to: str, display_name: str) -> dict:
    """Send a short welcome menu with the main actions users can take."""
    buttons = [
        {"id": "book_self", "title": "Book for me"},
        {"id": "book_other", "title": "For someone else"},
        {"id": "view_routes", "title": "View routes"},
    ]
    return send_button_message(to, f"Hi {display_name}! What would you like to do?", buttons)


def send_route_buttons(to: str, routes: list) -> dict:
    """Send a compact interactive route picker using buttons."""
    buttons = build_route_selection_buttons(routes)
    return send_button_message(to, "Choose a route to continue:", buttons)


def _make_sections(rows: list, base_title: str) -> list:
    sections = []
    for idx in range(0, min(len(rows), 30), 10):
        chunk = rows[idx : idx + 10]
        section_title = f"{base_title} {idx + 1}-{idx + len(chunk)}"
        sections.append({"title": section_title, "rows": chunk})
    return sections


def send_route_list(to: str, routes: list, display_name: Optional[str] = None) -> dict:
    """Send an interactive list so the user can tap to pick a route."""
    rows = []
    for route in routes:
        full_name = route.get("name") or route.get("short_name") or route.get("route_id") or route.get("id") or "Route"
        route_id = route.get("route_id") or route.get("id")
        title = (route.get("short_name") or route.get("name") or route_id or "Route")[:24]
        price = route.get("price") or 0
        rows.append(
            {
                "id": f"route_{route_id}",
                "title": title,
                "description": f"{full_name[:72]} • Rs {price} per seat"[:72],
            }
        )

    welcome_name = (display_name or "there").strip() or "there"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "ShuttleSeva"},
            "body": {
                "text": f"Hi {welcome_name}, welcome to ShuttleSeva. Please choose your route to see available departure times."
            },
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Select Route",
                "sections": _make_sections(rows, "Available Routes")[:1],
            },
        },
    }
    return _post(payload)


def send_route_time_list(to: str, services: list, display_name: Optional[str] = None) -> dict:
    """Send one combined list of route and time options in a single message."""
    rows = []
    for service in services:
        route_name = service.get("short_name") or service.get("name") or service.get("route_id") or "Route"
        date_text = service["service_date"].strftime("%b %d")
        time_text = service["service_time"].strftime("%I:%M %p").lstrip("0")
        rows.append(
            {
                "id": f"service_{service['id']}",
                "title": route_name[:24],
                "description": f"{date_text} • {time_text}"[:72],
            }
        )

    welcome_name = (display_name or "there").strip() or "there"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "ShuttleSeva"},
            "body": {
                "text": f"Hi {welcome_name}, welcome to ShuttleSeva. Tap one option to book your ride."
            },
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Select Route & Time",
                "sections": _make_sections(rows, "Route & Time")[:1],
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
        available = service.get("available_seats")
        seats_text = (
            f"{available} seat{'s' if available != 1 else ''} available"
            if available is not None
            else "Seats unknown"
        )
        rows.append(
            {
                "id": f"service_{service['id']}",
                "title": f"{date_text} {time_text}"[:24],
                "description": f"{route['short_name']} • {seats_text}"[:72],
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


def send_seat_count_prompt(to: str, service: dict, available_count: int) -> dict:
    """Ask the user how many seats they want to book and show the total price."""
    price = service.get("price") or 0
    max_allowed = min(4, available_count)
    seat_rows = []
    for count in range(1, max_allowed + 1):
        total_price = price * count
        seat_rows.append(
            {
                "id": f"seat_count_{service['id']}_{count}",
                "title": f"{count} seat{'s' if count > 1 else ''}",
                "description": f"Total Rs {total_price}"[:72],
            }
        )

    action_rows = [
        {
            "id": "back_to_route",
            "title": "Back",
            "description": "Return to the route list",
        },
        {
            "id": "refresh_seats",
            "title": "Refresh seats",
            "description": "Reload the latest seat availability",
        },
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": service["short_name"][:60]},
            "body": {
                "text": f"Total available seats: {available_count}\nPlease choose 1, 2, 3, or 4 seats (max {max_allowed})."
            },
            "footer": {"text": "metrotooffice.in"},
            "action": {
                "button": "Select Seats",
                "sections": _make_sections(seat_rows, "Seat Count") + [
                    {"title": "Options", "rows": action_rows},
                ],
            },
        },
    }

    return _post(payload)


def check_whatsapp_token() -> dict:
    """Perform a lightweight GET against the Graph API using the configured
    `WHATSAPP_TOKEN` and `PHONE_NUMBER_ID`. Returns a dict with status and
    the JSON body (or text on error).
    """
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}"
    try:
        resp = httpx.get(url, headers=_headers(), params={"fields": "id,display_phone_number"}, timeout=10)
        resp.raise_for_status()
        return {"status": resp.status_code, "body": resp.json()}
    except httpx.HTTPStatusError:
        logger.error("whatsapp token check failed: status=%s body=%s", resp.status_code, resp.text)
        return {"status": resp.status_code, "body": resp.text}
    except Exception as exc:
        logger.error("unexpected error checking whatsapp token: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}
