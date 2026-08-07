"""Configuration loaded from .env. Secrets are never hardcoded."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Writable state (data cache, SQLite draft log, token file when not using the
# Keychain). On Render this must point at a mounted disk — everything else on
# the container filesystem is wiped on every deploy.
DATA_DIR = Path(os.getenv("FFB_DATA_DIR", str(ROOT / "data"))).expanduser()

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("YAHOO_REDIRECT_URI", "oob").strip() or "oob"
TOKEN_STORE = os.getenv("FFB_TOKEN_STORE", "keychain").strip().lower()

# Yahoo OAuth2 endpoints — https://developer.yahoo.com/oauth2/guide/flows_authcode/
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

# Our two leagues, from the uploaded Scoring & Settings pages.
# league_key format is {game_key}.l.{league_id}; game_key is resolved at runtime
# so this keeps working across seasons.
KNOWN_LEAGUES = {
    "582600": "The League of Gains & Gains",
    "670028": "League of Legends",
}


class ConfigError(RuntimeError):
    pass


def require_credentials() -> None:
    """Fail loudly and usefully rather than sending an empty client_id to Yahoo."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ConfigError(
            "Missing Yahoo credentials.\n"
            "  1. cp .env.example .env\n"
            "  2. Create an app at https://developer.yahoo.com/apps/create/\n"
            "     Type='Installed Application', Redirect URI='oob',\n"
            "     API Permissions='Fantasy Sports' -> Read\n"
            "  3. Paste the Client ID and Client Secret into .env"
        )
