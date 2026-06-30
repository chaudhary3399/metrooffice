"""
WhatsApp webhook for the Metro-to-Office booking bot.
Step 1: verify webhook, receive messages, reply to any text with the route list,
and acknowledge a tapped route.
"""
import logging

from fastapi import FastAPI, Request, Response

from .bookings import (
    available_seats,
    book_seat,
    get_route,
    get_route_services,
    get_routes,
    get_service,
    save_customer,
    seed_today_services,
)
from .config import VERIFY_TOKEN
from .whatsapp import send_route_list, send_service_list, send_seat_list, send_text

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("metrooffice")

app = FastAPI(title="Metro to Office WhatsApp Bot")

latest_event = {}
debug_info = {
    "webhook_calls": 0,
    "verification_calls": 0,
    "last_verify": {},
    "last_event": {},
}

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

@app.post("/webhook")
async def receive_message(request: Request):
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
                send_text(from_number, "Sorry, I couldn't process that selection. Please try again.")
                return {"status": "ok"}

            if selected_id.startswith("seat_"):
                parts = selected_id.split("_")
                route_service_id = int(parts[1])
                seat = int(parts[2])
                service = get_service(route_service_id)
                if service and book_seat(route_service_id, seat, from_number, customer_name):
                    when = service["service_time"].strftime("%I:%M %p").lstrip("0")
                    send_text(
                        from_number,
                        f"Seat {seat} booked on {service['short_name']} at {when} on {service['service_date']}. Amount: Rs {service['price']}. (Next: QR Payment.)",
                    )
                else:
                    left = available_seats(route_service_id)
                    if left and service:
                        send_seat_list(from_number, service, left)
                    else:
                        send_text(from_number, "Sorry, that service is fully booked or unavailable.")
            elif selected_id.startswith("service_"):
                route_service_id = int(selected_id.split("_", 1)[1])
                service = get_service(route_service_id)
                if service:
                    seats = available_seats(route_service_id)
                    if seats:
                        send_seat_list(from_number, service, seats)
                    else:
                        send_text(from_number, "Sorry, this service has no seats available.")
                else:
                    send_text(from_number, "Sorry, that service is no longer available.")
            elif selected_id.startswith("route_"):
                route_id = selected_id.split("_", 1)[1]
                route = get_route(route_id)
                if route:
                    services = get_route_services(route_id)
                    if services:
                        send_service_list(from_number, route, services)
                    else:
                        logger.info("no active services found for route %s", route_id)
                        send_text(from_number, "Sorry, this route has no active services at the moment.")
                else:
                    send_text(from_number, "Sorry, that route is not available.")
            else:
                send_text(from_number, "Sorry, I could not understand that selection. Please try again.")
        else:
            logger.info("sending route list to %s", from_number)
            try:
                send_route_list(from_number, get_routes())
            except Exception as send_exc:
                logger.error("failed to send route list: %s", send_exc, exc_info=True)
                send_text(from_number, "Sorry, I couldn't load routes right now. Please try again later.")
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

### END ###
