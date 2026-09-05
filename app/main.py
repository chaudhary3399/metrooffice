"""
WhatsApp webhook for the Metro-to-Office booking bot.
Step 1: verify webhook, receive messages, reply to any text with the route list,
and acknowledge a tapped route.
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bookings import (
    available_seats,
    book_seats,
    get_route,
    get_route_services,
    get_routes,
    get_service,
    save_customer,
    seed_today_services,
    update_booking_passenger_details,
)
from .config import (
    BUSINESS_NAME,
    RAZORPAY_PAYMENT_LINK,
    RAZORPAY_WEBHOOK_SECRET,
    ROUTES_FILE,
    SUPPORT_EMAIL,
    VERIFY_TOKEN,
    WHATSAPP_CONTACT_NUMBER,
)
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
SITE_FILE = ROUTES_FILE.parent.parent / "site" / "index.html"
app.mount("/site", StaticFiles(directory=SITE_FILE.parent), name="site")

@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Startup complete. File-backed booking mode is active.")

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
processed_message_times = {}
pending_payments = {}


def _remember_message_id(message_id: str) -> bool:
    """Return True only for a new message id; ignore duplicate webhook retries immediately."""
    if not message_id:
        return True

    now = time.time()
    expired = [mid for mid, ts in processed_message_times.items() if now - ts > 3600]
    for mid in expired:
        processed_message_times.pop(mid, None)
        processed_message_ids.discard(mid)

    if message_id in processed_message_ids:
        return False

    processed_message_ids.add(message_id)
    processed_message_times[message_id] = now
    return True

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


def _send_route_selection_prompt(from_number: str, customer_name: Optional[str] = None, background_tasks: Optional[BackgroundTasks] = None) -> None:
    prompted_numbers.add(from_number)
    routes = []
    try:
        routes = get_routes()
    except Exception as e:
        logger.error("Failed to get routes: %s", e, exc_info=True)
        routes = []

    if not routes:
        error_msg = "Sorry, there are no routes available right now."
        if background_tasks:
            background_tasks.add_task(send_text, from_number, error_msg)
        else:
            send_text(from_number, error_msg)
        return

    if background_tasks:
        background_tasks.add_task(send_route_list, from_number, routes, customer_name)
    else:
        send_route_list(from_number, routes, display_name=customer_name)


def _payment_message(amount: int) -> str:
    return (
        f"Please complete the payment of Rs {amount} using this Razorpay link:\n"
        f"{RAZORPAY_PAYMENT_LINK}\n\n"
        "Your booking will be confirmed automatically after Razorpay confirms the payment."
    )


def _queue_payment(
    from_number: str,
    route_service_id: int,
    seat_count: int,
    phone_to_book: str,
    passenger_name: Optional[str],
    service: dict,
    background_tasks: BackgroundTasks,
) -> None:
    if not RAZORPAY_PAYMENT_LINK:
        logger.error("RAZORPAY_PAYMENT_LINK is not configured")
        background_tasks.add_task(send_text, from_number, "Payment is temporarily unavailable. Please try again later.")
        return
    amount = int(service.get("price") or 0) * seat_count
    pending_payments[from_number] = {
        "route_service_id": route_service_id,
        "seat_count": seat_count,
        "phone_to_book": phone_to_book,
        "passenger_name": passenger_name,
        "amount": amount,
    }
    logger.info("payment requested from=%s service=%s amount=%s", from_number, route_service_id, amount)
    background_tasks.add_task(send_text, from_number, _payment_message(amount))


def _payment_phone(payload: dict) -> Optional[str]:
    entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    contact = entity.get("customer", {}).get("contact") or entity.get("contact")
    if not contact:
        return None
    digits = "".join(character for character in str(contact) if character.isdigit())
    if len(digits) == 10:
        return f"91{digits}"
    if digits.startswith("0") and len(digits) == 11:
        return f"91{digits[1:]}"
    return digits or None


async def _complete_paid_booking(phone: str, background_tasks: BackgroundTasks) -> None:
    pending = pending_payments.pop(phone, None)
    if not pending:
        logger.warning("paid Razorpay webhook has no pending booking for phone=%s", phone)
        return
    service = get_service(pending["route_service_id"])
    if not service:
        background_tasks.add_task(send_text, phone, "Payment received, but the selected service is no longer available. Please contact support.")
        return
    success, booked_seats, total_amount = book_seats(
        pending["route_service_id"], pending["seat_count"],
        pending["phone_to_book"], pending["passenger_name"],
    )
    if not success:
        background_tasks.add_task(send_text, phone, "Payment received, but the selected service is now full. Please contact support for a refund.")
        return
    when = service["service_time"].strftime("%I:%M %p").lstrip("0")
    seats_text = ", ".join(str(seat) for seat in booked_seats)
    background_tasks.add_task(
        send_text, phone,
        f"Booking confirmed after payment.\n\nRoute: {service['short_name']}\n"
        f"Seats: {seats_text}\nDeparture: {when} on {service['service_date']}\n"
        f"Amount paid: Rs {total_amount}",
    )


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks):
    """Confirm bookings only for a signature-verified Razorpay payment event."""
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not RAZORPAY_WEBHOOK_SECRET or not hmac.compare_digest(signature, expected):
        logger.warning("rejected Razorpay webhook with invalid signature")
        return Response(content="Invalid signature", status_code=400)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(content="Invalid JSON", status_code=400)
    if payload.get("event") == "payment_link.paid":
        phone = _payment_phone(payload)
        if phone:
            await _complete_paid_booking(phone, background_tasks)
        else:
            logger.warning("Razorpay payment webhook did not include a customer phone")
    return {"status": "ok"}


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
        message_id = message.get("id")
        if not _remember_message_id(message_id):
            logger.info("duplicate webhook delivery ignored: %s", message_id)
            return {"status": "ignored"}

        from_number = message.get("from")
        if not from_number:
            logger.warning("Webhook message missing from_number: %s", message)
            return {"status": "ignored"}

        from_number = message["from"]
        customer_name = None
        contacts = value.get("contacts") or []
        if contacts:
            customer_name = contacts[0].get("profile", {}).get("name")

        try:
            save_customer(from_number, customer_name)
        except Exception as save_exc:
            logger.error("failed to save customer info: %s", save_exc, exc_info=True)

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
                    _queue_payment(
                        from_number, route_service_id, seat_count, phone_to_book,
                        passenger_name_for_booking, service, background_tasks,
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
                        _queue_payment(
                            from_number, route_service_id, 1, from_number,
                            customer_name, service, background_tasks,
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
                logger.info("route selected: %s by %s", route_id, from_number)
                route = get_route(route_id)
                if route:
                    try:
                        services = get_route_services(route_id)
                        for service_item in services:
                            service_item["available_seats"] = len(available_seats(service_item["id"]))
                        if services:
                            logger.info("sending %d services for route %s", len(services), route_id)
                            background_tasks.add_task(send_service_list, from_number, route, services)
                        else:
                            logger.info("no active services found for route %s", route_id)
                            background_tasks.add_task(
                                send_button_message,
                                from_number,
                                "Sorry, this route has no active services at the moment.",
                                [
                                    {"id": "route_more", "title": "Try another route"},
                                ],
                            )
                    except Exception as e:
                        logger.error("error getting services for route %s: %s", route_id, e, exc_info=True)
                        background_tasks.add_task(
                            send_button_message,
                            from_number,
                            "Sorry, error loading services. Please try again.",
                            [
                                {"id": "route_more", "title": "Try another route"},
                            ],
                        )
                else:
                    logger.warning("route not found: %s", route_id)
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
                logger.info("sending initial route selection to %s (customer: %s)", from_number, customer_name)
                try:
                    _send_route_selection_prompt(from_number, customer_name, background_tasks)
                except Exception as exc:
                    logger.error("failed to send route selection: %s", exc, exc_info=True)
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
async def website():
    return FileResponse(SITE_FILE)


@app.get("/api/routes")
async def public_routes():
    return {
        "business_name": BUSINESS_NAME,
        "whatsapp_number": WHATSAPP_CONTACT_NUMBER,
        "support_email": SUPPORT_EMAIL,
        "routes": get_routes(),
    }


@app.get("/health")
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
