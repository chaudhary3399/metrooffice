from fastapi.testclient import TestClient

from app import main


def test_admin_dashboard_requires_credentials_and_serves_page(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_USERNAME", "operations")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "test-only-strong-password")
    client = TestClient(main.app)

    assert client.get("/admin").status_code == 401
    assert client.get("/site/admin.html").status_code == 404
    response = client.get("/admin", auth=("operations", "test-only-strong-password"))
    assert response.status_code == 200
    assert "Today’s rides" in response.text


def test_admin_api_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_USERNAME", "operations")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "test-only-strong-password")
    client = TestClient(main.app)

    response = client.get("/admin/api/services", auth=("operations", "wrong-password"))

    assert response.status_code == 401


def test_admin_api_accepts_selected_service_date(monkeypatch, tmp_path):
    from app import bookings

    monkeypatch.setattr(main, "ADMIN_USERNAME", "operations")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "test-only-strong-password")
    monkeypatch.setattr(bookings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(bookings, "SEAT_DIR", tmp_path / "data" / "seat_inventory")
    client = TestClient(main.app)

    response = client.get(
        "/admin/api/services?service_date=2030-05-06",
        auth=("operations", "test-only-strong-password"),
    )

    assert response.status_code == 200
    assert response.json()["service_date"] == "2030-05-06"
    assert response.json()["services"] == []
