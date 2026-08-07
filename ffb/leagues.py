"""League definitions, transcribed from the uploaded Yahoo Scoring & Settings pages.

Both leagues scored identically as of 2026-08-07, so they share SCORING_2026.
This is a *bootstrap* source: Yahoo's own /league/{key}/settings endpoint is
authoritative and should be diffed against this the moment API access lands
(see `ffb.cli verify-scoring`).

Point values are per-unit multipliers against nflverse stat column names, so
the scoring engine is a dot product rather than a pile of special cases.
"""
from typing import Dict, List, NamedTuple, Optional

# --------------------------------------------------------------------------
# Offense / kicker: nflverse column -> points per unit
# --------------------------------------------------------------------------
SCORING_2026: Dict[str, float] = {
    # Passing — "25 yards per point" = 0.04/yd
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "passing_interceptions": -2.0,          # league override; Yahoo default -1
    "passing_2pt_conversions": 2.0,
    # Rushing — "10 yards per point" = 0.1/yd
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "rushing_2pt_conversions": 2.0,
    # Receiving — full PPR; league override, Yahoo default is 0.5
    "receptions": 1.0,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "receiving_2pt_conversions": 2.0,
    # Turnovers
    "fumbles_lost_total": -2.0,
    # Return + misc TDs (all worth 6 here)
    "special_teams_tds": 6.0,
    "fumble_recovery_tds": 6.0,
    # Kicking — Yahoo's 50+ tier spans nflverse's 50_59 and 60_ columns
    "fg_made_0_19": 3.0,
    "fg_made_20_29": 3.0,
    "fg_made_30_39": 3.0,
    "fg_made_40_49": 4.0,
    "fg_made_50_59": 5.0,
    "fg_made_60_": 5.0,
    "pat_made": 1.0,
}

# --------------------------------------------------------------------------
# Team defense / special teams
# --------------------------------------------------------------------------
DST_SCORING_2026: Dict[str, float] = {
    "def_sacks": 2.0,                # league override; Yahoo default 1
    "def_interceptions": 2.0,
    "fumble_recovery_opp": 2.0,
    "def_tds": 6.0,
    "special_teams_tds": 6.0,
    "def_safeties": 2.0,
    "def_tackles_for_loss": 0.5,     # league override; Yahoo default 0
    # Deliberately absent — see KNOWN_GAPS:
    #   "fg_blocked"         : in nflverse team stats this is the team's OWN
    #                          kicker being blocked, not a block by its
    #                          defense. Scoring it would credit the wrong team.
    #   "extra_point_returned": no nflverse column; vanishingly rare.
}

# Scoring rules we cannot currently compute, and what it costs. Surfaced in the
# UI so a DST projection reads as "slightly conservative", not "authoritative".
KNOWN_GAPS = [
    ("Block Kick (2 pts)", "no def-side blocked-kick column in nflverse team "
                           "stats; ~1-2 per team per season, so ~2-4 pts"),
    ("Extra Point Returned (2 pts)", "no nflverse column; a few per league per "
                                     "season, effectively 0"),
]

# Points-allowed tiers: (inclusive_max_points_allowed, fantasy_points).
# NOTE: the 21-27 tier read as 0 from the League of Legends page but did not
# extract cleanly from the Gains & Gains page. 0 is the only value consistent
# with the surrounding monotonic tiers (1 above it, -1 below), so it is used
# here — but it is flagged for confirmation against the API.
DST_POINTS_ALLOWED: List[tuple] = [
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),   # <-- verify against Yahoo API when access lands
    (34, -1.0),
    (10**6, -4.0),
]

UNVERIFIED_RULES = [
    "DST Points Allowed 21-27 = 0 (inferred; did not extract from GnG page)",
]


class Roster(NamedTuple):
    starters: Dict[str, int]
    flex: Dict[str, int]      # slot name -> count, with eligible positions below
    flex_eligible: Dict[str, tuple]
    bench: int

    @property
    def total_slots(self) -> int:
        return sum(self.starters.values()) + sum(self.flex.values()) + self.bench

    @property
    def starting_slots(self) -> int:
        return sum(self.starters.values()) + sum(self.flex.values())


# QB, WR, WR, WR, RB, RB, TE, W/R/T, K, DEF, BN x5
ROSTER_2026 = Roster(
    starters={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1},
    flex={"W/R/T": 1},
    flex_eligible={"W/R/T": ("WR", "RB", "TE")},
    bench=5,
)


class League(NamedTuple):
    league_id: str
    name: str
    num_teams: int
    draft_type: str
    draft_time: Optional[str]
    scoring: Dict[str, float]
    dst_scoring: Dict[str, float]
    points_allowed: List[tuple]
    roster: Roster
    waiver_type: str
    # True when picks cannot be polled from Yahoo during the draft.
    manual_draft_only: bool


LEAGUES: Dict[str, League] = {
    "582600": League(
        league_id="582600",
        name="The League of Gains & Gains",
        num_teams=12,
        draft_type="Live Standard Draft",
        draft_time="2026-08-20 18:00 PDT",
        scoring=SCORING_2026,
        dst_scoring=DST_SCORING_2026,
        points_allowed=DST_POINTS_ALLOWED,
        roster=ROSTER_2026,
        waiver_type="continual rolling list",
        manual_draft_only=False,
    ),
    "670028": League(
        league_id="670028",
        name="League of Legends",
        num_teams=12,
        draft_type="Offline Draft",
        draft_time=None,
        scoring=SCORING_2026,
        dst_scoring=DST_SCORING_2026,
        points_allowed=DST_POINTS_ALLOWED,
        roster=ROSTER_2026,
        waiver_type="continual rolling list",
        # Offline draft: Yahoo has no live pick feed, by definition.
        manual_draft_only=True,
    ),
}


def get(league_id: str) -> League:
    if league_id not in LEAGUES:
        raise KeyError("Unknown league {!r}. Known: {}".format(
            league_id, ", ".join(LEAGUES)))
    return LEAGUES[league_id]
