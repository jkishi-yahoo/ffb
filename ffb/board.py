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


def _apply_rookie_projections(df: pd.DataFrame, league: League) -> pd.DataFrame:
    """Fill in value for players with no stat history, using draft capital.

    Only applied where there is no baseline: a rookie who somehow already has
    2025 production keeps the real number. Ranking uses the risk-adjusted
    figure so a boom/bust late-rounder does not outrank a safe starter on
    upside alone; the unadjusted projection is kept for display.
    """
    from . import rookies as rookies_mod

    try:
        # Three classes: this year's rookies, plus recent picks who never
        # produced and would otherwise sit on the board at zero.
        proj = rookies_mod.project(CURRENT_SEASON, league, classes=3)
    except Exception:
        # A rookie-curve failure must not take the whole board down.
        return df
    if proj.empty:
        return df

    proj = proj.copy()
    proj["_key"] = proj.player_display_name.map(norm_name)
    proj = proj.drop_duplicates("_key")
    lookup = proj.set_index("_key")

    out = df.copy()
    keys = out.player_display_name.map(norm_name)
    fill = ~out.has_baseline & keys.isin(lookup.index)

    out.loc[fill, "ppg"] = keys[fill].map(lookup.risk_adj_ppg).values
    out.loc[fill, "proj_ppg_upside"] = keys[fill].map(lookup.proj_ppg).values
    out.loc[fill, "hit_rate"] = keys[fill].map(lookup.hit_rate).values
    out.loc[fill, "draft_pick"] = keys[fill].map(lookup["pick"]).values
    out.loc[fill, "value_source"] = "draft capital"
    return out


BASELINE_SEASONS = (2025, 2024, 2023)


def build(league: League) -> pd.DataFrame:
    """The full board for `league`, sorted by value over replacement.

    The spine is the actual 2026 NFL roster, not a bulk player dump: Sleeper
    marks long-retired players as Active, which put Roethlisberger and Le'Veon
    Bell on the board. Joins run on gsis_id / sleeper_id rather than names, so
    accents and generational suffixes cannot silently drop anyone.
    """
    roster = data.rosters(CURRENT_SEASON)
    roster = roster[roster.position.isin(("QB", "RB", "WR", "TE", "K"))]
    if "status" in roster.columns:
        # ACT plus RES (reserve/injured): a player on IR in August is still a
        # legitimate late-round stash. RET/CUT/E14 are genuinely off the board.
        roster = roster[roster.status.isin(("ACT", "RES"))]
    roster = roster.dropna(subset=["gsis_id"]).drop_duplicates("gsis_id")

    spine = pd.DataFrame({
        "gsis_id": roster.gsis_id.values,
        "sleeper_id": roster.get("sleeper_id").values,
        "yahoo_id": roster.get("yahoo_id").values,
        "player_display_name": roster.full_name.values,
        "position": roster.position.values,
        "team": roster.team.values,
    })

    # --- recency-weighted production baseline, joined by gsis_id ----------
    base = projections.player_multi_season_points(BASELINE_SEASONS, league)
    spine = spine.merge(
        base[["player_id", "ppg", "games_played", "seasons_used",
              "latest_season"]],
        left_on="gsis_id", right_on="player_id", how="left")
    spine["has_baseline"] = spine.ppg.notna()
    spine["value_source"] = spine.has_baseline.map(
        {True: "production", False: "none"})

    # --- Sleeper adds injury status and search interest -------------------
    sl = sleeper_frame()
    spine["sleeper_id"] = spine.sleeper_id.astype(str)
    sl["sleeper_id"] = sl.sleeper_id.astype(str)
    spine = spine.merge(
        sl[["sleeper_id", "injury_status", "search_rank", "years_exp"]],
        on="sleeper_id", how="left")

    # --- rookies get value from draft capital -----------------------------
    spine["ppg"] = spine.ppg.fillna(0.0)
    spine = _apply_rookie_projections(spine, league)

    # --- team defenses ----------------------------------------------------
    dst = projections.dst_season_points(SEASON_BASELINE, league)
    team_col = "team" if "team" in dst.columns else dst.columns[2]
    dst_rows = pd.DataFrame({
        "player_display_name": dst.player_display_name.values,
        "position": "DEF",
        "team": dst[team_col].values,
        "ppg": dst.ppg.values,
        "has_baseline": True,
        "value_source": "production",
    })
    board = pd.concat([spine, dst_rows], ignore_index=True, sort=False)

    scored = valuation.add_vor(board, league)
    scored = valuation.add_tiers(scored)

    byes = data.bye_weeks(CURRENT_SEASON)
    scored["bye"] = scored["team"].map(lambda t: byes.get(nflverse_team(t)))
    scored["rookie"] = scored["years_exp"].fillna(-1).astype(float) == 0

    cols = ["player_display_name", "position", "team", "bye", "ppg", "vor",
            "tier", "value_source", "proj_ppg_upside", "hit_rate",
            "draft_pick", "seasons_used", "latest_season", "has_baseline",
            "rookie", "injury_status", "search_rank", "games_played",
            "gsis_id", "yahoo_id"]
    keep = [c for c in cols if c in scored.columns]
    return scored[keep].reset_index(drop=True)


def coverage_report(board: pd.DataFrame) -> Dict[str, object]:
    """What the board does and does not know — reported alongside it so gaps
    are visible rather than implied."""
    counts = (board.value_source.value_counts().to_dict()
              if "value_source" in board else {})
    return {
        "players": len(board),
        "from_production": int(counts.get("production", 0)),
        "from_draft_capital": int(counts.get("draft capital", 0)),
        "unvalued": int(counts.get("none", 0)),
        "byes_resolved": int(board.bye.notna().sum()) if "bye" in board else 0,
    }
