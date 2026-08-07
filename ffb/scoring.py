"""Score real stat lines under a league's actual rules.

Everything downstream — rankings, VOR, draft and waiver recommendations — is
denominated in these points. That is the whole point of the tool: a WR in a
full-PPR 3WR league is not the same asset as in a 2WR half-PPR league, and
generic rankings cannot know the difference.
"""
from typing import Dict, Iterable, Mapping, Optional

from .leagues import League


def score_stats(stats: Mapping[str, float], league: League) -> float:
    """Points for one offensive/kicking stat line (a player-season or -week)."""
    total = 0.0
    for col, per_unit in league.scoring.items():
        val = stats.get(col)
        if val:  # skips 0, None, and NaN-as-falsy after coercion
            total += float(val) * per_unit
    return total


def score_dst(stats: Mapping[str, float], points_allowed: Optional[float],
              league: League) -> float:
    """Points for a team defense. `points_allowed` is per-game or per-season
    depending on the caller; the tier lookup applies per game, so pass
    per-game values when scoring a season."""
    total = 0.0
    for col, per_unit in league.dst_scoring.items():
        val = stats.get(col)
        if val:
            total += float(val) * per_unit
    if points_allowed is not None:
        total += points_allowed_points(points_allowed, league)
    return total


def points_allowed_points(pa: float, league: League) -> float:
    """Map points allowed in a game onto the league's PA tier."""
    for ceiling, pts in league.points_allowed:
        if pa <= ceiling:
            return pts
    return league.points_allowed[-1][1]


def explain(stats: Mapping[str, float], league: League) -> Dict[str, float]:
    """Per-stat point contributions, for showing the user *why* a projection
    is what it is. Only non-zero contributions are returned."""
    out = {}
    for col, per_unit in league.scoring.items():
        val = stats.get(col)
        if val:
            out[col] = round(float(val) * per_unit, 2)
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1])))


def score_frame(df, league: League, dst: bool = False):
    """Vectorised scoring for a pandas DataFrame of stat rows.

    Missing columns are treated as zero rather than raising, since nflverse
    schemas drift between seasons and a missing column should degrade the
    projection, not crash the app.
    """
    table = league.dst_scoring if dst else league.scoring
    total = None
    for col, per_unit in table.items():
        if col not in df.columns:
            continue
        contrib = df[col].fillna(0.0) * per_unit
        total = contrib if total is None else total + contrib
    if total is None:
        raise ValueError("None of the scoring columns were present in the data")
    return total


def missing_columns(columns: Iterable[str], league: League,
                    dst: bool = False) -> list:
    """Scoring columns absent from a dataset — surfaced so a silently
    under-counted projection is visible instead of plausible-looking."""
    table = league.dst_scoring if dst else league.scoring
    have = set(columns)
    return sorted(c for c in table if c not in have)
