"""
WhatsApp webhook for the Metro-to-Office booking bot.
Step 1: verify webhook, receive messages, reply to any text with the route list,
and acknowledge a tapped route.
"""
import logging

from fastapi import FastAPI, Request, Response

from .bookings import available_seats, book_seat, get_route
from .config import VERIFY_TOKEN, load_routes
from .whatsapp import send_route_list, send_seat_list, send_text

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
        msg_type = message["type"]
        logger.info("message from=%s type=%s", from_number, msg_type)

        if msg_type == "interactive":
            reply = message["interactive"]
            selected_id = reply["list_reply"]["id"]
            selected_title = reply["list_reply"]["title"]
            if selected_id.startswith("seat_"):
                parts = selected_id.split("_")
                route_id = "_".join(parts[1:-1])
                seat = int(parts[-1])
                route = get_route(route_id)
                if route and book_seat(route_id, seat):
                    send_text(from_number, f"Seat {seat} booked on {route['short_name']}. Amount: Rs {route['price']}. (Next: QR Payment.) ")
                else:
                    left = available_seats(route_id)
                    if left:
                        send_seat_list(from_number, get_route(route_id), left)
                    else:
                        send_text(from_number, "Sorry, that route is fully booked.")
            else:
                seats = available_seats(selected_id)
                if seats:
                    send_seat_list(from_number, get_route(selected_id), seats)
                else:
                    send_text(from_number, "Sorry, this route is fully booked.")
        else:
            logger.info("sending route list to %s", from_number)
            try:
                send_route_list(from_number, load_routes())
            except Exception as send_exc:
                logger.error("failed to send route list: %s", send_exc, exc_info=True)
    except (KeyError, IndexError) as exc:
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
