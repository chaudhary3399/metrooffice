from datetime import date, datetime, time, timedelta, timezone
from threading import Thread

from app import bookings


def test_same_seat_booking_is_atomic(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    seat_dir = data_dir / "seat_inventory"
    bookings.DATA_DIR = data_dir
    bookings.SEAT_DIR = seat_dir
    bookings.BOOKINGS_FILE = data_dir / "bookings.json"
    bookings.CUSTOMERS_FILE = data_dir / "customers.json"

    service_id = 101
    route_id = "route_1"
    bookings._ensure_service_state(
        service_id,
        route_id=route_id,
        service_date=date.today() + timedelta(days=1),
        service_time=time(9, 0),
        capacity=1,
        route_name="Route 1",
        short_name="R1",
        price=50,
    )

    results = []
    errors = []

    def worker(phone_number):
        try:
            results.append(bookings.book_seats(service_id, 1, phone_number, f"User {phone_number}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        Thread(target=worker, args=("919999999991",)),
        Thread(target=worker, args=("919999999992",)),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sum(1 for success, _, _ in results if success) == 1
    assert bookings.available_seats(service_id) == []


def test_upcoming_service_uses_india_time_not_server_utc():
    # 05:00 UTC is 10:30 in India; a 10:15 service must already be hidden.
    now_utc = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
    assert not bookings.is_service_upcoming(date(2026, 9, 30), time(10, 15), now_utc)
    assert bookings.is_service_upcoming(date(2026, 9, 30), time(10, 45), now_utc)
    assert bookings.is_service_upcoming(date(2026, 10, 1), time(7, 0), now_utc)
