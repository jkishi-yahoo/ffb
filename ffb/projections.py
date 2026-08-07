"""Turn raw stats into per-game points under a league's rules.

Method, stated plainly so the numbers are auditable:

  * Offensive players and kickers are scored from nflverse season totals with
    the league's own multipliers, then divided by games played to get a rate.
  * Team defenses are scored the same way, plus the points-allowed tier applied
    *per game* (a season total would land in the wrong tier every time).
  * This is a *baseline of past production*, not a forecast. It answers "what
    would this player have been worth in MY league last year". Consensus rank
    is blended in separately (see valuation.py) to account for 2026 role and
    injury changes that history cannot know about.
"""
from typing import Optional

import pandas as pd

from . import data, leagues, scoring
from .leagues import League

OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE", "K")


def player_season_points(season: int, league: League) -> pd.DataFrame:
    """Per-player season and per-game points under `league`."""
    df = data.player_stats(season).copy()
    df = df[df.position.isin(OFFENSE_POSITIONS)]
    df["points"] = scoring.score_frame(df, league)
    games = df.get("games")
    df["games_played"] = games.fillna(0) if games is not None else 0
    df["ppg"] = df.apply(
        lambda r: r["points"] / r["games_played"] if r["games_played"] else 0.0,
        axis=1,
    )
    cols = ["player_id", "player_display_name", "position", "recent_team",
            "games_played", "points", "ppg"]
    keep = [c for c in cols if c in df.columns]
    return df[keep].sort_values("points", ascending=False).reset_index(drop=True)


def _points_allowed_by_team(season: int) -> pd.DataFrame:
    """Per-game points allowed for every team, from the schedule's scores.

    Regular season only. The team stat totals come from stats_team_reg, so
    including playoff games here would divide regular-season stat points by an
    inflated game count — penalising exactly the best defenses.
    """
    games = data.schedule()
    yr = games[(games.season == season) & games.home_score.notna()]
    if "game_type" in yr.columns:
        yr = yr[yr.game_type == "REG"]
    rows = []
    for _, g in yr.iterrows():
        # A team "allows" its opponent's score.
        rows.append({"team": g.home_team, "week": g.week, "pa": g.away_score})
        rows.append({"team": g.away_team, "week": g.week, "pa": g.home_score})
    return pd.DataFrame(rows)


def dst_season_points(season: int, league: League) -> pd.DataFrame:
    """Per-team DST season and per-game points under `league`.

    Points allowed is tiered per game and summed, which is materially
    different from tiering a season total.
    """
    team = data.team_stats(season).copy()
    team["stat_points"] = scoring.score_frame(team, league, dst=True)

    pa = _points_allowed_by_team(season)
    pa["pa_points"] = pa.pa.apply(
        lambda v: scoring.points_allowed_points(float(v), league))
    agg = pa.groupby("team").agg(
        games_played=("week", "count"),
        pa_points=("pa_points", "sum"),
        pa_per_game=("pa", "mean"),
    ).reset_index()

    key = "team" if "team" in team.columns else team.columns[0]
    out = team.merge(agg, left_on=key, right_on="team", how="left",
                     suffixes=("", "_agg"))
    out["points"] = out.stat_points.fillna(0) + out.pa_points.fillna(0)
    out["ppg"] = out.apply(
        lambda r: r["points"] / r["games_played"] if r.get("games_played") else 0.0,
        axis=1,
    )
    out["position"] = "DEF"
    out["player_display_name"] = out[key] + " DST"
    cols = ["player_display_name", "position", key, "games_played",
            "stat_points", "pa_points", "pa_per_game", "points", "ppg"]
    keep = [c for c in cols if c in out.columns]
    return out[keep].sort_values("points", ascending=False).reset_index(drop=True)


def baseline(season: int, league: League) -> pd.DataFrame:
    """Offense + DST in one frame, the input to valuation."""
    off = player_season_points(season, league)
    dst = dst_season_points(season, league)
    dst = dst.rename(columns={c: "recent_team" for c in dst.columns
                              if c in ("team",)})
    frames = [off, dst[[c for c in off.columns if c in dst.columns]]]
    return pd.concat(frames, ignore_index=True, sort=False)
