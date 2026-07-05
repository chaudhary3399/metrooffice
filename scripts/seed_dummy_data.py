from app.bookings import seed_today_services, get_route_services, book_seat, _connect


def main() -> None:
    seed_today_services()
    print("Seeded today services")

    route_ids = ["route_1", "route_2"]
    booked = []
    for route_id in route_ids:
        services = get_route_services(route_id)
        print(f"Route {route_id} active services: {len(services)}")
        for service in services[:2]:
            for seat in (1, 2):
                phone_number = f"919999000{service['id']:02d}{seat}"
                customer_name = f"Test Customer {service['id']}-{seat}"
                result = book_seat(service["id"], seat, phone_number, customer_name)
                status = "booked" if result else "failed"
                print(
                    f"Booking route_service_id={service['id']} seat={seat} phone={phone_number} -> {status}"
                )
                booked.append((service["id"], seat, phone_number, status))

    with _connect() as conn:
        with conn.cursor() as cur:
            print("\nDatabase row counts:")
            for table in ["routes", "drivers", "route_services", "service_seats", "customers", "bookings"]:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                print(table, cur.fetchone()[0])
            print("\nSample bookings:")
            cur.execute(
                "SELECT id, route_id, route_service_id, seat_number, phone_number, customer_name FROM bookings ORDER BY id DESC LIMIT 10"
            )
            for row in cur.fetchall():
                print(row)


if __name__ == "__main__":
    main()
