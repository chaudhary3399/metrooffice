from datetime import time, timedelta

import pytest

from app import bookings


def _prepare_store(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(bookings, "DATA_DIR", data_dir)
    monkeypatch.setattr(bookings, "SEAT_DIR", data_dir / "seat_inventory")
    monkeypatch.setattr(bookings, "BOOKINGS_FILE", data_dir / "bookings.json")
    monkeypatch.setattr(bookings, "CUSTOMERS_FILE", data_dir / "customers.json")
    service_date = bookings.local_today() + timedelta(days=1)
    service_id = 712345
    bookings._ensure_service_state(
        service_id,
        route_id="route_1",
        service_date=service_date,
        service_time=time(9, 0),
        capacity=4,
        route_name="Noida to Oxygen Park",
        short_name="Noida → Oxygen Park",
        price=50,
    )
    return service_id, service_date


def test_manifest_tracks_cash_and_online_and_cash_collection(monkeypatch, tmp_path):
    service_id, _ = _prepare_store(monkeypatch, tmp_path)

    assert bookings.book_seats(service_id, 1, "919999999991", "Cash Rider", 50, "cash")[0]
    assert bookings.book_seats(service_id, 1, "919999999992", "Online Rider", 45.5, "online")[0]

    manifest = bookings.get_service_manifest(service_id)
    assert manifest is not None
    assert manifest["cash_due"] == 50
    assert manifest["paid_online"] == 45.5
    assert [row["payment_status"] for row in manifest["passengers"]] == ["cash_due", "paid_online"]

    assert bookings.mark_cash_collected(service_id, 1)
    manifest = bookings.get_service_manifest(service_id)
    assert manifest is not None
    assert manifest["cash_due"] == 0
    assert manifest["cash_collected"] == 50

    with pytest.raises(ValueError, match="Only cash bookings"):
        bookings.mark_cash_collected(service_id, 2)


def test_update_service_changes_saved_departure_and_fare(monkeypatch, tmp_path):
    service_id, service_date = _prepare_store(monkeypatch, tmp_path)

    updated = bookings.update_service(
        service_id,
        {"service_date": service_date.isoformat(), "service_time": "10:30", "price": 75},
    )

    assert updated["service_time"] == time(10, 30)
    assert updated["price"] == 75
    assert bookings.list_services_for_date(service_date)[0]["id"] == service_id


def test_cancelled_service_is_hidden_and_cannot_accept_booking(monkeypatch, tmp_path):
    service_id, service_date = _prepare_store(monkeypatch, tmp_path)
    assert bookings.book_seats(service_id, 1, "919999999991", "Passenger", 50, "cash")[0]

    service, recipients = bookings.cancel_service(service_id)

    assert service["status"] == "cancelled"
    assert recipients == ["919999999991"]
    assert bookings.available_seats(service_id) == []
    assert not bookings.book_seats(service_id, 1, "919999999992", "Late Rider")[0]
    assert bookings.get_route_services("route_1") == []
    assert bookings.list_services_for_date(service_date)[0]["status"] == "cancelled"
