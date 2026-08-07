"""OAuth token persistence.

Default backend is the macOS Keychain via `keyring`, so tokens never touch the
repo. The 'file' backend is a fallback for headless use and writes 0600.
"""
import json
import os
import stat
from pathlib import Path
from typing import Optional

from . import config

SERVICE = "ffb-yahoo-fantasy"
ACCOUNT = "default"
FILE_PATH = config.DATA_DIR / ".ffb_tokens.json"


def _use_keychain() -> bool:
    return config.TOKEN_STORE != "file"


def save(tokens: dict) -> None:
    blob = json.dumps(tokens)
    if _use_keychain():
        import keyring

        keyring.set_password(SERVICE, ACCOUNT, blob)
        return
    # Create with 0600 from the start — don't write then chmod.
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(FILE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as fh:
        fh.write(blob)


def load() -> Optional[dict]:
    stored = _load_stored()
    if stored:
        return stored
    # Nothing persisted. On a diskless host this is every restart, so fall
    # back to the seeded refresh token. Marked already-expired so the first
    # call refreshes it into a real access token.
    if config.BOOTSTRAP_REFRESH_TOKEN:
        return {"access_token": "", "expires_at": 0,
                "refresh_token": config.BOOTSTRAP_REFRESH_TOKEN}
    return None


def _load_stored() -> Optional[dict]:
    if _use_keychain():
        import keyring

        blob = keyring.get_password(SERVICE, ACCOUNT)
        return json.loads(blob) if blob else None
    if not Path(FILE_PATH).exists():
        return None
    with open(FILE_PATH) as fh:
        return json.load(fh)


def clear() -> None:
    if _use_keychain():
        import keyring

        try:
            keyring.delete_password(SERVICE, ACCOUNT)
        except Exception:
            pass
    elif Path(FILE_PATH).exists():
        Path(FILE_PATH).unlink()


def describe_store() -> str:
    return "macOS Keychain" if _use_keychain() else str(FILE_PATH)
