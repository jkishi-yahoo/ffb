"""Value over replacement, tiers, and positional scarcity for a specific league.

Replacement level is derived from the league's actual roster shape and team
count, not a convention. In a 12-team league starting 3 WR plus a W/R/T flex,
36-48 WRs are starters league-wide, so WR replacement level sits far deeper
than in a 2-WR league — which is exactly why generic rankings misprice WRs
here.

Flex slots are allocated to whichever of WR/RB/TE actually earn them rather
than assumed, so the baseline reflects how the league really fills out.
"""
from typing import Dict, List, Optional

import pandas as pd

from .leagues import League

FLEX_POSITIONS = ("WR", "RB", "TE")


def replacement_levels(df: pd.DataFrame, league: League,
                       value_col: str = "ppg") -> Dict[str, float]:
    """Points at replacement level for each position.

    Walks the draft board the way the league actually fills: dedicated starter
    slots first, then flex slots to the best remaining flex-eligible players.
    Replacement level for a position is the best player at that position who
    does *not* end up a league-wide starter.
    """
    pool = df.sort_values(value_col, ascending=False)
    teams = league.num_teams

    starters_needed = {pos: cnt * teams
                       for pos, cnt in league.roster.starters.items()}
    taken: Dict[str, int] = {pos: 0 for pos in starters_needed}
    starter_ids = set()

    # 1. Dedicated slots.
    for idx, row in pool.iterrows():
        pos = row["position"]
        if pos in starters_needed and taken[pos] < starters_needed[pos]:
            taken[pos] += 1
            starter_ids.add(idx)

    # 2. Flex slots go to the best remaining eligible players.
    flex_slots = sum(cnt * teams for cnt in league.roster.flex.values())
    for idx, row in pool.iterrows():
        if flex_slots <= 0:
            break
        if idx in starter_ids:
            continue
        if row["position"] in FLEX_POSITIONS:
            starter_ids.add(idx)
            flex_slots -= 1

    # 3. Replacement = best non-starter at each position.
    levels: Dict[str, float] = {}
    for pos in pool["position"].unique():
        rest = pool[(pool.position == pos) & (~pool.index.isin(starter_ids))]
        levels[pos] = float(rest.iloc[0][value_col]) if len(rest) else 0.0
    return levels


def add_vor(df: pd.DataFrame, league: League,
            value_col: str = "ppg") -> pd.DataFrame:
    """Attach replacement level and value-over-replacement per position."""
    out = df.copy()
    levels = replacement_levels(out, league, value_col)
    out["replacement"] = out.position.map(levels).fillna(0.0)
    out["vor"] = out[value_col] - out["replacement"]
    return out.sort_values("vor", ascending=False).reset_index(drop=True)


def add_tiers(df: pd.DataFrame, by: str = "vor",
              gap_multiplier: float = 1.0) -> pd.DataFrame:
    """Group players into tiers per position, breaking where value drops.

    A tier break is a gap between consecutive players larger than the mean gap
    plus `gap_multiplier` standard deviations. Tiers matter more than raw rank
    on draft day: the question is rarely "who is best" but "does waiting a
    round cost me a tier".
    """
    out = df.copy()
    out["tier"] = 0
    for pos, grp in out.groupby("position"):
        grp = grp.sort_values(by, ascending=False)
        vals = grp[by].to_numpy()
        if len(vals) < 3:
            out.loc[grp.index, "tier"] = 1
            continue
        gaps = vals[:-1] - vals[1:]
        threshold = gaps.mean() + gap_multiplier * gaps.std()
        tier = 1
        tiers = [tier]
        for gap in gaps:
            if gap > threshold:
                tier += 1
            tiers.append(tier)
        out.loc[grp.index, "tier"] = tiers
    return out


def scarcity(df: pd.DataFrame, league: League,
             value_col: str = "vor") -> pd.DataFrame:
    """How fast value decays at each position — the tiebreaker when two
    players grade similarly. A steep drop means waiting is expensive."""
    teams = league.num_teams
    rows = []
    for pos, grp in df.groupby("position"):
        grp = grp.sort_values(value_col, ascending=False)
        vals = grp[value_col].to_numpy()
        starters = league.roster.starters.get(pos, 0) * teams
        window = vals[:max(starters, 1)]
        rows.append({
            "position": pos,
            "starters_league_wide": starters,
            "best": round(float(vals[0]), 2) if len(vals) else 0.0,
            "starter_floor": round(float(window[-1]), 2) if len(window) else 0.0,
            "drop_across_starters": round(
                float(window[0] - window[-1]), 2) if len(window) else 0.0,
            "pool_depth": int((grp[value_col] > 0).sum()),
        })
    return pd.DataFrame(rows).sort_values(
        "drop_across_starters", ascending=False).reset_index(drop=True)


def apply_overrides(df: pd.DataFrame, overrides: Optional[pd.DataFrame],
                    weight: float = 0.5,
                    name_col: str = "player_display_name") -> pd.DataFrame:
    """Blend user-supplied rankings into the computed board.

    `overrides` needs a name column and a `rank` column. Blending happens in
    rank space (not points) because uploaded rankings rarely carry point
    values. weight=0 ignores overrides, 1.0 uses them alone.
    """
    if overrides is None or overrides.empty:
        return df
    out = df.copy()
    out["_key"] = out[name_col].str.lower().str.strip()
    ov = overrides.copy()
    ov["_key"] = ov[ov.columns[0]].astype(str).str.lower().str.strip()
    ov = ov[["_key", "rank"]].dropna()

    out["computed_rank"] = out["vor"].rank(ascending=False, method="min")
    out = out.merge(ov, on="_key", how="left")
    out["blended_rank"] = out.apply(
        lambda r: r["computed_rank"] if pd.isna(r.get("rank"))
        else (1 - weight) * r["computed_rank"] + weight * r["rank"],
        axis=1,
    )
    out = out.drop(columns=["_key"])
    return out.sort_values("blended_rank").reset_index(drop=True)
