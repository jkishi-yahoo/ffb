"""Thin Yahoo Fantasy API client + helpers for Yahoo's awkward JSON shape.

Yahoo encodes collections as dicts keyed by stringified indices alongside a
"count" key, and frequently splits a single logical object across a list of
partial dicts. `numeric_items` and `merge` normalise both so the rest of the
codebase can work with plain dicts.
"""
import time
from typing import Any, Dict, Iterator, List, Optional

import httpx

from . import config, tokens, yahoo_auth

# Yahoo publishes no hard number; third-party projects converge on ~1000/hr.
# We keep a conservative floor between calls so long polling loops (draft day)
# stay well inside whatever the real ceiling is.
MIN_INTERVAL = 0.35
_last_call = [0.0]


class YahooError(RuntimeError):
    pass


def _throttle() -> None:
    delta = time.monotonic() - _last_call[0]
    if delta < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - delta)
    _last_call[0] = time.monotonic()


def get(path: str, _retry: bool = True, **params: Any) -> dict:
    """GET a Fantasy API path (relative to /fantasy/v2) and return parsed JSON.

    A 401 gets exactly one forced-refresh retry. It must not retry unbounded:
    a *persistent* 401 means the grant itself is bad (scope not authorised,
    access revoked), which no amount of refreshing will fix.
    """
    _throttle()
    params.setdefault("format", "json")
    token = yahoo_auth.valid_access_token()
    url = "{}/{}".format(config.API_BASE, path.lstrip("/"))
    resp = httpx.get(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401 and _retry:
        stored = tokens.load()
        if stored and stored.get("refresh_token"):
            yahoo_auth.refresh(stored["refresh_token"])
            return get(path, _retry=False, **params)
    if resp.status_code in (401, 403):
        raise YahooError(
            "Yahoo API {} on {}\n{}\n\n"
            "A persistent {} after a token refresh means the token is valid but "
            "is not authorised for Fantasy Sports data. The current Yahoo app "
            "form has no Fantasy Sports permission option, so this most likely "
            "means API access must be requested at "
            "https://sports.yahoo.com/developer/access/".format(
                resp.status_code, url, resp.text[:600], resp.status_code
            )
        )
    if resp.status_code != 200:
        raise YahooError(
            "Yahoo API {} on {}\n{}".format(resp.status_code, url, resp.text[:600])
        )
    return resp.json()


# --------------------------------------------------------------------------
# JSON shape helpers
# --------------------------------------------------------------------------
def numeric_items(node: Any) -> Iterator[Any]:
    """Yield the values of a Yahoo numeric-keyed collection, in order."""
    if not isinstance(node, dict):
        return
    keys = [k for k in node.keys() if str(k).isdigit()]
    for k in sorted(keys, key=int):
        yield node[k]


def merge(node: Any) -> Dict[str, Any]:
    """Flatten Yahoo's list-of-partial-dicts (and nested lists) into one dict."""
    out: Dict[str, Any] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).isdigit():
                out.update(merge(v))
            else:
                out[k] = v
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                out.update(merge(item))
    return out


def _find(node: Any, key: str) -> Optional[Any]:
    """Depth-first search for the first occurrence of `key`."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find(item, key)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------
# Phase 0 reads
# --------------------------------------------------------------------------
def current_game_key() -> str:
    """Resolve the game_key for the current NFL season (e.g. '461')."""
    data = get("games;game_keys=nfl")
    gk = _find(data, "game_key")
    if not gk:
        raise YahooError("Could not resolve current NFL game_key from: {}".format(data))
    return str(gk)


def _parse_leagues(data: dict) -> List[dict]:
    leagues: List[dict] = []
    users = _find(data, "users") or {}
    for user in numeric_items(users):
        games = _find(user, "games") or {}
        for game in numeric_items(games):
            games_node = _find(game, "leagues") or {}
            for lg in numeric_items(games_node):
                info = merge(lg.get("league", lg))
                if info.get("league_key"):
                    leagues.append(info)
    return leagues


def my_leagues() -> List[dict]:
    """Every NFL league the logged-in user belongs to this season."""
    return _parse_leagues(get("users;use_login=1/games;game_keys=nfl/leagues"))


def _parse_teams(data: dict) -> List[dict]:
    teams: List[dict] = []
    users = _find(data, "users") or {}
    for user in numeric_items(users):
        games = _find(user, "games") or {}
        for game in numeric_items(games):
            teams_node = _find(game, "teams") or {}
            for tm in numeric_items(teams_node):
                info = merge(tm.get("team", tm))
                if info.get("team_key"):
                    # team_key is {game}.l.{league_id}.t.{team_id}
                    parts = str(info["team_key"]).split(".t.")
                    info["league_key"] = parts[0]
                    info["league_id"] = parts[0].split(".l.")[-1]
                    teams.append(info)
    return teams


def my_teams() -> List[dict]:
    """Every team the logged-in user manages this season, with its league."""
    return _parse_teams(get("users;use_login=1/games;game_keys=nfl/teams"))


def league_settings(league_key: str) -> dict:
    """Raw settings incl. roster_positions and stat_modifiers (scoring)."""
    data = get("league/{}/settings".format(league_key))
    return merge(_find(data, "league") or {})
