"""Loads environment variables and route configuration settings."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
RAZORPAY_PAYMENT_LINK = os.getenv("RAZORPAY_PAYMENT_LINK", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "MetroOffice Shuttle")
WHATSAPP_CONTACT_NUMBER = os.getenv("WHATSAPP_CONTACT_NUMBER", "")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
SERVICE_START_HOUR = int(os.getenv("SERVICE_START_HOUR", "7"))
SERVICE_END_HOUR = int(os.getenv("SERVICE_END_HOUR", "19"))
ROUTES_FILE = PROJECT_ROOT / "config" / "routes.yaml"
DATA_DIR = PROJECT_ROOT / "data"
SEAT_DIR = DATA_DIR / "seat_inventory"
BOOKINGS_FILE = DATA_DIR / "bookings.json"
CUSTOMERS_FILE = DATA_DIR / "customers.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"
