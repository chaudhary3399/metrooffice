"""PostgreSQL-backed route, service, and booking store."""
from datetime import date, datetime, time, timedelta
from threading import Event, Thread
from time import sleep
from typing import Dict, List, Optional

import psycopg2
import yaml
from psycopg2.extras import DictCursor

from .config import DATABASE_URL, ROUTES_FILE, SERVICE_END_HOUR, SERVICE_START_HOUR
from .whatsapp import send_text

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in .env to use the Postgres booking store.")

_driver_reminder_thread: Optional[Thread] = None
_driver_reminder_stop_event: Optional[Event] = None


def _connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)


def _load_default_routes() -> List[Dict]:
    if not ROUTES_FILE.exists():
        return []
    with open(ROUTES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data.get("routes", []) if isinstance(data, dict) else []


def _seed_default_drivers(cur) -> list:
    default_drivers = [
        {
            "driver_name": "Rahul",
            "vehicle_type": "Ertiga",
            "vehicle_number": "UP15DW1234",
            "phone_number": "1234567890",
        },
        {
            "driver_name": "Amit",
            "vehicle_type": "Tata Tiago",
            "vehicle_number": "UP16AB5678",
            "phone_number": "9876543210",
        },
        {
            "driver_name": "Suresh",
            "vehicle_type": "Tempo Traveller",
            "vehicle_number": "UP20CD2468",
            "phone_number": "9988776655",
        },
    ]
    driver_ids = []
    for driver in default_drivers:
        cur.execute(
            """
            INSERT INTO drivers (driver_name, vehicle_type, vehicle_number, phone_number, created_at, updated_at)
            VALUES (%s, %s, %s, %s, now(), now())
            ON CONFLICT (vehicle_number) DO UPDATE
            SET driver_name = EXCLUDED.driver_name,
                vehicle_type = EXCLUDED.vehicle_type,
                phone_number = EXCLUDED.phone_number,
                updated_at = now()
            RETURNING id
            """,
            (
                driver["driver_name"],
                driver["vehicle_type"],
                driver["vehicle_number"],
                driver["phone_number"],
            ),
        )
        row = cur.fetchone()
        if row:
            driver_ids.append(row["id"])
    return driver_ids


def _seed_default_routes_and_services() -> None:
    routes = _load_default_routes()
    if not routes:
        return

    today = date.today()
    with _connect() as conn:
        with conn.cursor() as cur:
            driver_ids = _seed_default_drivers(cur)

            for route in routes:
                route_id = route.get("id")
                if not route_id:
                    continue
                name = route.get("name", "")
                short_name = route.get("short_name", "")
                price = route.get("price", 0)
                capacity = route.get("total_seats")

                cur.execute(
                    """
                    INSERT INTO routes (route_id, name, short_name, price, active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, TRUE, now(), now())
                    ON CONFLICT (route_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        short_name = EXCLUDED.short_name,
                        price = EXCLUDED.price,
                        updated_at = now()
                    """,
                    (route_id, name, short_name, price),
                )

                if capacity is None:
                    continue

                service_entries = route.get("service_hours")
                if isinstance(service_entries, list) and service_entries:
                    schedule = []
                    for entry in service_entries:
                        hour = entry.get("hour")
                        cap = entry.get("capacity", capacity)
                        if isinstance(hour, int) and 0 <= hour <= 23:
                            schedule.append((time(hour, 0), cap))
                else:
                    schedule = [(time(hour, 0), capacity) for hour in range(SERVICE_START_HOUR, SERVICE_END_HOUR + 1)]

                for service_time, service_capacity in schedule:
                    driver_id = None
                    if driver_ids:
                        driver_id = driver_ids[(service_time.hour - SERVICE_START_HOUR) % len(driver_ids)]
                    cur.execute(
                        """
                        INSERT INTO route_services (route_id, service_date, service_time, status, capacity, driver_id, created_at, updated_at)
                        VALUES (%s, %s, %s, 'active', %s, %s, now(), now())
                        ON CONFLICT (route_id, service_date, service_time) DO UPDATE
                        SET driver_id = EXCLUDED.driver_id,
                            status = EXCLUDED.status,
                            capacity = EXCLUDED.capacity,
                            updated_at = now()
                        """,
                        (route_id, today, service_time, service_capacity, driver_id),
                    )

            cur.execute(
                "SELECT id, route_id, capacity FROM route_services WHERE service_date = %s",
                (today,),
            )
            services = cur.fetchall()
            for service_row in services:
                route_service_id = service_row["id"]
                cap = service_row["capacity"]
                if cap is None:
                    continue
                cur.execute(
                    "SELECT COUNT(*) FROM service_seats WHERE route_service_id = %s",
                    (route_service_id,),
                )
                seat_count = cur.fetchone()[0]
                if seat_count == 0:
                    for seat_num in range(1, cap + 1):
                        cur.execute(
                            """
                            INSERT INTO service_seats (route_service_id, seat_number, status, updated_at)
                            VALUES (%s, %s, 'available', now())
                            ON CONFLICT (route_service_id, seat_number) DO NOTHING
                            """,
                            (route_service_id, seat_num),
                        )


def seed_today_services() -> None:
    """Seed today’s routes and hourly services from the default route config."""
    _seed_default_routes_and_services()


def _init_db() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS routes (
                    route_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    short_name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS drivers (
                    id SERIAL PRIMARY KEY,
                    driver_name TEXT NOT NULL,
                    vehicle_type TEXT NOT NULL,
                    vehicle_number TEXT NOT NULL UNIQUE,
                    phone_number TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS route_services (
                    id SERIAL PRIMARY KEY,
                    route_id TEXT NOT NULL REFERENCES routes(route_id),
                    service_date DATE NOT NULL,
                    service_time TIME NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    capacity INTEGER,
                    driver_id INTEGER REFERENCES drivers(id),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (route_id, service_date, service_time)
                )
                """
            )
            cur.execute(
                "ALTER TABLE route_services ADD COLUMN IF NOT EXISTS driver_id INTEGER REFERENCES drivers(id)",
            )
            cur.execute(
                "ALTER TABLE route_services ADD COLUMN IF NOT EXISTS last_driver_reminder_at TIMESTAMPTZ",
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    phone_number TEXT PRIMARY KEY,
                    customer_name TEXT,
                    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS service_seats (
                    id SERIAL PRIMARY KEY,
                    route_service_id INTEGER NOT NULL REFERENCES route_services(id) ON DELETE CASCADE,
                    seat_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (route_service_id, seat_number)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    id SERIAL PRIMARY KEY,
                    route_id TEXT NOT NULL REFERENCES routes(route_id),
                    route_service_id INTEGER NOT NULL REFERENCES route_services(id),
                    service_date DATE NOT NULL,
                    service_time TIME NOT NULL,
                    seat_number INTEGER NOT NULL,
                    driver_id INTEGER REFERENCES drivers(id),
                    phone_number TEXT NOT NULL REFERENCES customers(phone_number),
                    customer_name TEXT,
                    amount INTEGER,
                    status TEXT NOT NULL DEFAULT 'booked',
                    payment_ref TEXT,
                    booked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (route_service_id, seat_number)
                )
                """
            )
            cur.execute(
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS driver_id INTEGER REFERENCES drivers(id)",
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_route_services_route_date_time
                ON route_services(route_id, service_date, service_time)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_service_seats_route_service
                ON service_seats(route_service_id)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bookings_route_service
                ON bookings(route_service_id)
                """
            )
    _seed_default_routes_and_services()


_init_db()


def get_routes() -> List[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT route_id, name, short_name, price FROM routes WHERE active = TRUE ORDER BY route_id"
            )
            return [dict(row) for row in cur.fetchall()]


def get_route(route_id: str) -> Optional[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT route_id, name, short_name, price, active FROM routes WHERE route_id = %s",
                (route_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_route_services(route_id: str) -> List[Dict]:
    now = datetime.now()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, route_id, service_date, service_time, status, capacity
                FROM route_services
                WHERE route_id = %s AND status = 'active' AND (
                    service_date > current_date OR (
                        service_date = current_date AND service_time > %s
                    )
                )
                ORDER BY service_date, service_time
                """,
                (route_id, now.time()),
            )
            return [dict(row) for row in cur.fetchall()]


def get_service(route_service_id: int) -> Optional[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rs.id, rs.route_id, rs.service_date, rs.service_time, rs.status,
                       rs.capacity, rs.driver_id, rs.last_driver_reminder_at, r.short_name, r.price, r.name,
                       d.driver_name, d.vehicle_type, d.vehicle_number, d.phone_number AS driver_phone
                FROM route_services rs
                JOIN routes r ON rs.route_id = r.route_id
                LEFT JOIN drivers d ON rs.driver_id = d.id
                WHERE rs.id = %s
                """,
                (route_service_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


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

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT customer_name, phone_number FROM bookings WHERE route_service_id = %s AND status = 'booked' ORDER BY id",
                (route_service_id,),
            )
            passengers = [dict(row) for row in cur.fetchall()]

    if not passengers:
        return False

    driver_phone = service.get("driver_phone")
    if not driver_phone:
        return False

    try:
        send_text(normalize_phone_number(driver_phone), build_driver_reminder_message(service, passengers))
    except Exception:
        return False

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE route_services SET last_driver_reminder_at = now(), updated_at = now() WHERE id = %s",
                (route_service_id,),
            )
    return True


def process_driver_reminders(now: Optional[datetime] = None) -> int:
    if now is None:
        now = datetime.now()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rs.id, rs.route_id, rs.service_date, rs.service_time, rs.status,
                       rs.capacity, rs.driver_id, rs.last_driver_reminder_at, r.short_name, r.price, r.name,
                       d.driver_name, d.vehicle_type, d.vehicle_number, d.phone_number AS driver_phone
                FROM route_services rs
                JOIN routes r ON rs.route_id = r.route_id
                LEFT JOIN drivers d ON rs.driver_id = d.id
                WHERE rs.status = 'active' AND rs.last_driver_reminder_at IS NULL
                """
            )
            services = [dict(row) for row in cur.fetchall()]

    sent = 0
    for service in services:
        departure_dt = datetime.combine(service["service_date"], service["service_time"])
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


def save_customer(phone_number: str, customer_name: Optional[str] = None) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customers (phone_number, customer_name, last_seen)
                VALUES (%s, %s, now())
                ON CONFLICT (phone_number) DO UPDATE
                SET customer_name = COALESCE(EXCLUDED.customer_name, customers.customer_name),
                    last_seen = now()
                """,
                (phone_number, customer_name),
            )


def available_seats(route_service_id: int) -> List[int]:
    service = get_service(route_service_id)
    if not service or service["status"] != "active":
        return []

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seat_number, status FROM service_seats WHERE route_service_id = %s ORDER BY seat_number",
                (route_service_id,),
            )
            rows = cur.fetchall()
            if rows:
                return [row["seat_number"] for row in rows if row["status"] == "available"]

            if service["capacity"] is None:
                return []

            cur.execute(
                "SELECT seat_number FROM bookings WHERE route_service_id = %s",
                (route_service_id,),
            )
            booked = {row["seat_number"] for row in cur.fetchall()}
            return [n for n in range(1, service["capacity"] + 1) if n not in booked]


def update_booking_passenger_details(
    route_service_id: int,
    seat_numbers: list[int],
    original_phone: str,
    new_phone: str,
    new_name: Optional[str] = None,
) -> bool:
    if not seat_numbers:
        return False

    save_customer(new_phone, new_name)
    placeholders = ", ".join(["%s"] * len(seat_numbers))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE bookings
                SET phone_number = %s,
                    customer_name = %s
                WHERE route_service_id = %s
                  AND seat_number IN ({placeholders})
                  AND phone_number = %s
                  AND status = 'booked'
                """,
                [new_phone, new_name, route_service_id, *seat_numbers, original_phone],
            )
            return cur.rowcount > 0


def book_seats(route_service_id: int, seat_count: int, phone_number: str, customer_name: Optional[str] = None) -> tuple[bool, list[int], int]:
    service = get_service(route_service_id)
    if not service or service["status"] != "active":
        return False, [], 0

    if seat_count < 1 or seat_count > 4:
        return False, [], 0

    now = datetime.now()
    try:
        svc_date = service.get("service_date")
        svc_time = service.get("service_time")
        if svc_date == now.date() and svc_time <= now.time():
            return False, [], 0
    except Exception:
        return False, [], 0

    save_customer(phone_number, customer_name)

    selected_seats = []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seat_number
                FROM service_seats
                WHERE route_service_id = %s AND status = 'available'
                ORDER BY seat_number
                LIMIT %s
                FOR UPDATE
                """,
                (route_service_id, seat_count),
            )
            seat_rows = cur.fetchall()
            if len(seat_rows) < seat_count:
                return False, [], 0

            selected_seats = [row["seat_number"] for row in seat_rows]
            for seat in selected_seats:
                cur.execute(
                    "UPDATE service_seats SET status = 'booked', updated_at = now() WHERE route_service_id = %s AND seat_number = %s",
                    (route_service_id, seat),
                )
                cur.execute(
                    """
                    INSERT INTO bookings (
                        route_id,
                        route_service_id,
                        service_date,
                        service_time,
                        seat_number,
                        driver_id,
                        phone_number,
                        customer_name
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (route_service_id, seat_number) DO NOTHING
                    """,
                    (
                        service["route_id"],
                        route_service_id,
                        service["service_date"],
                        service["service_time"],
                        seat,
                        service.get("driver_id"),
                        phone_number,
                        customer_name,
                    ),
                )

    price_per_seat = service.get("price") or 0
    total_amount = price_per_seat * len(selected_seats)
    return True, selected_seats, total_amount


def book_seat(route_service_id: int, seat: int, phone_number: str, customer_name: Optional[str] = None) -> bool:
    success, _, _ = book_seats(route_service_id, 1, phone_number, customer_name)
    return success
