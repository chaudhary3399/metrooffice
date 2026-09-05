"""File-backed route, service, and booking store.

This project intentionally avoids a database dependency. Route definitions live in YAML,
while service seat availability and bookings are stored in JSON under the app data folder.
The file lock ensures two users cannot reserve the same seat at the same time.
"""
import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Event, Thread
from time import sleep
from typing import Dict, Iterable, List, Optional
import fcntl
import yaml

from .config import DATA_DIR, ROUTES_FILE, SEAT_DIR, BOOKINGS_FILE, CUSTOMERS_FILE, SERVICE_END_HOUR, SERVICE_START_HOUR
from .data_model import (
    BookingRecord,
    ServiceState,
    CustomerRecord,
    DEFAULT_ROUTES,
    DEFAULT_SERVICE_HOURS,
    DEFAULT_DRIVERS,
    DEFAULT_CONFIG,
)
from .whatsapp import send_text

logger = logging.getLogger(__name__)

_driver_reminder_thread: Optional[Thread] = None
_driver_reminder_stop_event: Optional[Event] = None


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEAT_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json_atomic(path: Path, payload) -> None:
    _ensure_data_dir()
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    temp_path.replace(path)


def _service_state_path(service_id: int) -> Path:
    return SEAT_DIR / f"service_{service_id}.json"


def _service_lock_path(service_id: int) -> Path:
    return SEAT_DIR / f"service_{service_id}.lock"


@contextmanager
def _service_lock(service_id: int):
    _ensure_data_dir()
    lock_path = _service_lock_path(service_id)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def normalize_phone_number(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return phone or ""
    if len(digits) == 10:
        return f"91{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"91{digits[1:]}"
    if len(digits) > 10 and digits.startswith("91"):
        return digits
    return digits


def _load_default_routes() -> List[Dict]:
    if not ROUTES_FILE.exists():
        return DEFAULT_ROUTES
    with open(ROUTES_FILE, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if isinstance(data, dict):
        routes = data.get("routes", [])
        if routes:
            return routes
    return DEFAULT_ROUTES


def _service_hours_for_route(route: Dict) -> List[time]:
    service_entries = route.get("service_hours")
    if isinstance(service_entries, list) and service_entries:
        hours = []
        for entry in service_entries:
            hour = entry.get("hour") if isinstance(entry, dict) else None
            if isinstance(hour, int) and 0 <= hour <= 23:
                hours.append(time(hour, 0))
        if hours:
            return hours

    total_seats = route.get("total_seats")
    if total_seats is None:
        return []
    
    # Use DEFAULT_SERVICE_HOURS if available, otherwise generate range
    if DEFAULT_SERVICE_HOURS:
        return [time(entry["hour"], 0) for entry in DEFAULT_SERVICE_HOURS if isinstance(entry, dict) and "hour" in entry]
    return [time(hour, 0) for hour in range(SERVICE_START_HOUR, SERVICE_END_HOUR + 1)]


def _service_id_for(route_id: str, service_date: date, service_time: time) -> int:
    key = f"{route_id}|{service_date.isoformat()}|{service_time.strftime('%H:%M:%S')}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _build_service_snapshot(service_id: int, route_id: str, service_date: date, service_time: time, capacity: int, route_name: str, short_name: str, price: int) -> Dict:
    state = ServiceState(
        id=service_id,
        route_id=route_id,
        service_date=service_date.isoformat(),
        service_time=service_time.strftime("%H:%M:%S"),
        status="active",
        capacity=int(capacity),
        route_name=route_name,
        short_name=short_name,
        price=int(price),
        driver_id=None,
        driver_name=None,
        driver_phone=None,
        last_driver_reminder_at=None,
        bookings=[],
    )
    return state.to_dict()


def _ensure_service_state(
    service_id: int,
    route_id: str,
    service_date: date,
    service_time: time,
    capacity: int,
    route_name: Optional[str] = None,
    short_name: Optional[str] = None,
    price: int = 0,
) -> Dict:
    _ensure_data_dir()
    path = _service_state_path(service_id)
    payload = _read_json(path, None)
    if payload is None:
        payload = _build_service_snapshot(service_id, route_id, service_date, service_time, capacity, route_name or route_id, short_name or route_id, price)
        _write_json_atomic(path, payload)
        return payload

    payload["route_id"] = route_id
    payload["service_date"] = payload.get("service_date") or service_date.isoformat()
    payload["service_time"] = payload.get("service_time") or service_time.strftime("%H:%M:%S")
    payload["capacity"] = int(payload.get("capacity") or capacity)
    payload["route_name"] = payload.get("route_name") or route_name or route_id
    payload["short_name"] = payload.get("short_name") or short_name or route_id
    payload["price"] = int(payload.get("price") or price)
    payload["status"] = payload.get("status", "active")
    payload["bookings"] = payload.get("bookings", [])
    _write_json_atomic(path, payload)
    return payload


def _load_customer_store() -> Dict:
    data = _read_json(CUSTOMERS_FILE, {})
    if isinstance(data, dict):
        return data
    return {}


def seed_today_services() -> None:
    """Create the day’s route service files from the YAML config."""
    routes = _load_default_routes()
    for route in routes:
        route_id = route.get("id")
        if not route_id:
            continue
        capacity = route.get("total_seats")
        if capacity is None:
            continue
        route_name = route.get("name") or route_id
        short_name = route.get("short_name") or route_id
        price = route.get("price") or 0
        for service_time in _service_hours_for_route(route):
            service_id = _service_id_for(route_id, date.today(), service_time)
            _ensure_service_state(service_id, route_id, date.today(), service_time, capacity, route_name, short_name, price)


def get_routes() -> List[Dict]:
    routes = _load_default_routes()
    normalized = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_id = route.get("route_id") or route.get("id")
        if not route_id:
            continue
        normalized.append({
            "route_id": route_id,
            "name": route.get("name") or route.get("short_name") or route_id,
            "short_name": route.get("short_name") or route.get("name") or route_id,
            "price": route.get("price") or 0,
        })
    return normalized


def get_route(route_id: str) -> Optional[Dict]:
    for route in get_routes():
        if route.get("route_id") == route_id:
            return route
    return None


def get_route_services(route_id: str) -> List[Dict]:
    routes = _load_default_routes()
    route = next((item for item in routes if str(item.get("id") or item.get("route_id")) == str(route_id)), None)
    if route is None:
        return []

    services = []
    for service_time in _service_hours_for_route(route):
        service_id = _service_id_for(route_id, date.today(), service_time)
        state = _ensure_service_state(service_id, route_id, date.today(), service_time, route.get("total_seats") or 0, route.get("name") or route_id, route.get("short_name") or route_id, route.get("price") or 0)
        services.append({
            "id": state["id"],
            "route_id": state["route_id"],
            "service_date": date.fromisoformat(state["service_date"]),
            "service_time": datetime.strptime(state["service_time"], "%H:%M:%S").time(),
            "status": state.get("status", "active"),
            "capacity": state.get("capacity"),
            "short_name": state.get("short_name"),
            "price": state.get("price"),
            "name": state.get("route_name"),
        })
    return [service for service in services if service["service_time"] >= datetime.now().time() or service["service_date"] > date.today()]


def get_service(route_service_id: int) -> Optional[Dict]:
    path = _service_state_path(route_service_id)
    if not path.exists():
        return None

    state = _read_json(path, {})
    if not state:
        return None

    route = get_route(state.get("route_id") or "")
    if route is None:
        route = {
            "route_id": state.get("route_id"),
            "name": state.get("route_name") or state.get("route_id"),
            "short_name": state.get("short_name") or state.get("route_id"),
            "price": state.get("price") or 0,
        }

    service_date = state.get("service_date")
    service_time = state.get("service_time")
    try:
        parsed_date = date.fromisoformat(service_date) if service_date else date.today()
    except ValueError:
        parsed_date = date.today()
    try:
        parsed_time = datetime.strptime(service_time, "%H:%M:%S").time() if service_time else time(0, 0)
    except ValueError:
        parsed_time = time(0, 0)

    return {
        "id": state.get("id") or route_service_id,
        "route_id": state.get("route_id") or route.get("route_id"),
        "service_date": parsed_date,
        "service_time": parsed_time,
        "status": state.get("status", "active"),
        "capacity": state.get("capacity"),
        "driver_id": state.get("driver_id"),
        "driver_name": state.get("driver_name"),
        "driver_phone": state.get("driver_phone"),
        "short_name": state.get("short_name") or route.get("short_name") or route.get("name"),
        "price": state.get("price") or route.get("price") or 0,
        "name": state.get("route_name") or route.get("name") or route.get("route_id"),
        "last_driver_reminder_at": state.get("last_driver_reminder_at"),
    }


def save_customer(phone_number: str, customer_name: Optional[str] = None) -> None:
    data = _load_customer_store()
    customer = CustomerRecord(
        phone_number=phone_number,
        customer_name=customer_name or data.get(phone_number, {}).get("customer_name"),
        last_seen=datetime.utcnow().isoformat(),
    )
    data[phone_number] = customer.to_dict()
    _write_json_atomic(CUSTOMERS_FILE, data)


def _read_service_state(service_id: int) -> Optional[Dict]:
    path = _service_state_path(service_id)
    if not path.exists():
        return None
    data = _read_json(path, {})
    if not isinstance(data, dict):
        return None
    data.setdefault("bookings", [])
    return data


def available_seats(route_service_id: int) -> List[int]:
    service = get_service(route_service_id)
    if not service or service["status"] != "active":
        return []

    state = _read_service_state(route_service_id)
    if state is None:
        return []

    booked = {int(booking["seat_number"]) for booking in state.get("bookings", []) if booking.get("status") != "cancelled"}
    return [n for n in range(1, int(service["capacity"] or 0) + 1) if n not in booked]


def update_booking_passenger_details(
    route_service_id: int,
    seat_numbers: list[int],
    original_phone: str,
    new_phone: str,
    new_name: Optional[str] = None,
) -> bool:
    if not seat_numbers:
        return False

    with _service_lock(route_service_id):
        state = _read_service_state(route_service_id)
        if state is None:
            return False

        updated = False
        for booking in state.get("bookings", []):
            seat = booking.get("seat_number")
            if seat in seat_numbers and booking.get("phone_number") == original_phone:
                booking["phone_number"] = new_phone
                booking["customer_name"] = new_name or booking.get("customer_name")
                updated = True

        if updated:
            _write_json_atomic(_service_state_path(route_service_id), state)
            save_customer(new_phone, new_name)
        return updated


def book_seats(route_service_id: int, seat_count: int, phone_number: str, customer_name: Optional[str] = None) -> tuple[bool, list[int], int]:
    service = get_service(route_service_id)
    if not service or service["status"] != "active":
        return False, [], 0

    if seat_count < 1 or seat_count > DEFAULT_CONFIG.get("max_seats_per_booking", 4):
        return False, [], 0

    now = datetime.now()
    try:
        if service.get("service_date") == now.date() and service.get("service_time") <= now.time():
            return False, [], 0
    except Exception:
        return False, [], 0

    save_customer(phone_number, customer_name)
    with _service_lock(route_service_id):
        state = _read_service_state(route_service_id)
        if state is None:
            return False, [], 0

        bookings = state.get("bookings", [])
        booked_seats = {int(item["seat_number"]) for item in bookings if item.get("status") != "cancelled"}
        available = [n for n in range(1, int(service["capacity"] or 0) + 1) if n not in booked_seats]
        if len(available) < seat_count:
            return False, [], 0

        selected = available[:seat_count]
        for seat in selected:
            booking = BookingRecord(
                seat_number=int(seat),
                phone_number=phone_number,
                customer_name=customer_name,
                status="booked",
                amount=int(service.get("price") or 0),
            )
            bookings.append(booking.to_dict())

        state["bookings"] = bookings
        _write_json_atomic(_service_state_path(route_service_id), state)

    total_amount = int(service.get("price") or 0) * len(selected)
    return True, selected, total_amount


def book_seat(route_service_id: int, seat: int, phone_number: str, customer_name: Optional[str] = None) -> bool:
    success, _, _ = book_seats(route_service_id, 1, phone_number, customer_name)
    return success


def build_driver_reminder_message(service: Dict, passengers: List[Dict]) -> str:
    route_name = service.get("short_name") or service.get("name") or "Route"
    departure_time = service["service_time"].strftime("%I:%M %p").lstrip("0")
    departure_date = service["service_date"].strftime("%b %d")
    header = f"Reminder: {len(passengers)} passenger(s) for {route_name} on {departure_date} at {departure_time}."
    lines = [header]
    for idx, passenger in enumerate(passengers, start=1):
        name = passenger.get("customer_name") or "Passenger"
        phone = passenger.get("phone_number") or "-"
        lines.append(f"{idx}. {name} - {phone}")
    return "\n".join(lines)


def send_driver_reminder(route_service_id: int) -> bool:
    service = get_service(route_service_id)
    if not service:
        return False

    state = _read_service_state(route_service_id)
    passengers = [
        {"customer_name": item.get("customer_name"), "phone_number": item.get("phone_number")}
        for item in state.get("bookings", []) if item.get("status") == "booked"
    ]
    if not passengers:
        return False

    driver_phone = service.get("driver_phone")
    if not driver_phone:
        return False

    try:
        send_text(normalize_phone_number(driver_phone), build_driver_reminder_message(service, passengers))
    except Exception:
        return False

    state["last_driver_reminder_at"] = datetime.utcnow().isoformat()
    _write_json_atomic(_service_state_path(route_service_id), state)
    return True


def process_driver_reminders(now: Optional[datetime] = None) -> int:
    if now is None:
        now = datetime.now()

    sent = 0
    for route in get_routes():
        for service_row in get_route_services(route["route_id"]):
            service = get_service(service_row["id"])
            if not service or service["status"] != "active":
                continue
            departure_dt = datetime.combine(service["service_date"], service["service_time"])  # type: ignore[arg-type]
            remind_at = departure_dt - timedelta(minutes=15)
            if remind_at <= now < departure_dt:
                if send_driver_reminder(service["id"]):
                    sent += 1
    return sent


def start_driver_reminder_worker(interval_seconds: int = 60) -> None:
    global _driver_reminder_thread, _driver_reminder_stop_event
    if _driver_reminder_thread is not None and _driver_reminder_thread.is_alive():
        return

    _driver_reminder_stop_event = Event()
    _driver_reminder_thread = Thread(target=_driver_reminder_loop, args=(interval_seconds,), daemon=True)
    _driver_reminder_thread.start()


def stop_driver_reminder_worker() -> None:
    global _driver_reminder_stop_event
    if _driver_reminder_stop_event is not None:
        _driver_reminder_stop_event.set()


def _driver_reminder_loop(interval_seconds: int) -> None:
    while _driver_reminder_stop_event is None or not _driver_reminder_stop_event.is_set():
        process_driver_reminders()
        sleep(interval_seconds)


def database_available() -> bool:
    """The file-backed store is always available while the app is running."""
    return True


def initialize_database() -> None:
    """Compatibility no-op kept for older imports and startup code."""
    seed_today_services()
