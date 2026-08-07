"""Login throttling that holds up against a short PIN on a public URL.

Three layers, because the obvious one is not enough:

  per-IP    — the standard control. Defeated by rotating IPs, which is cheap.
  global    — a cap across ALL addresses. This is the one that actually
              matters against distributed guessing, since rotating IPs does
              not raise the ceiling.
  delay     — a fixed pause on every failure, so automated guessing is slow
              even below the thresholds.

Attempts are written to SQLite so the counters survive a process restart. On a
host with no persistent disk the database itself is ephemeral, so a restart
still clears history — the global cap is what carries the load there, since an
attacker cannot force restarts on demand.

The global cap is deliberately generous relative to a legitimate user, who
authenticates once per device and then holds a 30-day cookie. It can in
principle be used to lock the owner out; that is a worse outcome than a slow
attacker but a better one than a guessed PIN, and the window is short.
"""
import sqlite3
import time
from typing import Optional, Tuple

from . import config

WINDOW = 900            # 15 minutes
PER_IP_MAX = 8
GLOBAL_MAX = 25         # across every address
FAIL_DELAY = 1.0        # seconds added to each failed attempt


def _conn() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA_DIR / "ratelimit.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            ts REAL NOT NULL,
            ip TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON attempts(ts)")
    conn.commit()
    return conn


def record_failure(ip: str) -> None:
    conn = _conn()
    conn.execute("INSERT INTO attempts (ts, ip) VALUES (?,?)", (time.time(), ip))
    conn.execute("DELETE FROM attempts WHERE ts < ?", (time.time() - WINDOW * 4,))
    conn.commit()
    time.sleep(FAIL_DELAY)


def clear(ip: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM attempts WHERE ip = ?", (ip,))
    conn.commit()


def check(ip: str) -> Tuple[bool, Optional[str]]:
    """(allowed, reason_if_blocked)."""
    since = time.time() - WINDOW
    conn = _conn()
    ip_hits = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE ip=? AND ts>?",
        (ip, since)).fetchone()[0]
    if ip_hits >= PER_IP_MAX:
        return False, "too many attempts from this device"
    total = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE ts>?", (since,)).fetchone()[0]
    if total >= GLOBAL_MAX:
        return False, "too many failed attempts overall — locked briefly"
    return True, None


def stats() -> dict:
    since = time.time() - WINDOW
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE ts>?", (since,)).fetchone()[0]
    ips = conn.execute(
        "SELECT COUNT(DISTINCT ip) FROM attempts WHERE ts>?",
        (since,)).fetchone()[0]
    return {"failures_in_window": total, "distinct_ips": ips,
            "window_seconds": WINDOW, "per_ip_max": PER_IP_MAX,
            "global_max": GLOBAL_MAX}
