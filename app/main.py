"""
WhatsApp webhook for the Metro-to-Office booking bot.
Step 1: verify webhook, receive messages, reply to any text with the route list,
and acknowledge a tapped route.
"""
import logging
import time
from datetime import datetime

from fastapi import FastAPI, Request, Response, BackgroundTasks

from .bookings import (
    available_seats,
    book_seats,
    get_route,
    get_route_services,
    get_routes,
    get_service,
    save_customer,
    seed_today_services,
    start_driver_reminder_worker,
    update_booking_passenger_details,
)
from .config import VERIFY_TOKEN, ROUTES_FILE
import yaml
from .whatsapp import (
    send_route_list,
    send_route_time_list,
    send_service_list,
    send_seat_count_prompt,
    send_text,
    send_button_message,
    send_quick_template,
    send_welcome_menu,
    check_whatsapp_token,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("metrooffice")

app = FastAPI(title="ShuttleSeva WhatsApp Bot")

@app.on_event("startup")
async def startup_event() -> None:
    start_driver_reminder_worker()

latest_event = {}
debug_info = {
    "webhook_calls": 0,
    "verification_calls": 0,
    "last_verify": {},
    "last_event": {},
}

# In-memory pending conversation actions keyed by requestor phone number.
# Example: { "+911234...": {"action": "collect_other", "stage": "name", "name": None, "target_phone": None, "booking_for": "other"} }
pending_actions = {}
prompted_numbers = set()
processed_message_ids = set()

@app.get("/debug/last-event")
async def debug_last_event():
    """Return the last webhook payload received by the bot."""
    return {"last_event": latest_event}

@app.get("/debug/info")
async def debug_info_route():
    """Return debug stats about webhook and verification activity."""
    return debug_info

@app.get("/admin/seed-services")
async def admin_seed_services():
    """Seed today’s routes and hourly services from the default route config."""
    try:
        seed_today_services()
        return {"status": "ok", "message": "Today’s services seeded."}
    except Exception as exc:
        logger.error("failed to seed today\'s services: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta calls this once to confirm the webhook URL is yours."""
    global debug_info
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    debug_info["verification_calls"] += 1
    debug_info["last_verify"] = {
        "mode": mode,
        "token": token,
        "challenge": challenge,
    }
    logger.info("webhook verify request: hub.mode=%s hub.verify_token=%s hub.challenge=%s", mode, token, challenge)
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("webhook verification succeeded")
        return Response(content=challenge, media_type="text/plain")
    logger.warning("webhook verification failed")
    return Response(content="Verification failed", status_code=403)

def _get_available_services_for_selection() -> list:
    services = []
    for route in get_routes():
        for service_info in get_route_services(route["route_id"]):
            service = get_service(service_info["id"])
            if not service:
                continue
            if available_seats(service["id"]):
                services.append(service)
    return services


def _send_route_selection_prompt(from_number: str, customer_name: str | None = None, background_tasks: BackgroundTasks | None = None) -> None:
    prompted_numbers.add(from_number)
    # Send a compact route selection first to keep initial response fast.
    # Prefer reading routes directly from the YAML file (fast, in-memory),
    # falling back to the DB-backed `get_routes()` if the file is missing.
    routes = []
    try:
        if ROUTES_FILE.exists():
            with open(ROUTES_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                routes = data.get("routes", []) if isinstance(data, dict) else []
    except Exception:
        routes = []

    if not routes:
        # If no YAML routes, fall back to DB (unchanged behaviour).
        try:
            routes = get_routes()
        except Exception:
            routes = []

    # Normalize YAML-loaded routes to the DB-shaped dicts the rest of the code expects.
    normalized_routes = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        # DB-backed entries use 'route_id'; YAML uses 'id'. Ensure both are present.
        route_id = r.get("route_id") or r.get("id")
        if not route_id:
            continue
        normalized_routes.append(
            {
                "route_id": route_id,
                "name": r.get("name") or r.get("short_name") or route_id,
                "short_name": r.get("short_name") or r.get("name") or route_id,
                "price": r.get("price") or 0,
            }
        )

    if normalized_routes:
        if background_tasks:
            background_tasks.add_task(send_route_list, from_number, normalized_routes, customer_name)
        else:
            send_route_list(from_number, normalized_routes, display_name=customer_name)
    else:
        if background_tasks:
            background_tasks.add_task(send_text, from_number, "Sorry, there are no routes available right now.")
        else:
            send_text(from_number, "Sorry, there are no routes available right now.")


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """Meta sends every incoming WhatsApp message here."""
    global latest_event, debug_info
    body = await request.json()
    latest_event = body
    debug_info["webhook_calls"] += 1
    debug_info["last_event"] = body
    logger.info("incoming webhook event: %s", body)
    try:
        value = body["entry"][0]["changes"][0]["value"]
        messages = value.get("messages")
        if not messages:
            logger.info("no messages found in incoming event")
            return {"status": "ignored"}

        message = messages[0]
        # Idempotency: ignore duplicate webhook deliveries for the same message id
        message_id = message.get("id")
        if message_id and message_id in processed_message_ids:
            logger.info("duplicate webhook delivery ignored: %s", message_id)
            return {"status": "ignored"}
        if message_id:
            processed_message_ids.add(message_id)
        from_number = message["from"]
        customer_name = None
        contacts = value.get("contacts") or []
        if contacts:
            customer_name = contacts[0].get("profile", {}).get("name")

        try:
            save_customer(from_number, customer_name)
        except Exception as save_exc:
            logger.warning("failed to save customer info: %s", save_exc)

        msg_type = message["type"]
        logger.info("message from=%s type=%s name=%s", from_number, msg_type, customer_name)

        if msg_type == "interactive":
            reply = message["interactive"]
            selected_id = None
            if "list_reply" in reply:
                selected_id = reply["list_reply"].get("id")
            elif "button_reply" in reply:
                selected_id = reply["button_reply"].get("id")
            logger.info("interactive reply received: %s", selected_id)

            if not selected_id:
                logger.warning("interactive reply missing id: %s", reply)
                background_tasks.add_task(
                    send_button_message,
                    from_number,
                    "Sorry, I couldn't process that selection. Please try again.",
                    [
                        {"id": "route_help", "title": "Show routes again"},
                    ],
                )
                return {"status": "ok"}

            # Quick navigation and booking-for choices
            if selected_id == "book_self":
                # Clear any pending other-collection state and proceed to routes
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id == "book_other":
                # Start collecting other passenger details in one response.
                pending_actions[from_number] = {"action": "collect_other", "stage": "entry"}
                background_tasks.add_task(
                    send_text,
                    from_number,
                    "Please reply with the passenger's name and 10-digit phone number in one message, for example:\nRavi Chaudhary, 9876543210",
                )
                return {"status": "ok"}

            if selected_id == "book_for_other":
                pending_actions[from_number] = {
                    "action": "collect_other",
                    "stage": "entry",
                }
                background_tasks.add_task(
                    send_text,
                    from_number,
                    "Please reply with the passenger's name and 10-digit phone number in one message, for example:\nRavi Chaudhary, 9876543210",
                )
                return {"status": "ok"}

            if selected_id == "view_routes":
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id == "main_menu":
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id == "view_all_routes":
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id == "back_to_route":
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id in ("refresh_seats", "refresh_services"):
                # simple fallback: send route list to restart selection
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id in ("route_more", "route_help"):
                pending_actions.pop(from_number, None)
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
                return {"status": "ok"}

            if selected_id.startswith("seat_count_"):
                parts = selected_id.split("_")
                route_service_id = int(parts[2])
                seat_count = int(parts[3])
                service = get_service(route_service_id)
                if service:
                    now = datetime.now()
                    svc_date = service.get("service_date")
                    svc_time = service.get("service_time")
                    if svc_date == now.date() and svc_time <= now.time():
                        background_tasks.add_task(
                            send_button_message,
                            from_number,
                            "Sorry, the selected departure time has already passed.",
                            [
                                {"id": "route_more", "title": "Choose another route"},
                                {"id": "refresh_services", "title": "Refresh services"},
                            ],
                        )
                        return {"status": "ok"}
                pending = pending_actions.get(from_number, {})
                phone_to_book = pending.get("target_phone") or from_number
                passenger_name_for_booking = pending.get("name") or customer_name

                if service:
                    success, booked_seats, total_amount = book_seats(route_service_id, seat_count, phone_to_book, passenger_name_for_booking)
                    if success:
                        if pending and pending.get("action") == "collect_other":
                            pending_actions[from_number] = {
                                "action": "collect_other",
                                "stage": "entry",
                                "booking_context": {
                                    "route_service_id": route_service_id,
                                    "seat_numbers": booked_seats,
                                    "original_phone": phone_to_book,
                                    "original_customer_name": passenger_name_for_booking,
                                },
                            }
                        else:
                            pending_actions[from_number] = {
                                "action": "await_other_post_booking",
                                "booking_context": {
                                    "route_service_id": route_service_id,
                                    "seat_numbers": booked_seats,
                                    "original_phone": phone_to_book,
                                    "original_customer_name": passenger_name_for_booking,
                                },
                            }

                        when = service["service_time"].strftime("%I:%M %p").lstrip("0")
                        driver_lines = []
                        if service.get("driver_name"):
                            driver_lines.append(f"Driver: {service['driver_name']}")
                        if service.get("vehicle_type") and service.get("vehicle_number"):
                            driver_lines.append(f"Vehicle: {service['vehicle_type']} ({service['vehicle_number']})")
                        if service.get("driver_phone"):
                            driver_lines.append(f"Driver phone: {service['driver_phone']}")
                        driver_text = "\n".join(driver_lines)
                        passenger_display = passenger_name_for_booking or "Passenger"
                        seats_text = ", ".join(str(seat) for seat in booked_seats)
                        status_text = (
                            f"🎉 *Congratulations! {passenger_display} — Your seats are booked.*\n"
                            f"\n🚍 *Route:* {service['short_name']}\n"
                            f"🪑 *Seats:* {seats_text}\n"
                            f"🕒 *Departure:* {when} on {service['service_date']}\n"
                            f"💰 *Amount:* Rs {total_amount}"
                        )
                        if driver_text:
                            status_text += f"\n\n{driver_text}"
                        status_text += "\n\n✨ Have a safe and pleasant ride!"
                        status_text += "\n\nIf this ride is for someone else, please type 'other'."
                        # Final booking confirmation: send plain text with no action buttons.
                        background_tasks.add_task(send_text, from_number, status_text)
                    else:
                        left = available_seats(route_service_id)
                        if left and service:
                            background_tasks.add_task(send_seat_count_prompt, from_number, service, len(left))
                        else:
                            background_tasks.add_task(
                                send_button_message,
                                from_number,
                                "Sorry, that service is fully booked or unavailable.",
                                [
                                    {"id": "route_more", "title": "Try another route"},
                                ],
                            )
                else:
                    background_tasks.add_task(
                        send_button_message,
                        from_number,
                        "Sorry, that service is no longer available.",
                        [
                            {"id": "route_more", "title": "Try another route"},
                        ],
                    )
            elif selected_id.startswith("service_"):
                route_service_id = int(selected_id.split("_", 1)[1])
                service = get_service(route_service_id)
                if service:
                    seats = available_seats(route_service_id)
                    if seats:
                        background_tasks.add_task(
                            send_text,
                            from_number,
                            "Thanks for selecting your time. We are working on it and will confirm your seat in a moment.",
                        )
                        success, booked_seats, total_amount = book_seats(route_service_id, 1, from_number, customer_name)
                        if success:
                            if pending_actions.get(from_number, {}).get("action") == "collect_other":
                                pending_actions[from_number] = {
                                    "action": "collect_other",
                                    "stage": "entry",
                                    "booking_context": {
                                        "route_service_id": route_service_id,
                                        "seat_numbers": booked_seats,
                                        "original_phone": from_number,
                                        "original_customer_name": customer_name,
                                    },
                                }
                            else:
                                pending_actions[from_number] = {
                                    "action": "await_other_post_booking",
                                    "booking_context": {
                                        "route_service_id": route_service_id,
                                        "seat_numbers": booked_seats,
                                        "original_phone": from_number,
                                        "original_customer_name": customer_name,
                                    },
                                }
                            when = service["service_time"].strftime("%I:%M %p").lstrip("0")
                            passenger_display_single = customer_name or "Passenger"
                            status_text = (
                                f"🎉 Congratulations {passenger_display_single} | your 1 seat is booked.\n"
                                f"\n🚍 Route: {service['short_name']}\n"
                                f"🕒 Departure: {when} on {service['service_date']}\n"
                                f"💰 Amount: Rs {total_amount}"
                            )
                            driver_lines = []
                            if service.get("driver_name"):
                                driver_lines.append(f"Driver: {service['driver_name']}")
                            if service.get("vehicle_type") and service.get("vehicle_number"):
                                driver_lines.append(f"Vehicle: {service['vehicle_type']} ({service['vehicle_number']})")
                            if service.get("driver_phone"):
                                driver_lines.append(f"Driver phone: {service['driver_phone']}")
                            if driver_lines:
                                status_text += f"\n\n{('\n'.join(driver_lines))}"
                            status_text += "\n\nIf this ride is for someone else, please type 'other'."
                            # Final booking confirmation: send plain text with no action buttons.
                            background_tasks.add_task(send_text, from_number, status_text)
                        else:
                            background_tasks.add_task(
                                send_button_message,
                                from_number,
                                "Sorry, that service is no longer available.",
                                [
                                    {"id": "route_more", "title": "Choose another route"},
                                ],
                            )
                    else:
                        background_tasks.add_task(
                            send_button_message,
                            from_number,
                            "Sorry, this service has no seats available.",
                            [
                                {"id": "route_more", "title": "Choose another route"},
                            ],
                        )
                else:
                    background_tasks.add_task(
                        send_button_message,
                        from_number,
                        "Sorry, that service is no longer available.",
                        [
                            {"id": "route_more", "title": "Show routes"},
                        ],
                    )
            elif selected_id.startswith("route_"):
                route_id = selected_id.split("_", 1)[1]
                route = get_route(route_id)
                if route:
                    display_name = customer_name or "there"
                    background_tasks.add_task(
                        send_text,
                        from_number,
                        f"Thanks {display_name}, we are checking available seats for this route now. Please wait a moment.",
                    )

                    def delayed_service_list():
                        time.sleep(1)
                        services = get_route_services(route_id)
                        for service_item in services:
                            service_item["available_seats"] = len(available_seats(service_item["id"]))
                        if services:
                            send_service_list(from_number, route, services)
                        else:
                            logger.info("no active services found for route %s", route_id)
                            send_button_message(
                                from_number,
                                "Sorry, this route has no active services at the moment.",
                                [
                                    {"id": "route_more", "title": "Try another route"},
                                ],
                            )

                    background_tasks.add_task(delayed_service_list)
                else:
                    background_tasks.add_task(
                        send_button_message,
                        from_number,
                        "Sorry, that route is not available.",
                        [
                            {"id": "route_more", "title": "Show routes"},
                        ],
                    )
            else:
                background_tasks.add_task(
                    send_button_message,
                    from_number,
                    "Sorry, I could not understand that selection. Please try again.",
                    [
                        {"id": "route_help", "title": "Show routes again"},
                    ],
                )
        else:
            # Handle non-interactive messages (e.g., plain text).
            # If we're collecting an 'other' passenger's details, process those steps.
            pending = pending_actions.get(from_number)
            if msg_type == "text" and pending and pending.get("action") == "collect_other":
                body_text = message.get("text", {}).get("body", "").strip()
                if pending.get("stage") == "entry":
                    # Expect a single message like "Ravi Chaudhary, 9876543210"
                    parts = [part.strip() for part in body_text.replace(';', ',').split(',') if part.strip()]
                    if len(parts) < 2:
                        background_tasks.add_task(
                            send_text,
                            from_number,
                            "Please send both the passenger's name and a 10-digit Indian phone number separated by a comma.\nExample: Ravi Chaudhary, 9876543210",
                        )
                        return {"status": "ok"}

                    name = parts[0]
                    phone = parts[-1]
                    phone_digits = ''.join(ch for ch in phone if ch.isdigit())
                    if len(phone_digits) != 10 or not phone_digits.isdigit():
                        background_tasks.add_task(
                            send_text,
                            from_number,
                            "Please provide a valid 10-digit Indian phone number. Example: 9876543210",
                        )
                        return {"status": "ok"}

                    phone = f"91{phone_digits}"
                    booking_context = pending.get("booking_context") or {}
                    success = update_booking_passenger_details(
                        booking_context.get("route_service_id"),
                        booking_context.get("seat_numbers", []),
                        booking_context.get("original_phone") or from_number,
                        phone,
                        name,
                    )
                    pending_actions.pop(from_number, None)
                    if success:
                        # After updating passenger details for an 'other' booking,
                        # send a full booking confirmation including driver and vehicle info.
                        route_service_id = booking_context.get("route_service_id")
                        seat_numbers = booking_context.get("seat_numbers", [])
                        service = None
                        try:
                            service = get_service(route_service_id)
                        except Exception:
                            service = None

                        if service:
                            when = service["service_time"].strftime("%I:%M %p").lstrip("0")
                            seats_text = ", ".join(str(s) for s in seat_numbers)
                            price = service.get("price") or 0
                            total_amount = price * max(1, len(seat_numbers))
                            driver_lines = []
                            if service.get("driver_name"):
                                driver_lines.append(f"Driver: {service['driver_name']}")
                            if service.get("vehicle_type") and service.get("vehicle_number"):
                                driver_lines.append(f"Vehicle: {service['vehicle_type']} ({service['vehicle_number']})")
                            if service.get("driver_phone"):
                                driver_lines.append(f"Driver phone: {service['driver_phone']}")
                            driver_text = "\n".join(driver_lines)

                            status_text = (
                                f"🎉 Congratulations {name} | your {len(seat_numbers)} seat{'s' if len(seat_numbers)>1 else ''} is booked.\n"
                                f"\n🚍 Route: {service.get('short_name')}\n"
                                f"🪑 Seats: {seats_text}\n"
                                f"🕒 Departure: {when} on {service['service_date']}\n"
                                f"💰 Amount: Rs {total_amount}"
                            )
                            if driver_text:
                                status_text += f"\n\n{driver_text}"
                            status_text += "\n\n✨ Have a safe and pleasant ride!"
                            # Send final confirmation
                            background_tasks.add_task(send_text, from_number, status_text)
                        else:
                            background_tasks.add_task(
                                send_text,
                                from_number,
                                f"Booking details updated for {name} on {phone}.",
                            )
                    else:
                        background_tasks.add_task(
                            send_text,
                            from_number,
                            "I couldn't update the booking details yet. Please try again.",
                        )
                    return {"status": "ok"}

            # If the user has a completed booking and types "other", switch to collecting passenger details.
            if msg_type == "text" and pending and pending.get("action") == "await_other_post_booking":
                body_text = message.get("text", {}).get("body", "").strip().lower()
                if body_text == "other":
                    pending_actions[from_number] = {
                        "action": "collect_other",
                        "stage": "entry",
                        "booking_context": pending.get("booking_context", {}),
                    }
                    background_tasks.add_task(
                        send_text,
                        from_number,
                        "Please reply with the passenger's name and a 10-digit Indian phone number in one message, for example:\nRavi Chaudhary, 9876543210",
                    )
                    return {"status": "ok"}
                pending_actions.pop(from_number, None)

            # Otherwise, this is a fresh conversation: start with route selection.
            if msg_type == "text":
                try:
                    _send_route_selection_prompt(from_number, customer_name, background_tasks)
                except Exception:
                    background_tasks.add_task(
                        send_button_message,
                        from_number,
                        "Sorry, I couldn't load routes right now. Please try again later.",
                        [
                            {"id": "route_help", "title": "Retry"},
                        ],
                    )
                return {"status": "ok"}

            # fallback: send route list
            logger.info("sending route list to %s", from_number)
            try:
                _send_route_selection_prompt(from_number, customer_name, background_tasks)
            except Exception as send_exc:
                logger.error("failed to send route list: %s", send_exc, exc_info=True)
                background_tasks.add_task(
                    send_button_message,
                    from_number,
                    "Sorry, I couldn't load routes right now. Please try again later.",
                    [
                        {"id": "route_help", "title": "Retry"},
                    ],
                )
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("failed to parse webhook event: %s", exc)
        return {"status": "ignored"}
    except Exception as exc:
        logger.error("unexpected error in webhook handler: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}
    return {"status": "ok"}


@app.get("/")
async def health():
    return {"status": "running", "service": "metrotoffice-whatsapp-bot"}


@app.get("/admin/check-whatsapp")
async def admin_check_whatsapp():
    """Admin endpoint to validate the configured WhatsApp token and phone id.

    Returns the Graph API response (status + body). Useful to debug 401 errors.
    """
    try:
        result = check_whatsapp_token()
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.error("admin/check-whatsapp failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}

### END ###
