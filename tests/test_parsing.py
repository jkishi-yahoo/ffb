"""Exercise the Yahoo JSON shape helpers against a realistic response fixture.

Yahoo's payloads are dicts keyed by stringified indices next to a "count", and
single objects are split across lists of partial dicts. These fixtures mirror
that shape so parsing regressions surface here, not on draft night.

    .venv/bin/python -m tests.test_parsing
"""
from ffb import yahoo_client as yc

TEAMS_FIXTURE = {
    "fantasy_content": {
        "users": {
            "0": {
                "user": [
                    {"guid": "ABC123"},
                    {
                        "games": {
                            "0": {
                                "game": [
                                    {"game_key": "461", "code": "nfl", "season": "2026"},
                                    {
                                        "teams": {
                                            "0": {
                                                "team": [
                                                    [
                                                        {"team_key": "461.l.582600.t.4"},
                                                        {"team_id": "4"},
                                                        {"name": "Gainz Train"},
                                                    ],
                                                    {"managers": []},
                                                ]
                                            },
                                            "1": {
                                                "team": [
                                                    [
                                                        {"team_key": "461.l.670028.t.9"},
                                                        {"team_id": "9"},
                                                        {"name": "Bot Lane"},
                                                    ],
                                                    {"managers": []},
                                                ]
                                            },
                                            "count": 2,
                                        }
                                    },
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            },
            "count": 1,
        }
    }
}

LEAGUES_FIXTURE = {
    "fantasy_content": {
        "users": {
            "0": {
                "user": [
                    {"guid": "ABC123"},
                    {
                        "games": {
                            "0": {
                                "game": [
                                    {"game_key": "461", "code": "nfl"},
                                    {
                                        "leagues": {
                                            "0": {
                                                "league": [
                                                    {
                                                        "league_key": "461.l.582600",
                                                        "league_id": "582600",
                                                        "name": "The League of Gains & Gains",
                                                        "draft_status": "predraft",
                                                        "num_teams": 12,
                                                        "scoring_type": "head",
                                                    }
                                                ]
                                            },
                                            "1": {
                                                "league": [
                                                    {
                                                        "league_key": "461.l.670028",
                                                        "league_id": "670028",
                                                        "name": "League of Legends",
                                                        "draft_status": "predraft",
                                                        "num_teams": 12,
                                                        "scoring_type": "head",
                                                    }
                                                ]
                                            },
                                            "count": 2,
                                        }
                                    },
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            },
            "count": 1,
        }
    }
}


def check(label, got, want):
    status = "PASS" if got == want else "FAIL"
    print("[{}] {}".format(status, label))
    if got != want:
        print("       got : {!r}".format(got))
        print("       want: {!r}".format(want))
    return got == want


def main():
    ok = True

    # numeric_items must skip "count" and preserve numeric order (not "10" < "2").
    coll = {"0": "a", "1": "b", "2": "c", "10": "k", "count": 4}
    ok &= check("numeric_items order/skip-count",
                list(yc.numeric_items(coll)), ["a", "b", "c", "k"])

    # merge must flatten nested lists of partial dicts into one object.
    split = [[{"team_key": "461.l.1.t.1"}, {"name": "X"}], {"managers": []}]
    ok &= check("merge nested partial dicts",
                yc.merge(split).get("name"), "X")

    # my_teams / my_leagues parse the real nesting.
    teams = yc._parse_teams(TEAMS_FIXTURE)
    ok &= check("teams count", len(teams), 2)
    ok &= check("team name", teams[0]["name"], "Gainz Train")
    ok &= check("league_key derived from team_key",
                teams[0]["league_key"], "461.l.582600")
    ok &= check("league_id derived", teams[0]["league_id"], "582600")
    ok &= check("second team league_id", teams[1]["league_id"], "670028")

    leagues = yc._parse_leagues(LEAGUES_FIXTURE)
    ok &= check("leagues count", len(leagues), 2)
    ok &= check("league name", leagues[0]["name"], "The League of Gains & Gains")
    ok &= check("draft_status", leagues[0]["draft_status"], "predraft")

    # Both uploaded leagues must be recognised.
    from ffb import config
    found = {lg["league_id"] for lg in leagues}
    ok &= check("uploaded leagues matched",
                found >= set(config.KNOWN_LEAGUES), True)

    print("\n{}".format("all passed" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
