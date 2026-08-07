"""Rookie projections from draft capital.

Rookies have no NFL stats, so history cannot value them directly. Draft capital
is the strongest freely-available predictor of rookie fantasy production, so
this fits an empirical curve: for each position and draft-pick range, what did
past rookies actually score *under this league's rules*?

Two numbers come out, and they answer different questions:

  proj_ppg  — expected points per game *given the player is fantasy-relevant*
              (played >= MIN_GAMES). Directly comparable to the veteran ppg on
              the board, which applies the same filter.
  hit_rate  — probability a rookie at that slot is fantasy-relevant at all.
              Rookies who never played are counted as misses, not dropped;
              dropping them would inflate every projection.

`risk_adj_ppg = proj_ppg * hit_rate` is what the board ranks on, so a boom/bust
late-round flier does not outrank a safe starter on upside alone. Both are
shown, because which one matters depends on roster construction.
"""
from typing import Dict, Optional, Tuple

import pandas as pd

from . import data, scoring
from .leagues import League

DRAFT_PICKS_URL = ("https://github.com/nflverse/nflverse-data/releases/"
                   "download/draft_picks/draft_picks.csv")

SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
MIN_GAMES = 4          # matches the board's relevance filter
TRAIN_SEASONS = range(2012, 2025)   # 2025 is held out as a sanity check
SHRINK_K = 5.0         # pseudo-observations pulling sparse buckets to the
                       # position mean, so a 3-player cell is not trusted

# (label, inclusive_max_pick). UDFAs fall through to the final bucket.
PICK_BUCKETS: Tuple[Tuple[str, int], ...] = (
    ("1-10", 10),
    ("11-32", 32),
    ("33-64", 64),
    ("65-105", 105),
    ("106-150", 150),
    ("151-262", 262),
    ("UDFA", 10 ** 6),
)


def bucket_for(pick: Optional[float]) -> str:
    if pick is None or pd.isna(pick):
        return "UDFA"
    for label, ceiling in PICK_BUCKETS:
        if pick <= ceiling:
            return label
    return "UDFA"


def draft_picks() -> pd.DataFrame:
    return data.fetch_csv(DRAFT_PICKS_URL, "draft_picks.csv",
                          ttl=7 * 24 * 3600)


def _rookie_season_rows(season: int, league: League) -> pd.DataFrame:
    """Every drafted skill player from `season`, with their rookie-year points.

    Left-joined from the draft class, so players who never took a snap appear
    with zero games — the bust half of the distribution.
    """
    picks = draft_picks()
    cls = picks[(picks.season == season) &
                (picks.position.isin(SKILL_POSITIONS))].copy()
    if cls.empty:
        return pd.DataFrame()

    stats = data.player_stats(season).copy()
    stats["points"] = scoring.score_frame(stats, league)
    # draft_picks also has `games` (career) and `position`/`team`. Rename the
    # stat-side columns explicitly rather than relying on merge suffixes,
    # which silently produced games_x/games_y and zeroed the whole curve.
    stats = stats[[c for c in ["player_id", "points", "games"]
                   if c in stats.columns]].rename(
        columns={"games": "rookie_games", "points": "rookie_points"})

    merged = cls.merge(stats, left_on="gsis_id", right_on="player_id",
                       how="left")
    if "rookie_games" not in merged.columns:
        raise RuntimeError(
            "stats for {} lack a games column; rookie curve would be "
            "silently zero".format(season))
    merged["rookie_games"] = merged["rookie_games"].fillna(0.0)
    merged["rookie_points"] = merged["rookie_points"].fillna(0.0)
    merged["games"] = merged["rookie_games"]
    merged["points"] = merged["rookie_points"]
    merged["relevant"] = merged["games"] >= MIN_GAMES
    merged["ppg"] = merged.apply(
        lambda r: r["points"] / r["games"] if r["games"] else 0.0, axis=1)
    merged["bucket"] = merged["pick"].map(bucket_for)
    merged["draft_season"] = season
    return merged[["draft_season", "pfr_player_name", "position", "pick",
                   "bucket", "games", "points", "ppg", "relevant"]]


def training_table(league: League) -> pd.DataFrame:
    frames = [_rookie_season_rows(s, league) for s in TRAIN_SEASONS]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise RuntimeError("No rookie training data could be assembled")
    return pd.concat(frames, ignore_index=True)


def fit_curve(league: League) -> pd.DataFrame:
    """Expected ppg and hit rate for each (position, pick bucket).

    Sparse cells — a handful of top-10 TEs across a decade — are shrunk toward
    the position mean rather than trusted at face value.
    """
    train = training_table(league)
    pos_means = train.groupby("position")[["ppg", "relevant"]].apply(
        lambda g: pd.Series({
            "pos_ppg": g.loc[g.relevant, "ppg"].mean() if g.relevant.any() else 0.0,
            "pos_hit": g.relevant.mean(),
        })
    )

    rows = []
    for (pos, bucket), grp in train.groupby(["position", "bucket"]):
        n = len(grp)
        rel = grp[grp.relevant]
        raw_ppg = rel.ppg.mean() if len(rel) else 0.0
        raw_hit = grp.relevant.mean()
        p_ppg = float(pos_means.loc[pos, "pos_ppg"])
        p_hit = float(pos_means.loc[pos, "pos_hit"])
        rows.append({
            "position": pos,
            "bucket": bucket,
            "n": n,
            "raw_ppg": round(raw_ppg, 2),
            "raw_hit": round(raw_hit, 3),
            "proj_ppg": round((n * raw_ppg + SHRINK_K * p_ppg) / (n + SHRINK_K), 2),
            "hit_rate": round((n * raw_hit + SHRINK_K * p_hit) / (n + SHRINK_K), 3),
        })
    curve = pd.DataFrame(rows)
    curve["risk_adj_ppg"] = (curve.proj_ppg * curve.hit_rate).round(2)
    return curve


# A player drafted two years ago who still has no production is not the same
# prospect as a fresh pick — the draft-capital prior has been partly falsified
# by the intervening silence. Decay per year since being drafted.
STALE_DECAY = 0.6


def project(season: int, league: League, classes: int = 1) -> pd.DataFrame:
    """Projections for the `classes` most recent draft classes up to `season`.

    Covering more than the newest class matters: a second-year player who
    barely played as a rookie has no production baseline *and* falls outside
    the current class, so with classes=1 they land on the board at zero.
    """
    curve = fit_curve(league)
    picks = draft_picks()
    seasons = [season - i for i in range(classes)]
    cls = picks[(picks.season.isin(seasons)) &
                (picks.position.isin(SKILL_POSITIONS))].copy()
    if cls.empty:
        return pd.DataFrame()
    cls["bucket"] = cls["pick"].map(bucket_for)
    out = cls.merge(curve, on=["position", "bucket"], how="left")
    out["years_since_draft"] = season - out["season"]
    decay = STALE_DECAY ** out["years_since_draft"]
    out["proj_ppg"] = (out.proj_ppg * decay).round(2)
    out["risk_adj_ppg"] = (out.risk_adj_ppg * decay).round(2)
    out["player_display_name"] = out["pfr_player_name"]
    cols = ["player_display_name", "position", "team", "season", "round",
            "pick", "bucket", "years_since_draft", "proj_ppg", "hit_rate",
            "risk_adj_ppg", "n"]
    keep = [c for c in cols if c in out.columns]
    return out[keep].sort_values("risk_adj_ppg", ascending=False).reset_index(
        drop=True)


def holdout_check(league: League, season: int = 2025) -> pd.DataFrame:
    """Compare the fitted curve against a season it was not trained on.

    A projection method that is never checked against reality is just a
    confident-looking guess.
    """
    curve = fit_curve(league)
    actual = _rookie_season_rows(season, league)
    if actual.empty:
        return pd.DataFrame()
    merged = actual.merge(curve[["position", "bucket", "proj_ppg", "hit_rate"]],
                          on=["position", "bucket"], how="left")
    rows = []
    for bucket, grp in merged.groupby("bucket"):
        rel = grp[grp.relevant]
        rows.append({
            "bucket": bucket,
            "n": len(grp),
            "predicted_ppg": round(grp.proj_ppg.mean(), 2),
            "actual_ppg": round(rel.ppg.mean(), 2) if len(rel) else 0.0,
            "predicted_hit": round(grp.hit_rate.mean(), 3),
            "actual_hit": round(grp.relevant.mean(), 3),
        })
    order = {label: i for i, (label, _) in enumerate(PICK_BUCKETS)}
    return pd.DataFrame(rows).sort_values(
        "bucket", key=lambda s: s.map(order)).reset_index(drop=True)
