"""Loads environment variables and the routes.yaml config."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")

ROUTES_FILE = PROJECT_ROOT / "config" / "routes.yaml"


def load_routes():
    """Read routes.yaml fresh each call so edits show up on next message."""
    with open(ROUTES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data.get("routes", [])
