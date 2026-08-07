"""Weekly waiver-wire recommendations.

Ranks available players by what *this* roster actually needs, not by raw
value. The gap that matters is between a candidate and the player they would
replace in your lineup — adding a WR4 who never starts is worth nothing even
if he is the best player available.

Both leagues use continual rolling waivers, not FAAB, so suggestions are
expressed as waiver-priority cost rather than a dollar bid. `bid_guidance`
returns FAAB numbers only for a league configured that way.
"""
from typing import Dict, List, Optional

import pandas as pd

from .leagues import League

FLEX_ELIGIBLE = ("WR", "RB", "TE")
STARTABLE = ("QB", "RB", "WR", "TE", "K", "DEF")


def starter_baseline(roster: pd.DataFrame, league: League) -> Dict[str, float]:
    """The ppg of the weakest starter at each position — the bar a pickup has
    to clear to actually change your lineup."""
    out = {}
    for pos, count in league.roster.starters.items():
        at_pos = roster[roster.position == pos].sort_values(
            "ppg", ascending=False)
        if len(at_pos) >= count:
            out[pos] = float(at_pos.iloc[count - 1].ppg)
        else:
            out[pos] = 0.0   # slot unfilled: anyone is an upgrade
    return out


def droppable(roster: pd.DataFrame, league: League,
              keep_starters: bool = True) -> pd.DataFrame:
    """Roster players ranked worst-first as drop candidates.

    Starters at a position where you have no backup are protected: freeing a
    roster spot by creating a hole in the lineup is not a gain.
    """
    if roster.empty:
        return roster
    r = roster.sort_values("ppg", ascending=True).copy()
    if not keep_starters:
        return r
    protected = set()
    for pos, count in league.roster.starters.items():
        at_pos = roster[roster.position == pos].sort_values(
            "ppg", ascending=False)
        protected.update(at_pos.head(count).index.tolist())
    r["is_starter"] = r.index.isin(protected)
    return r.sort_values(["is_starter", "ppg"], ascending=[True, True])


def recommend(available: pd.DataFrame, roster: pd.DataFrame, league: League,
              waiver_priority: Optional[int] = None,
              top_n: int = 8) -> List[dict]:
    """Rank waiver targets for one team, each with a reason and a drop."""
    if available is None or available.empty:
        return []

    baseline = starter_baseline(roster, league) if not roster.empty else {}
    drops = droppable(roster, league)
    bench_depth = max(0, len(roster) - league.roster.starting_slots)

    # Bye weeks already stacked on this roster — a pickup that covers a hole
    # is worth more than its raw value suggests.
    bye_counts = (roster.bye.value_counts().to_dict()
                  if "bye" in roster and not roster.empty else {})

    out = []
    for _, p in available.head(120).iterrows():
        pos = p.get("position")
        if pos not in STARTABLE:
            continue
        ppg = float(p.get("ppg") or 0.0)
        base = baseline.get(pos, 0.0)
        upgrade = ppg - base

        reasons, score = [], upgrade

        if not roster.empty and upgrade > 1.0:
            reasons.append("+{:.1f} ppg over your worst starting {}".format(
                upgrade, pos))
        elif not roster.empty and upgrade > 0:
            reasons.append("marginal upgrade at {} (+{:.1f})".format(pos, upgrade))
        else:
            reasons.append("bench depth only — below your current {}".format(pos))
            score -= 1.0

        # Injury replacement / bye coverage
        if bye_counts:
            worst_bye = max(bye_counts.values())
            if p.get("bye") and bye_counts.get(p["bye"], 0) == 0 and worst_bye >= 3:
                score += 0.8
                reasons.append("bye {} is uncovered on your roster".format(
                    int(p["bye"])))

        if isinstance(p.get("status"), str) and p["status"]:
            score -= 1.5
            reasons.append("listed {}".format(p["status"]))

        pct = p.get("percent_owned")
        if pct is not None and pct == pct:
            try:
                pct = float(pct)
                if pct > 40:
                    score += 0.5
                    reasons.append("{:.0f}% rostered — others want him".format(pct))
                elif pct < 5:
                    reasons.append("{:.0f}% rostered — no rush".format(pct))
            except (TypeError, ValueError):
                pass

        drop = None
        if bench_depth <= 0 and not drops.empty:
            cand = drops.iloc[0]
            drop = {"name": cand.get("name") or cand.get("player_display_name"),
                    "position": cand.get("position"),
                    "ppg": float(cand.get("ppg") or 0.0)}
        elif bench_depth > 0:
            reasons.append("open bench spot — no drop needed")

        out.append({
            "name": p.get("name") or p.get("player_display_name"),
            "position": pos, "team": p.get("team"), "bye": p.get("bye"),
            "ppg": ppg, "upgrade": round(upgrade, 2),
            "score": round(score, 2), "reasons": reasons, "drop": drop,
            "priority_advice": priority_advice(score, waiver_priority),
        })

    out.sort(key=lambda r: -r["score"])
    return out[:top_n]


def priority_advice(score: float, waiver_priority: Optional[int]) -> str:
    """What a rolling-waiver claim is worth.

    Waiver priority is a single-use asset: claiming drops you to last. So the
    advice is about whether this player justifies spending it, not a bid.
    """
    if score >= 4:
        return ("Worth using your claim — this is a starting-lineup upgrade."
                + (" You are #{}.".format(waiver_priority)
                   if waiver_priority else ""))
    if score >= 2:
        return "Claim if your priority is mid-pack; otherwise try free agency."
    if score >= 0.5:
        return "Not worth burning priority — grab only if he clears waivers."
    return "Watchlist. Do not spend a claim."


def faab_bid(score: float, budget: int = 100) -> int:
    """FAAB percentage, for leagues that use it. Neither of these leagues
    does today, so this stays unused until one changes."""
    if score >= 6:
        return int(budget * 0.30)
    if score >= 4:
        return int(budget * 0.15)
    if score >= 2:
        return int(budget * 0.06)
    if score >= 0.5:
        return int(budget * 0.02)
    return 0
