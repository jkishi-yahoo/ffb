"""Live draft state and pick recommendations.

State is persisted to SQLite, deliberately: a draft is two hours of
irreplaceable input, and losing it to a server restart at pick 90 would be
unrecoverable. Picks are entered manually, which is the only option for the
offline league and the fallback for the live one while Yahoo API access is
pending.

Recommendations are scored on five things, and each contributes a plain-English
reason so the suggestion can be argued with rather than just obeyed:

  value      — VOR under this league's scoring
  need       — does it fill a starting slot still empty
  urgency    — will this tier survive until the next pick
  bye        — does it stack byes with players already rostered
  market     — is it early or late versus where the market takes him
"""
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config
from .leagues import League

DB_PATH = config.ROOT / "data" / "drafts.db"

FLEX_ELIGIBLE = ("WR", "RB", "TE")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id TEXT NOT NULL,
            my_slot INTEGER NOT NULL,
            rounds INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            overall INTEGER NOT NULL,
            team_slot INTEGER NOT NULL,
            player TEXT NOT NULL,
            position TEXT,
            UNIQUE(draft_id, overall)
        )""")
    conn.commit()
    return conn


def create_draft(league: League, my_slot: int, rounds: int = 15) -> int:
    conn = _conn()
    conn.execute("UPDATE drafts SET active=0 WHERE league_id=?",
                 (league.league_id,))
    cur = conn.execute(
        "INSERT INTO drafts (league_id, my_slot, rounds) VALUES (?,?,?)",
        (league.league_id, my_slot, rounds))
    conn.commit()
    return cur.lastrowid


def active_draft(league_id: str) -> Optional[sqlite3.Row]:
    conn = _conn()
    return conn.execute(
        "SELECT * FROM drafts WHERE league_id=? AND active=1 "
        "ORDER BY id DESC LIMIT 1", (league_id,)).fetchone()


def picks(draft_id: int) -> List[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM picks WHERE draft_id=? ORDER BY overall",
        (draft_id,)).fetchall()


def slot_on_clock(overall: int, num_teams: int) -> int:
    """Snake order: odd rounds run 1..N, even rounds run N..1."""
    rnd = (overall - 1) // num_teams
    idx = (overall - 1) % num_teams
    return idx + 1 if rnd % 2 == 0 else num_teams - idx


def add_pick(draft_id: int, player: str, position: Optional[str],
             num_teams: int) -> int:
    conn = _conn()
    row = conn.execute(
        "SELECT COALESCE(MAX(overall), 0) AS m FROM picks WHERE draft_id=?",
        (draft_id,)).fetchone()
    overall = row["m"] + 1
    conn.execute(
        "INSERT INTO picks (draft_id, overall, team_slot, player, position) "
        "VALUES (?,?,?,?,?)",
        (draft_id, overall, slot_on_clock(overall, num_teams), player, position))
    conn.commit()
    return overall


def undo_pick(draft_id: int) -> None:
    conn = _conn()
    conn.execute(
        "DELETE FROM picks WHERE draft_id=? AND overall=("
        "SELECT MAX(overall) FROM picks WHERE draft_id=?)",
        (draft_id, draft_id))
    conn.commit()


def my_next_picks(my_slot: int, num_teams: int, after_overall: int,
                  count: int = 3) -> List[int]:
    """The next `count` overall pick numbers belonging to `my_slot`."""
    out, n = [], after_overall + 1
    while len(out) < count and n <= num_teams * 30:
        if slot_on_clock(n, num_teams) == my_slot:
            out.append(n)
        n += 1
    return out


def roster_needs(my_players: pd.DataFrame, league: League) -> Dict[str, int]:
    """Starting slots still unfilled, flex counted separately."""
    have = my_players.position.value_counts().to_dict() if len(my_players) else {}
    needs = {}
    for pos, want in league.roster.starters.items():
        needs[pos] = max(0, want - have.get(pos, 0))
    flex_want = sum(league.roster.flex.values())
    surplus = sum(max(0, have.get(p, 0) - league.roster.starters.get(p, 0))
                  for p in FLEX_ELIGIBLE)
    needs["FLEX"] = max(0, flex_want - surplus)
    return needs


def recommend(board: pd.DataFrame, drafted: List[str], my_players: pd.DataFrame,
              league: League, my_slot: int, next_pick: int,
              following_pick: Optional[int], top_n: int = 5) -> List[dict]:
    """Rank the best available picks, each with reasons."""
    taken = {p.lower().strip() for p in drafted}
    avail = board[~board.player_display_name.str.lower().str.strip().isin(taken)]
    avail = avail[avail.vor.notna()].copy()
    if avail.empty:
        return []

    needs = roster_needs(my_players, league)
    my_byes = my_players.bye.dropna().tolist() if len(my_players) else []
    picks_until_next = (following_pick - next_pick) if following_pick else 0

    out = []
    for _, p in avail.head(60).iterrows():
        reasons, score = [], float(p.vor)
        pos = p.position

        # --- roster need -------------------------------------------------
        if needs.get(pos, 0) > 0:
            score += 2.5
            reasons.append("fills an empty {} slot".format(pos))
        elif pos in FLEX_ELIGIBLE and needs.get("FLEX", 0) > 0:
            score += 1.2
            reasons.append("fills your flex")
        else:
            score -= 1.5
            reasons.append("{} already covered — depth only".format(pos))

        # --- tier urgency -------------------------------------------------
        # Only claim urgency when the tier is genuinely thin. The earlier
        # threshold (left <= picks_until_next/3) fired on nearly every player
        # and reported "last of tier" while several remained — manufactured
        # scarcity is worse than none, because it drives reaches.
        same_tier = avail[(avail.position == pos) & (avail.tier == p.tier)]
        left_in_tier = len(same_tier)
        # Roughly how many players go before my next pick, of which some
        # fraction will be this position.
        expected_gone = picks_until_next / max(len(FLEX_ELIGIBLE) + 2, 1)
        if left_in_tier == 1:
            score += 2.0
            reasons.append("last {} in tier {}".format(pos, int(p.tier)))
        elif left_in_tier <= 3 and picks_until_next and left_in_tier <= expected_gone:
            score += 1.2
            reasons.append("only {} left in tier {} — unlikely to last {} picks"
                           .format(left_in_tier, int(p.tier), picks_until_next))
        elif left_in_tier > 6:
            score -= 0.8
            reasons.append("{} others at this tier — can wait".format(
                left_in_tier - 1))

        # --- bye-week collision ------------------------------------------
        if p.bye == p.bye and my_byes.count(p.bye) >= 2:
            score -= 1.5
            reasons.append("3rd player on bye {}".format(int(p.bye)))

        # --- market timing (proxy, not true ADP) --------------------------
        if p.get("search_rank") == p.get("search_rank") and p.search_rank:
            delta = p.search_rank - next_pick
            if delta > 18:
                score += 1.0
                reasons.append("market has him ~{} picks later".format(int(delta)))
            elif delta < -18:
                score -= 1.0
                reasons.append("reach vs market by ~{} picks".format(int(-delta)))

        # --- risk flags ---------------------------------------------------
        if p.get("value_source") == "draft capital":
            hr = p.get("hit_rate")
            reasons.append("rookie{}".format(
                " — {:.0f}% hit rate".format(hr * 100) if hr == hr else ""))
        if isinstance(p.get("injury_status"), str) and p.injury_status:
            score -= 0.8
            reasons.append(p.injury_status.lower())

        out.append({
            "player": p.player_display_name, "position": pos,
            "team": p.team, "bye": p.bye, "ppg": p.ppg, "vor": p.vor,
            "tier": p.tier, "score": round(score, 2), "reasons": reasons,
        })

    out.sort(key=lambda r: -r["score"])
    return out[:top_n]
