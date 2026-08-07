"""Scoring and valuation tests.

The scoring rules here were transcribed by hand from Yahoo's Scoring & Settings
page, so they are pinned against worked examples. A silent scoring error would
corrupt every downstream recommendation while still looking plausible.

    .venv/bin/python -m tests.test_scoring
"""
import pandas as pd

from ffb import leagues, scoring, valuation

LG = leagues.get("582600")
RESULTS = []


def check(label, got, want, tol=1e-6):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    RESULTS.append(ok)
    print("[{}] {}".format("PASS" if ok else "FAIL", label))
    if not ok:
        print("       got {!r} want {!r}".format(got, want))
    return ok


def main():
    # --- Worked example: a full-PPR WR line -------------------------------
    # 8 rec, 120 rec yds, 1 rec TD
    #   = 8*1.0 + 120*0.1 + 1*6 = 8 + 12 + 6 = 26.0
    check("WR line (8/120/1 TD)",
          scoring.score_stats(
              {"receptions": 8, "receiving_yards": 120, "receiving_tds": 1}, LG),
          26.0)

    # --- QB line, with the league's -2 INT override ------------------------
    # 300 pass yds, 2 pass TD, 1 INT, 30 rush yds
    #   = 300*0.04 + 2*4 + 1*(-2) + 30*0.1 = 12 + 8 - 2 + 3 = 21.0
    check("QB line (300/2TD/1INT/30rush)",
          scoring.score_stats({
              "passing_yards": 300, "passing_tds": 2,
              "passing_interceptions": 1, "rushing_yards": 30}, LG),
          21.0)

    # INT is -2 here, not Yahoo's default -1. Guard the override explicitly.
    check("INT override is -2",
          scoring.score_stats({"passing_interceptions": 1}, LG), -2.0)

    # Full PPR: 1.0/reception, not Yahoo's 0.5 default.
    check("reception is 1.0 (full PPR)",
          scoring.score_stats({"receptions": 1}, LG), 1.0)

    # --- Kicker tiers ------------------------------------------------------
    # Yahoo's "50+ = 5" spans nflverse's 50_59 and 60_ columns.
    check("FG 50-59 and 60+ both score 5",
          scoring.score_stats({"fg_made_50_59": 1, "fg_made_60_": 1}, LG),
          10.0)
    check("FG 0-19/20-29/30-39 all score 3",
          scoring.score_stats({"fg_made_0_19": 1, "fg_made_20_29": 1,
                               "fg_made_30_39": 1}, LG), 9.0)
    check("FG 40-49 scores 4",
          scoring.score_stats({"fg_made_40_49": 1}, LG), 4.0)

    # --- DST overrides -----------------------------------------------------
    check("sack is 2 (override, Yahoo default 1)",
          scoring.score_dst({"def_sacks": 1}, None, LG), 2.0)
    check("TFL is 0.5 (override, Yahoo default 0)",
          scoring.score_dst({"def_tackles_for_loss": 4}, None, LG), 2.0)

    # --- Points-allowed tiers, including the inferred 21-27 = 0 -----------
    for pa, want in [(0, 10.0), (6, 7.0), (7, 4.0), (13, 4.0), (14, 1.0),
                     (20, 1.0), (21, 0.0), (27, 0.0), (28, -1.0),
                     (34, -1.0), (35, -4.0), (60, -4.0)]:
        check("PA {} -> {}".format(pa, want),
              scoring.points_allowed_points(pa, LG), want)

    # --- Replacement level reflects the real roster shape ------------------
    # 12 teams x 3 WR = 36 WR starters, plus a flex that mostly goes to WR/RB.
    # So WR replacement must sit deeper than RB replacement's rank.
    rows = []
    for pos, n, base in [("WR", 80, 20.0), ("RB", 60, 20.0),
                         ("TE", 40, 14.0), ("QB", 40, 24.0),
                         ("K", 30, 10.0), ("DEF", 30, 12.0)]:
        for i in range(n):
            rows.append({"player_display_name": "{}{}".format(pos, i),
                         "position": pos, "ppg": base - i * 0.25})
    df = pd.DataFrame(rows)
    levels = valuation.replacement_levels(df, LG)

    # Ranks are 1-indexed here: the synthetic pool starts at index 0, so the
    # player whose ppg is `base - i*0.25` is the (i+1)th at that position.
    def rank_of(pos, base):
        return int(round((base - levels[pos]) / 0.25)) + 1

    # With 36 dedicated WR slots plus flex, WR replacement must be past WR36.
    wr_rank = rank_of("WR", 20.0)
    check("WR replacement sits past WR36 (3WR + flex)", wr_rank > 36, True)
    # RB has 24 dedicated slots; flex pushes it past 24 too.
    rb_rank = rank_of("RB", 20.0)
    check("RB replacement sits past RB24", rb_rank > 24, True)
    # TE has 12 dedicated slots and rarely wins flex, so it stays shallow.
    te_rank = rank_of("TE", 14.0)
    check("TE replacement near TE12", 12 <= te_rank <= 20, True)
    # Flex must be allocated, not ignored: 12 flex slots have to land
    # somewhere across WR/RB/TE, pushing those replacement levels deeper.
    depth_past_dedicated = (wr_rank - 37) + (rb_rank - 25) + (te_rank - 13)
    check("12 flex slots allocated across WR/RB/TE",
          depth_past_dedicated >= 12, True)

    # --- Tiers -------------------------------------------------------------
    tiered = valuation.add_tiers(
        valuation.add_vor(df, LG), by="vor")
    check("tiers assigned (>=1 per position)",
          bool((tiered.tier >= 1).all()), True)

    print("\n{} / {} passed".format(sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
