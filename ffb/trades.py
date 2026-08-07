"""Trade proposals: what helps me, and why the other manager would accept.

The metric throughout is **starting-lineup points**, not roster total. Trading
your RB4 for someone's WR5 changes your roster and not your Sunday score. A
trade is only good if it raises the points you actually start — and the same
test is applied to the other manager, because a proposal they lose on will be
rejected no matter how much it helps you.

Acceptance likelihood is estimated from their side of that same calculation:
a manager with three startable TEs and a hole at RB has a visible reason to
say yes, and that reason is shown so you can write the message yourself.
"""
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .leagues import League

FLEX_ELIGIBLE = ("WR", "RB", "TE")


def optimal_lineup(roster: pd.DataFrame, league: League) -> Tuple[float, list]:
    """Best legal starting lineup and its total ppg.

    Greedy: fill dedicated slots with the best at each position, then give the
    flex to the best remaining eligible player. With one flex this is optimal.
    """
    if roster is None or roster.empty:
        return 0.0, []
    pool = roster.sort_values("ppg", ascending=False)
    used, chosen = set(), []

    for pos, count in league.roster.starters.items():
        at_pos = pool[(pool.position == pos) & (~pool.index.isin(used))]
        for idx in at_pos.head(count).index:
            used.add(idx)
            chosen.append(idx)

    flex_slots = sum(league.roster.flex.values())
    if flex_slots:
        rest = pool[(pool.position.isin(FLEX_ELIGIBLE)) &
                    (~pool.index.isin(used))]
        for idx in rest.head(flex_slots).index:
            used.add(idx)
            chosen.append(idx)

    total = float(pool.loc[chosen].ppg.sum()) if chosen else 0.0
    return total, chosen


def positional_profile(roster: pd.DataFrame, league: League) -> Dict[str, dict]:
    """Per position: how many startable bodies, and the surplus or shortfall.

    Surplus is what makes a trade possible — a manager only gives up a player
    they were not starting anyway.
    """
    out = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        at_pos = roster[roster.position == pos].sort_values(
            "ppg", ascending=False)
        need = league.roster.starters.get(pos, 0)
        out[pos] = {
            "count": len(at_pos),
            "starters_needed": need,
            "surplus": max(0, len(at_pos) - need),
            "shortfall": max(0, need - len(at_pos)),
            "best": float(at_pos.iloc[0].ppg) if len(at_pos) else 0.0,
            "starter_floor": float(at_pos.iloc[need - 1].ppg)
            if len(at_pos) >= need and need else 0.0,
        }
    return out


def _name(row) -> str:
    return row.get("name") or row.get("player_display_name") or "?"


def _swap(roster: pd.DataFrame, out_idx: list, incoming: pd.DataFrame
          ) -> pd.DataFrame:
    kept = roster.drop(index=out_idx)
    return pd.concat([kept, incoming], ignore_index=False, sort=False)


def evaluate(my_roster: pd.DataFrame, their_roster: pd.DataFrame,
             give_idx: list, get_idx: list, league: League) -> dict:
    """Starting-lineup delta for both sides of one proposal."""
    my_before, _ = optimal_lineup(my_roster, league)
    their_before, _ = optimal_lineup(their_roster, league)

    my_after, _ = optimal_lineup(
        _swap(my_roster, give_idx, their_roster.loc[get_idx]), league)
    their_after, _ = optimal_lineup(
        _swap(their_roster, get_idx, my_roster.loc[give_idx]), league)

    return {
        "my_gain": round(my_after - my_before, 2),
        "their_gain": round(their_after - their_before, 2),
    }


def acceptance_score(my_gain: float, their_gain: float) -> float:
    """How likely the other manager says yes, 0-1.

    Driven mostly by their gain — nobody accepts a trade that weakens their
    lineup — with a penalty for lopsidedness, since an obviously unbalanced
    offer reads as an insult even when the raw numbers work.
    """
    if their_gain <= 0:
        return 0.0
    balance = 1.0 - min(abs(my_gain - their_gain) / max(abs(my_gain) + abs(their_gain), 0.01), 1.0)
    magnitude = min(their_gain / 3.0, 1.0)
    return round(0.35 * balance + 0.65 * magnitude, 3)


def propose(my_roster: pd.DataFrame, rosters: Dict[str, pd.DataFrame],
            league: League, team_names: Optional[Dict[str, str]] = None,
            max_per_team: int = 2, top_n: int = 10) -> List[dict]:
    """Generate and rank 1-for-1 and 2-for-1 trades across the league."""
    if my_roster is None or my_roster.empty:
        return []
    team_names = team_names or {}
    mine = positional_profile(my_roster, league)
    my_needs = [p for p, v in mine.items() if v["shortfall"] or v["surplus"] == 0]
    my_surplus = [p for p, v in mine.items() if v["surplus"] > 0]

    proposals = []
    for tkey, their in rosters.items():
        if their is None or their.empty:
            continue
        theirs = positional_profile(their, league)
        their_needs = [p for p, v in theirs.items()
                       if v["shortfall"] or v["surplus"] == 0]
        their_surplus = [p for p, v in theirs.items() if v["surplus"] > 0]

        # Only bother where the needs actually complement.
        give_positions = [p for p in my_surplus if p in their_needs]
        get_positions = [p for p in their_surplus if p in my_needs]
        if not give_positions or not get_positions:
            continue

        found = []
        for gp in give_positions:
            # Give from surplus, never a player we are starting.
            givers = my_roster[my_roster.position == gp].sort_values(
                "ppg", ascending=False)
            givers = givers.iloc[mine[gp]["starters_needed"]:]
            for gi in givers.index[:2]:
                for rp in get_positions:
                    takers = their[their.position == rp].sort_values(
                        "ppg", ascending=False)
                    takers = takers.iloc[theirs[rp]["starters_needed"]:]
                    for ti in takers.index[:2]:
                        ev = evaluate(my_roster, their, [gi], [ti], league)
                        if ev["my_gain"] <= 0:
                            continue
                        acc = acceptance_score(ev["my_gain"], ev["their_gain"])
                        if acc <= 0:
                            continue
                        found.append({
                            "team_key": tkey,
                            "team_name": team_names.get(tkey, tkey),
                            "give": [_name(my_roster.loc[gi])],
                            "give_pos": [gp],
                            "get": [_name(their.loc[ti])],
                            "get_pos": [rp],
                            "my_gain": ev["my_gain"],
                            "their_gain": ev["their_gain"],
                            "acceptance": acc,
                            "why_me": "upgrades your starting {} by {:+.1f} ppg"
                                      .format(rp, ev["my_gain"]),
                            "why_them": "they start only {} at {} and you are "
                                        "sending {} — worth {:+.1f} to them"
                                        .format(theirs[gp]["count"], gp,
                                                _name(my_roster.loc[gi]),
                                                ev["their_gain"]),
                        })
        found.sort(key=lambda r: -(r["acceptance"] * r["my_gain"]))
        proposals.extend(found[:max_per_team])

    proposals.sort(key=lambda r: -(r["acceptance"] * r["my_gain"]))
    return proposals[:top_n]
