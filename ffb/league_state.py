"""Live league state: my roster, everyone's rosters, and the available pool.

This is the seam between "public data only" (what works today) and "real
league data" (what unlocks when Yahoo approves API access). The waiver and
trade engines depend only on this module, so approval flips them on without
touching their logic.

When Yahoo is unreachable, every accessor returns empty and `status()`
explains why. Nothing here invents data to fill the gap — a waiver
recommendation built on guessed rosters would be worse than no recommendation.
"""
import time
from typing import Dict, List, Optional

import pandas as pd

from . import board as board_mod
from . import config, leagues, tokens, yahoo_client

CACHE_TTL = 600
_cache: Dict[str, tuple] = {}


class NotConnected(RuntimeError):
    pass


def _cached(key: str, fn, ttl: int = CACHE_TTL):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    val = fn()
    _cache[key] = (time.time() + ttl, val)
    return val


def invalidate(league_id: Optional[str] = None) -> None:
    if league_id is None:
        _cache.clear()
    else:
        for k in [k for k in _cache if league_id in k]:
            _cache.pop(k, None)


def status() -> dict:
    """Whether live league data is available, and if not, precisely why.

    Distinguishes 'not logged in' from 'logged in but not entitled', because
    they need completely different fixes and conflating them wastes time.
    """
    if not config.CLIENT_ID or not config.CLIENT_SECRET:
        return {"connected": False, "reason": "no_credentials",
                "detail": "No Yahoo client ID/secret in .env."}
    if not tokens.load():
        return {"connected": False, "reason": "not_logged_in",
                "detail": "Run: python -m ffb.cli login"}
    try:
        yahoo_client.current_game_key()
        return {"connected": True, "reason": None, "detail": "Live."}
    except yahoo_client.YahooError as exc:
        text = str(exc)
        if "additional_authorization_required" in text:
            return {
                "connected": False, "reason": "not_approved",
                "detail": ("Yahoo has not approved Fantasy API access for this "
                           "app yet. Apply at "
                           "https://sports.yahoo.com/developer/access/"),
            }
        return {"connected": False, "reason": "api_error", "detail": text[:300]}
    except Exception as exc:  # network down, etc.
        return {"connected": False, "reason": "unreachable",
                "detail": str(exc)[:300]}


def league_key(league_id: str) -> str:
    return _cached("key:" + league_id,
                   lambda: "{}.l.{}".format(
                       yahoo_client.current_game_key(), league_id),
                   ttl=24 * 3600)


def my_team_key(league_id: str) -> Optional[str]:
    def fetch():
        for t in yahoo_client.my_teams():
            if t.get("league_id") == league_id:
                return t["team_key"]
        return None
    return _cached("myteam:" + league_id, fetch, ttl=24 * 3600)


def all_rosters(league_id: str) -> Dict[str, List[dict]]:
    return _cached("rosters:" + league_id,
                   lambda: yahoo_client.all_rosters(league_key(league_id)))


def teams(league_id: str) -> List[dict]:
    return _cached("teams:" + league_id,
                   lambda: yahoo_client.league_teams(league_key(league_id)),
                   ttl=24 * 3600)


def available(league_id: str) -> List[dict]:
    return _cached("avail:" + league_id,
                   lambda: yahoo_client.available_players(league_key(league_id)))


def my_roster(league_id: str) -> List[dict]:
    tk = my_team_key(league_id)
    if not tk:
        return []
    return _cached("myroster:" + league_id,
                   lambda: yahoo_client.team_roster(tk))


# --------------------------------------------------------------------------
# Joining Yahoo players to the valuation board
# --------------------------------------------------------------------------
def attach_values(players: List[dict], league_id: str) -> pd.DataFrame:
    """Join Yahoo players onto the board so they carry ppg/VOR/tier/bye.

    Yahoo's player_id maps to the board's yahoo_id, which comes from the
    nflverse roster file — an ID join, not a name join. Falls back to
    normalised names for anyone the ID misses (team defenses especially,
    which Yahoo keys differently).
    """
    if not players:
        return pd.DataFrame()
    board = board_mod.build(leagues.get(league_id))
    ydf = pd.DataFrame(players)

    board = board.copy()
    board["_yid"] = board.get("yahoo_id")
    if "_yid" in board:
        board["_yid"] = board["_yid"].astype("string")
    ydf["_yid"] = ydf["yahoo_id"].astype("string")

    merged = ydf.merge(
        board[["_yid", "ppg", "vor", "tier", "bye", "value_source",
               "player_display_name"]],
        on="_yid", how="left", suffixes=("", "_b"))

    # Name fallback for rows the ID join missed.
    missing = merged.vor.isna()
    if missing.any():
        board["_nk"] = board.player_display_name.map(board_mod.norm_name)
        lookup = board.drop_duplicates("_nk").set_index("_nk")
        keys = merged.loc[missing, "name"].map(board_mod.norm_name)
        for col in ["ppg", "vor", "tier", "bye", "value_source"]:
            merged.loc[missing, col] = keys.map(
                lookup[col] if col in lookup else pd.Series(dtype=float)).values

    merged["ppg"] = merged.ppg.fillna(0.0)
    merged["vor"] = merged.vor.fillna(-99.0)   # unknown sorts last, visibly
    return merged
