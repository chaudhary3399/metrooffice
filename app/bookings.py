"""PostgreSQL-backed route, service, and booking store."""
from datetime import date, time
from typing import Dict, List, Optional

import psycopg2
import yaml
from psycopg2.extras import DictCursor

from .config import DATABASE_URL, ROUTES_FILE, SERVICE_END_HOUR, SERVICE_START_HOUR

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in .env to use the Postgres booking store.")


def _connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)


def _load_default_routes() -> List[Dict]:
    if not ROUTES_FILE.exists():
        return []
    with open(ROUTES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data.get("routes", []) if isinstance(data, dict) else []


def _seed_default_routes_and_services() -> None:
    routes = _load_default_routes()
    if not routes:
        return

    today = date.today()
    with _connect() as conn:
        with conn.cursor() as cur:
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

                for hour in range(SERVICE_START_HOUR, SERVICE_END_HOUR + 1):
                    service_time = time(hour, 0)
                    cur.execute(
                        """
                        INSERT INTO route_services (route_id, service_date, service_time, status, capacity, created_at, updated_at)
                        VALUES (%s, %s, %s, 'active', %s, now(), now())
                        ON CONFLICT (route_id, service_date, service_time) DO NOTHING
                        """,
                        (route_id, today, service_time, capacity),
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
                CREATE TABLE IF NOT EXISTS route_services (
                    id SERIAL PRIMARY KEY,
                    route_id TEXT NOT NULL REFERENCES routes(route_id),
                    service_date DATE NOT NULL,
                    service_time TIME NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    capacity INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (route_id, service_date, service_time)
                )
                """
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
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, route_id, service_date, service_time, status, capacity
                FROM route_services
                WHERE route_id = %s AND status = 'active' AND service_date >= current_date
                ORDER BY service_date, service_time
                """,
                (route_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def get_service(route_service_id: int) -> Optional[Dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rs.id, rs.route_id, rs.service_date, rs.service_time, rs.status,
                       rs.capacity, r.short_name, r.price, r.name
                FROM route_services rs
                JOIN routes r ON rs.route_id = r.route_id
                WHERE rs.id = %s
                """,
                (route_service_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


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


def book_seat(route_service_id: int, seat: int, phone_number: str, customer_name: Optional[str] = None) -> bool:
    service = get_service(route_service_id)
    if not service or service["status"] != "active":
        return False

    save_customer(phone_number, customer_name)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM service_seats WHERE route_service_id = %s AND seat_number = %s FOR UPDATE",
                (route_service_id, seat),
            )
            seat_row = cur.fetchone()
            if seat_row:
                if seat_row["status"] != "available":
                    return False
                cur.execute(
                    "UPDATE service_seats SET status = 'booked', updated_at = now() WHERE route_service_id = %s AND seat_number = %s",
                    (route_service_id, seat),
                )
            else:
                if service["capacity"] is None or seat < 1 or seat > service["capacity"]:
                    return False
                cur.execute(
                    "SELECT 1 FROM bookings WHERE route_service_id = %s AND seat_number = %s",
                    (route_service_id, seat),
                )
                if cur.fetchone():
                    return False

            cur.execute(
                """
                INSERT INTO bookings (
                    route_id,
                    route_service_id,
                    service_date,
                    service_time,
                    seat_number,
                    phone_number,
                    customer_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (route_service_id, seat_number) DO NOTHING
                """,
                (
                    service["route_id"],
                    route_service_id,
                    service["service_date"],
                    service["service_time"],
                    seat,
                    phone_number,
                    customer_name,
                ),
            )
            return cur.rowcount == 1
