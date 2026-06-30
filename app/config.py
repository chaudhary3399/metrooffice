"""Loads environment variables and database settings."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
DATABASE_URL = os.getenv("DATABASE_URL", "")
SERVICE_START_HOUR = int(os.getenv("SERVICE_START_HOUR", "7"))
SERVICE_END_HOUR = int(os.getenv("SERVICE_END_HOUR", "19"))
ROUTES_FILE = PROJECT_ROOT / "config" / "routes.yaml"
