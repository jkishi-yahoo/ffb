"""Data sources: nflverse (stats, schedule) and Sleeper (player universe).

Both are free and need no auth, which matters because Yahoo Fantasy API access
is approval-gated and may not arrive before draft day.

Everything is cached to data/ (gitignored) so draft-day work never depends on
a network round trip.
"""
import io
import json
import time
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

from . import config

CACHE = config.ROOT / "data"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"

DEFAULT_TTL = 24 * 3600  # a day; player news moves, season stats do not


def _cache_path(name: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    return CACHE / name


def _fresh(path: Path, ttl: int) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl


def fetch_csv(url: str, cache_name: str, ttl: int = DEFAULT_TTL) -> pd.DataFrame:
    path = _cache_path(cache_name)
    if _fresh(path, ttl):
        return pd.read_csv(path, low_memory=False)
    resp = httpx.get(url, follow_redirects=True, timeout=180)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return pd.read_csv(io.BytesIO(resp.content), low_memory=False)


def player_stats(season: int) -> pd.DataFrame:
    """Regular-season player stat totals for a season."""
    return fetch_csv(
        "{}/stats_player/stats_player_reg_{}.csv".format(NFLVERSE, season),
        "stats_player_reg_{}.csv".format(season),
        ttl=7 * 24 * 3600,  # a completed season never changes
    )


def team_stats(season: int) -> pd.DataFrame:
    """Team-level stat totals — the basis for team DST scoring."""
    return fetch_csv(
        "{}/stats_team/stats_team_reg_{}.csv".format(NFLVERSE, season),
        "stats_team_reg_{}.csv".format(season),
        ttl=7 * 24 * 3600,
    )


def rosters(season: int) -> pd.DataFrame:
    """Actual NFL rosters for `season`.

    This is the authority on who is really on a team. Sleeper's `active` flag
    is not usable for this — it marks long-retired players as Active. Also
    carries gsis_id / sleeper_id / yahoo_id, so downstream joins use real IDs
    instead of fuzzy name matching.
    """
    return fetch_csv(
        "{}/rosters/roster_{}.csv".format(NFLVERSE, season),
        "roster_{}.csv".format(season),
        ttl=12 * 3600,  # cuts and signings move daily in preseason
    )


def schedule() -> pd.DataFrame:
    return fetch_csv("{}/schedules/games.csv".format(NFLVERSE), "games.csv")


def bye_weeks(season: int) -> dict:
    """team -> bye week for `season`.

    Derived from the schedule rather than hardcoded: a team's bye is the week
    in the regular-season range where it does not appear. Returns {} if the
    schedule for that season is not published yet, so callers can degrade
    instead of inventing byes.
    """
    games = schedule()
    yr = games[games.season == season]
    if yr.empty:
        return {}
    weeks = sorted(int(w) for w in yr.week.unique())
    # Regular season only; playoff weeks have no byes to infer.
    reg = [w for w in weeks if w <= 18]
    teams = sorted(set(yr.home_team) | set(yr.away_team))
    out = {}
    for team in teams:
        played = set(
            yr[(yr.home_team == team) | (yr.away_team == team)].week.astype(int)
        )
        missing = [w for w in reg if w not in played]
        if len(missing) == 1:
            out[team] = missing[0]
    return out


def sleeper_players(ttl: int = DEFAULT_TTL) -> dict:
    """Sleeper's full player universe: names, positions, teams, injury status.

    Used for the current-season player pool, since nflverse stats are
    historical and say nothing about who is on a roster in August.
    """
    path = _cache_path("sleeper_players.json")
    if _fresh(path, ttl):
        return json.loads(path.read_text())
    resp = httpx.get(SLEEPER_PLAYERS, timeout=180)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return resp.json()


def cache_status() -> list:
    """(name, age_hours, size_mb) for each cached file — so it is obvious
    whether draft-day data is stale."""
    if not CACHE.exists():
        return []
    rows = []
    for p in sorted(CACHE.iterdir()):
        if p.is_file():
            rows.append((
                p.name,
                round((time.time() - p.stat().st_mtime) / 3600, 1),
                round(p.stat().st_size / 1e6, 1),
            ))
    return rows
