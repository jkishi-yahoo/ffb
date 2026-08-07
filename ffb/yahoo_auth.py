"""Yahoo OAuth2 authorization-code flow.

Per https://developer.yahoo.com/oauth2/guide/flows_authcode/ the token exchange
authenticates with an HTTP Basic header of base64(client_id:client_secret), and
the body carries grant_type/redirect_uri/code. Access tokens live 1 hour; we
refresh automatically using the stored refresh token.
"""
import base64
import time
import urllib.parse
from typing import Optional

import httpx

from . import config, tokens

# Refresh this many seconds before actual expiry, so a long request can't
# straddle the boundary and 401 mid-flight.
EXPIRY_SKEW = 120


def _basic_auth_header() -> str:
    raw = "{}:{}".format(config.CLIENT_ID, config.CLIENT_SECRET).encode()
    return "Basic " + base64.b64encode(raw).decode()


def authorize_url(state: Optional[str] = None) -> str:
    config.require_credentials()
    params = {
        "client_id": config.CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
    }
    if state:
        params["state"] = state
    return config.AUTH_URL + "?" + urllib.parse.urlencode(params)


def _stamp(payload: dict) -> dict:
    """Add an absolute expiry so we don't have to track when the call happened."""
    payload = dict(payload)
    payload["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    return payload


def exchange_code(code: str) -> dict:
    config.require_credentials()
    resp = httpx.post(
        config.TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "redirect_uri": config.REDIRECT_URI,
            "code": code.strip(),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "Yahoo token exchange failed ({}): {}\n"
            "Common causes: the code was already used, it expired (they are "
            "short-lived — paste it promptly), or the Redirect URI in .env does "
            "not exactly match the one on your Yahoo app.".format(
                resp.status_code, resp.text
            )
        )
    payload = _stamp(resp.json())
    tokens.save(payload)
    return payload


def refresh(refresh_token: str) -> dict:
    config.require_credentials()
    resp = httpx.post(
        config.TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "redirect_uri": config.REDIRECT_URI,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "Token refresh failed ({}): {}\nRe-run `python -m ffb.cli login`.".format(
                resp.status_code, resp.text
            )
        )
    payload = _stamp(resp.json())
    # Yahoo may omit refresh_token on refresh; keep the one we already have.
    if not payload.get("refresh_token"):
        payload["refresh_token"] = refresh_token
    tokens.save(payload)
    return payload


def valid_access_token() -> str:
    """Return a live access token, refreshing if it is near expiry."""
    stored = tokens.load()
    if not stored:
        raise RuntimeError("Not logged in. Run: python -m ffb.cli login")
    if time.time() >= stored.get("expires_at", 0) - EXPIRY_SKEW:
        stored = refresh(stored["refresh_token"])
    return stored["access_token"]
