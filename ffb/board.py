"""Assemble the draft board: baseline value + current status + byes + tiers.

The 2025 stat baseline knows what players *did*; it does not know who changed
teams, who is hurt, or that rookies exist. Sleeper supplies the current player
universe, so the two are joined here.

Players with no 2025 production (rookies, long injury absences) are kept and
flagged rather than dropped — silently omitting a first-round rookie from a
draft board would be worse than showing it with an explicit "no baseline" mark.
"""
import re
import unicodedata
from typing import Dict, Optional

import pandas as pd

from . import data, projections, valuation
from .leagues import League

SEASON_BASELINE = 2025
CURRENT_SEASON = 2026

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Team codes diverge between providers. Verified 2026-08-07: LAR/LA is the only
# live mismatch between Sleeper and the nflverse schedule, but the others are
# common enough across sources to guard pre-emptively. Maps provider code ->
# nflverse schedule code.
TEAM_ALIASES = {
    "LAR": "LA",    # Sleeper says LAR, nflverse schedule says LA
    "WSH": "WAS",
    "JAC": "JAX",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}


def nflverse_team(code) -> Optional[str]:
    """Normalise a team code to the one the nflverse schedule uses."""
    if not isinstance(code, str):
        return None
    return TEAM_ALIASES.get(code, code)


def norm_name(name: str) -> str:
    """Normalise a player name for joining across sources.

    Handles accents (Ja'Marr / José), punctuation, and generational suffixes,
    which are the usual causes of silent join failures between data providers.
    """
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    parts = [p for p in s.split() if p not in _SUFFIXES]
    return " ".join(parts)


def sleeper_frame() -> pd.DataFrame:
    """Current fantasy-relevant players from Sleeper."""
    raw = data.sleeper_players()
    rows = []
    for pid, p in raw.items():
        pos = p.get("position")
        if pos not in FANTASY_POSITIONS:
            continue
        if not p.get("team"):
            continue  # free agents / not on an NFL roster
        name = p.get("full_name") or "{} {}".format(
            p.get("first_name", ""), p.get("last_name", "")).strip()
        rows.append({
            "sleeper_id": pid,
            "name": name,
            "position": pos,
            "team": p.get("team"),
            "injury_status": p.get("injury_status"),
            "years_exp": p.get("years_exp"),
            "search_rank": p.get("search_rank"),
            "_key": norm_name(name),
        })
    return pd.DataFrame(rows)


def build(league: League, season: int = SEASON_BASELINE,
          min_games: int = 4) -> pd.DataFrame:
    """The full board for `league`, sorted by value over replacement."""
    base = projections.baseline(season, league)
    base = base[base.games_played >= min_games].copy()
    base["_key"] = base.player_display_name.map(norm_name)

    cur = sleeper_frame()
    # DST rows come from team stats and have no Sleeper counterpart by name;
    # match them on team code instead.
    # Both sides go through nflverse_team so LAR/LA and friends line up.
    dst_mask = base.position == "DEF"
    base.loc[dst_mask, "_key"] = base.loc[dst_mask, "recent_team"].map(
        lambda t: norm_name(str(nflverse_team(t) or t)))
    cur.loc[cur.position == "DEF", "_key"] = cur.loc[
        cur.position == "DEF", "team"].map(
            lambda t: norm_name(str(nflverse_team(t) or t)))

    merged = cur.merge(
        base[["_key", "position", "games_played", "points", "ppg"]],
        on=["_key", "position"], how="outer", suffixes=("", "_base"))

    # Prefer Sleeper's current team; fall back to the stat source.
    merged["name"] = merged["name"].fillna("")
    merged["has_baseline"] = merged["ppg"].notna()
    merged["ppg"] = merged["ppg"].fillna(0.0)
    merged["points"] = merged["points"].fillna(0.0)
    merged = merged[merged.position.isin(FANTASY_POSITIONS)]
    merged = merged[merged.name != ""]

    merged["player_display_name"] = merged["name"]
    scored = valuation.add_vor(merged, league)
    scored = valuation.add_tiers(scored)

    byes = data.bye_weeks(CURRENT_SEASON)
    scored["bye"] = scored["team"].map(
        lambda t: byes.get(nflverse_team(t)))

    scored["rookie"] = scored["years_exp"].fillna(-1).astype(int) == 0
    cols = ["player_display_name", "position", "team", "bye", "ppg", "vor",
            "tier", "has_baseline", "rookie", "injury_status", "search_rank",
            "games_played"]
    keep = [c for c in cols if c in scored.columns]
    return scored[keep].reset_index(drop=True)


def coverage_report(board: pd.DataFrame) -> Dict[str, object]:
    """What the board does and does not know — shown alongside it so gaps are
    visible rather than implied."""
    total = len(board)
    no_base = board[~board.has_baseline]
    return {
        "players": total,
        "with_2025_baseline": int(board.has_baseline.sum()),
        "without_baseline": int((~board.has_baseline).sum()),
        "rookies_without_baseline": int(
            (no_base.rookie if "rookie" in no_base else pd.Series(dtype=bool)).sum()),
        "byes_resolved": int(board.bye.notna().sum()) if "bye" in board else 0,
    }
