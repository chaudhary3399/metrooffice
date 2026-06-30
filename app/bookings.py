"""Simple file-based seat booking store."""
import json
from pathlib import Path

from .config import PROJECT_ROOT, load_routes

BOOKINGS_FILE = PROJECT_ROOT / "bookings.json"


def _load() -> dict:
    if BOOKINGS_FILE.exists():
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_route(route_id: str):
    for route in load_routes():
        if route["id"] == route_id:
            return route
    return None


def booked_seats(route_id: str) -> list:
    return _load().get(route_id, [])


def available_seats(route_id: str) -> list:
    route = get_route(route_id)
    if not route:
        return []
    taken = set(booked_seats(route_id))
    return [n for n in range(1, route["total_seats"] + 1) if n not in taken]


def book_seat(route_id: str, seat: int) -> bool:
    data = _load()
    taken = data.get(route_id, [])
    if seat in taken:
        return False
    taken.append(seat)
    data[route_id] = taken
    _save(data)
    return True
