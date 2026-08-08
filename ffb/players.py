"""Per-player detail: availability, health, and pickup score.

A ranked list tells you the order; it does not tell you why a name is worth a
claim or what you would be inheriting. This assembles the three things that
actually decide a waiver move:

  availability — is he free, on waivers, or rostered elsewhere, and how
                 contested is he
  health       — injury designation, and what it means for this week
  pickup score — value *relative to your roster*, not in the abstract

The pickup score is deliberately roster-relative. "Best player available" is
the wrong question: a WR4 who never cracks your lineup is worth nothing, and a
mediocre TE is worth a lot if your TE slot is empty. Where the roster is
unknown (Yahoo not yet connected) the score is reported as league-generic and
labelled as such, rather than pretending to a precision it does not have.
"""
from typing import Dict, List, Optional

import pandas as pd

from . import board as board_mod
from .leagues import League

# Yahoo injury designations, worst first, with what they practically mean.
INJURY_NOTES = {
    "IR": ("Injured reserve", "Out for an extended period — a stash, not a starter."),
    "PUP": ("Physically unable to perform", "Cannot play early in the season."),
    "NFI": ("Non-football injury", "Unavailable; timeline usually unclear."),
    "O": ("Out", "Will not play this week."),
    "D": ("Doubtful", "Unlikely to play — roughly 25% of doubtful players suit up."),
    "Q": ("Questionable", "Genuine game-time decision; have a backup ready."),
    "P": ("Probable", "Expected to play."),
    "DNR": ("Did not report", "Not with the team — treat as unavailable."),
    "SUSP": ("Suspended", "Ineligible until the suspension ends."),
}


def _norm_status(status: Optional[str]) -> Optional[str]:
    if not isinstance(status, str) or not status.strip():
        return None
    s = status.strip().upper()
    aliases = {"QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "O",
               "PROBABLE": "P", "SUSPENDED": "SUSP"}
    return aliases.get(s, s)


def health(row) -> Dict[str, object]:
    """Injury designation plus a plain reading of what it means."""
    code = _norm_status(row.get("injury_status") or row.get("status"))
    if not code:
        return {"code": None, "label": "No designation",
                "note": "No injury designation reported.", "severity": 0}
    label, note = INJURY_NOTES.get(code, (code, "Designation reported by Yahoo."))
    severity = {"IR": 4, "PUP": 4, "NFI": 4, "SUSP": 4, "DNR": 4,
                "O": 3, "D": 2, "Q": 1, "P": 0}.get(code, 1)
    return {"code": code, "label": label, "note": note, "severity": severity}


def availability(row) -> Dict[str, object]:
    """Whether he can be added, and how contested he is.

    percent_owned comes from Yahoo and is the single best signal of whether a
    claim will be contested — it is the league-wide version of "does anyone
    else want him".
    """
    status = (row.get("ownership_status") or row.get("avail_status") or "")
    pct = row.get("percent_owned")
    try:
        pct = float(pct) if pct is not None and pct == pct else None
    except (TypeError, ValueError):
        pct = None

    if status == "W":
        state, note = "On waivers", "Must be claimed, not added directly."
    elif status == "FA":
        state, note = "Free agent", "Can be added immediately."
    elif status == "T":
        state, note = "Rostered", "Owned by another manager — trade target only."
    else:
        state, note = "Unknown", "Availability needs a live Yahoo connection."

    if pct is not None:
        if pct >= 60:
            contest = "Heavily rostered ({:.0f}%) — expect competition.".format(pct)
        elif pct >= 25:
            contest = "Moderately rostered ({:.0f}%) — likely contested.".format(pct)
        elif pct >= 5:
            contest = "Lightly rostered ({:.0f}%).".format(pct)
        else:
            contest = "Almost unrostered ({:.0f}%) — no rush.".format(pct)
    else:
        contest = None

    return {"state": state, "note": note, "percent_owned": pct,
            "contest": contest}


def pickup_score(row, roster: Optional[pd.DataFrame],
                 league: League) -> Dict[str, object]:
    """How much adding this player would actually improve the lineup.

    Returns the score, a 0-100 display value, and the reasoning behind it.
    """
    from . import waivers

    ppg = float(row.get("ppg") or 0.0)
    pos = row.get("position")
    reasons: List[str] = []

    roster_known = roster is not None and not roster.empty
    if roster_known:
        baseline = waivers.starter_baseline(roster, league)
        base = baseline.get(pos, 0.0)
        upgrade = ppg - base
        score = upgrade
        if base == 0:
            reasons.append("your {} slot is unfilled — anyone starts".format(pos))
        elif upgrade > 2:
            reasons.append("{:+.1f} ppg over your worst starting {}".format(
                upgrade, pos))
        elif upgrade > 0:
            reasons.append("only {:+.1f} ppg over your current {}".format(
                upgrade, pos))
        else:
            reasons.append("worse than every {} you already start".format(pos))
    else:
        # No roster: fall back to value over replacement, which is
        # league-accurate but not roster-accurate.
        score = float(row.get("vor") or 0.0)
        upgrade = None
        reasons.append("scored against league replacement level, "
                       "not your roster (connect Yahoo for a roster-aware score)")

    h = health(row)
    if h["severity"] >= 3:
        score -= 3.0
        reasons.append("{} — no immediate lineup help".format(h["label"].lower()))
    elif h["severity"] == 2:
        score -= 1.5
        reasons.append("doubtful this week")
    elif h["severity"] == 1:
        score -= 0.5
        reasons.append("questionable — game-time decision")

    av = availability(row)
    if av["percent_owned"] is not None and av["percent_owned"] >= 40:
        score += 0.5
        reasons.append("others are rostering him")

    if row.get("value_source") == "draft capital":
        hr = row.get("hit_rate")
        reasons.append("rookie projection{}".format(
            " — {:.0f}% hit rate".format(hr * 100) if hr == hr and hr else ""))

    display = _to_display(score, roster_known)
    return {"score": round(score, 2), "display": display,
            "upgrade": round(upgrade, 2) if upgrade is not None else None,
            "roster_aware": roster_known, "reasons": reasons,
            "verdict": verdict(score, roster_known)}


def _to_display(score: float, roster_aware: bool) -> int:
    """Map a raw score to 0-100 without saturating.

    A linear map clipped at both ends put the top two players at 100 and every
    below-replacement player at 0 — and on a waiver wire almost everything is
    below replacement, so the number collapsed exactly where it needed to
    discriminate. A logistic curve keeps strict ordering across the whole
    range while giving the most resolution near the decision boundary.

    Centre and steepness differ by mode because the underlying quantities do:
    roster-aware scores are ppg upgrades over a starter (a tight band around
    zero), while league-generic scores are value over replacement (much wider).
    """
    import math

    centre, steepness = (1.0, 0.55) if roster_aware else (0.0, 0.32)
    return int(round(100 / (1 + math.exp(-steepness * (score - centre)))))


def verdict(score: float, roster_aware: bool) -> str:
    if not roster_aware:
        if score >= 4:
            return "Strong league-wide value"
        if score >= 1:
            return "Startable in this league"
        if score >= -2:
            return "Bench depth"
        return "Below replacement"
    if score >= 4:
        return "Priority add — clear starting upgrade"
    if score >= 2:
        return "Worth a claim"
    if score >= 0.5:
        return "Only if he clears waivers"
    return "Watchlist"


def detail(name: str, league: League, roster: Optional[pd.DataFrame] = None,
           extra: Optional[dict] = None) -> Optional[dict]:
    """Everything worth showing for one player."""
    board = board_mod.build(league)
    key = board_mod.norm_name(name)
    match = board[board.player_display_name.map(board_mod.norm_name) == key]
    if match.empty:
        return None
    row = match.iloc[0].to_dict()
    if extra:
        row.update({k: v for k, v in extra.items() if v is not None})

    same_pos = board[(board.position == row["position"]) & (board.vor.notna())]
    same_pos = same_pos.sort_values("vor", ascending=False).reset_index(drop=True)
    rank = int(same_pos[same_pos.player_display_name
                        == row["player_display_name"]].index[0]) + 1 \
        if (same_pos.player_display_name == row["player_display_name"]).any() else None
    tier_peers = int((same_pos.tier == row.get("tier")).sum())

    return {
        "name": row["player_display_name"],
        "position": row["position"],
        "team": row.get("team"),
        "bye": row.get("bye"),
        "ppg": round(float(row.get("ppg") or 0), 1),
        "vor": round(float(row.get("vor") or 0), 1),
        "tier": row.get("tier"),
        "position_rank": rank,
        "tier_peers": tier_peers,
        "value_source": row.get("value_source"),
        "seasons_used": row.get("seasons_used"),
        "health": health(row),
        "availability": availability(row),
        "pickup": pickup_score(row, roster, league),
    }
