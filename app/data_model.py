"""Data model and default values for the file-backed booking system.

Defines the structure and default values for routes, services, bookings, customers,
and seats stored in JSON files under the app data folder.
"""
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, time
from typing import Dict, List, Optional


@dataclass
class BookingRecord:
    """A single seat booking for a passenger on a service."""
    seat_number: int
    phone_number: str
    customer_name: Optional[str] = None
    status: str = "booked"  # booked, cancelled
    amount: int = 0
    booked_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "BookingRecord":
        return BookingRecord(
            seat_number=int(data.get("seat_number", 0)),
            phone_number=data.get("phone_number", ""),
            customer_name=data.get("customer_name"),
            status=data.get("status", "booked"),
            amount=int(data.get("amount", 0)),
            booked_at=data.get("booked_at", datetime.utcnow().isoformat()),
        )


@dataclass
class ServiceState:
    """Runtime state for a single scheduled service (route + time + date)."""
    id: int
    route_id: str
    service_date: str  # ISO format date
    service_time: str  # HH:MM:SS format
    status: str = "active"  # active, cancelled
    capacity: int = 4
    route_name: str = ""
    short_name: str = ""
    price: int = 0
    driver_id: Optional[int] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    last_driver_reminder_at: Optional[str] = None
    bookings: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "route_id": self.route_id,
            "service_date": self.service_date,
            "service_time": self.service_time,
            "status": self.status,
            "capacity": self.capacity,
            "route_name": self.route_name,
            "short_name": self.short_name,
            "price": self.price,
            "driver_id": self.driver_id,
            "driver_name": self.driver_name,
            "driver_phone": self.driver_phone,
            "last_driver_reminder_at": self.last_driver_reminder_at,
            "bookings": self.bookings,
        }

    @staticmethod
    def from_dict(data: Dict) -> "ServiceState":
        bookings = data.get("bookings", [])
        if bookings and isinstance(bookings[0], dict):
            bookings = [b if isinstance(b, dict) else b.to_dict() for b in bookings]
        return ServiceState(
            id=int(data.get("id", 0)),
            route_id=data.get("route_id", ""),
            service_date=data.get("service_date", date.today().isoformat()),
            service_time=data.get("service_time", "00:00:00"),
            status=data.get("status", "active"),
            capacity=int(data.get("capacity", 4)),
            route_name=data.get("route_name", ""),
            short_name=data.get("short_name", ""),
            price=int(data.get("price", 0)),
            driver_id=data.get("driver_id"),
            driver_name=data.get("driver_name"),
            driver_phone=data.get("driver_phone"),
            last_driver_reminder_at=data.get("last_driver_reminder_at"),
            bookings=bookings,
        )


@dataclass
class CustomerRecord:
    """A customer who has used the booking service."""
    phone_number: str
    customer_name: Optional[str] = None
    last_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict:
        return {
            "phone_number": self.phone_number,
            "customer_name": self.customer_name,
            "last_seen": self.last_seen,
        }

    @staticmethod
    def from_dict(data: Dict) -> "CustomerRecord":
        return CustomerRecord(
            phone_number=data.get("phone_number", ""),
            customer_name=data.get("customer_name"),
            last_seen=data.get("last_seen", datetime.utcnow().isoformat()),
        )


# Default route configuration (used if routes.yaml is missing or incomplete)
DEFAULT_ROUTES = [
    {
        "id": "route_1",
        "name": "Noida 15 Metro Station to Oxygen Park Office",
        "short_name": "Noida 15 → Oxygen Park",
        "price": 50,
        "total_seats": 4,
    },
    {
        "id": "route_2",
        "name": "Oxygen Park Office to Noida 15 Metro Station",
        "short_name": "Oxygen Park → Noida 15",
        "price": 50,
        "total_seats": 4,
    },
]

# Default service hours (7 AM to 7 PM, one per hour)
DEFAULT_SERVICE_HOURS = [
    {"hour": 7, "capacity": 4},
    {"hour": 8, "capacity": 4},
    {"hour": 9, "capacity": 4},
    {"hour": 10, "capacity": 4},
    {"hour": 11, "capacity": 4},
    {"hour": 12, "capacity": 4},
    {"hour": 13, "capacity": 4},
    {"hour": 14, "capacity": 4},
    {"hour": 15, "capacity": 4},
    {"hour": 16, "capacity": 4},
    {"hour": 17, "capacity": 4},
    {"hour": 18, "capacity": 4},
    {"hour": 19, "capacity": 4},
]

# Default driver data (for reminders and booking confirmation)
DEFAULT_DRIVERS = [
    {
        "id": 1,
        "driver_name": "Rahul",
        "vehicle_type": "Ertiga",
        "vehicle_number": "UP15DW1234",
        "phone_number": "9876543210",
    },
    {
        "id": 2,
        "driver_name": "Amit",
        "vehicle_type": "Tata Tiago",
        "vehicle_number": "UP16AB5678",
        "phone_number": "9988776655",
    },
    {
        "id": 3,
        "driver_name": "Suresh",
        "vehicle_type": "Tempo Traveller",
        "vehicle_number": "UP20CD2468",
        "phone_number": "8765432109",
    },
]

# Default configuration
DEFAULT_CONFIG = {
    "service_start_hour": 7,
    "service_end_hour": 19,
    "max_seats_per_booking": 4,
    "webhook_retry_dedupe_window_seconds": 3600,
    "driver_reminder_lead_time_minutes": 15,
}
