"""Phase 0 CLI: log in to Yahoo and read real team data.

    python -m ffb.cli login          # one-time OAuth (interactive)
    python -m ffb.cli auth-url       # print the authorize URL only
    python -m ffb.cli exchange CODE  # non-interactive half of login
    python -m ffb.cli teams          # list my teams + leagues
    python -m ffb.cli board [league] [pos] [n]   # draft board, my scoring
    python -m ffb.cli show-refresh-token  # for seeding a hosted deploy
    python -m ffb.cli logout         # forget stored tokens

`auth-url` + `exchange` split `login` into two non-interactive steps, for
environments where a blocking input() prompt is awkward.
"""
import sys
import webbrowser

from . import config, tokens, yahoo_auth, yahoo_client


def cmd_login() -> int:
    config.require_credentials()
    url = yahoo_auth.authorize_url()

    if config.REDIRECT_URI == "oob":
        print("\n1. Opening Yahoo authorization in your browser.")
        print("   If it does not open, paste this URL yourself:\n")
        print("   " + url + "\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        print("2. Approve access. Yahoo will show you a short code.")
        code = input("3. Paste the code here: ").strip()
        if not code:
            print("No code entered — aborted.")
            return 1
    else:
        print("Redirect URI is {!r}.".format(config.REDIRECT_URI))
        print("Authorize here, then paste the ?code= value from the callback URL:\n")
        print("   " + url + "\n")
        code = input("code: ").strip()
        if not code:
            print("No code entered — aborted.")
            return 1

    yahoo_auth.exchange_code(code)
    print("\nLogged in. Tokens stored in: {}".format(tokens.describe_store()))
    print("Access tokens last 1 hour and refresh automatically from here on.")
    return 0


def cmd_auth_url() -> int:
    config.require_credentials()
    print(yahoo_auth.authorize_url())
    return 0


def cmd_exchange() -> int:
    if len(sys.argv) < 3:
        print("Usage: python -m ffb.cli exchange <code>", file=sys.stderr)
        return 2
    yahoo_auth.exchange_code(sys.argv[2])
    print("Logged in. Tokens stored in: {}".format(tokens.describe_store()))
    return 0


def cmd_teams() -> int:
    teams = yahoo_client.my_teams()
    if not teams:
        print("No NFL teams found for this login this season.")
        return 1

    leagues = {lg["league_key"]: lg for lg in yahoo_client.my_leagues()}

    print("\nSeason game_key: {}\n".format(yahoo_client.current_game_key()))
    print("Your teams ({}):\n".format(len(teams)))
    for t in teams:
        lg = leagues.get(t["league_key"], {})
        league_id = t["league_id"]
        known = config.KNOWN_LEAGUES.get(league_id)
        print("  {}".format(t.get("name", "(unnamed team)")))
        print("    team_key    : {}".format(t["team_key"]))
        print("    league      : {} (id {})".format(
            lg.get("name", known or "?"), league_id))
        print("    league_key  : {}".format(t["league_key"]))
        print("    draft_status: {}".format(lg.get("draft_status", "?")))
        print("    teams       : {}".format(lg.get("num_teams", "?")))
        print("    scoring     : {}".format(lg.get("scoring_type", "?")))
        if known and lg.get("name") and known != lg.get("name"):
            print("    NOTE: uploaded HTML called this {!r}".format(known))
        print()

    matched = {t["league_id"] for t in teams} & set(config.KNOWN_LEAGUES)
    missing = set(config.KNOWN_LEAGUES) - matched
    if missing:
        print("Leagues from your uploaded HTML not found on this login: {}".format(
            ", ".join(sorted(missing))))
    return 0


def cmd_board() -> int:
    """Draft board under a league's real scoring. Usage:
        board [league_id] [position] [top_n]
    """
    import pandas as pd

    from . import board as board_mod
    from . import leagues as leagues_mod

    league_id = sys.argv[2] if len(sys.argv) > 2 else "582600"
    pos = sys.argv[3].upper() if len(sys.argv) > 3 else None
    top_n = int(sys.argv[4]) if len(sys.argv) > 4 else 25

    lg = leagues_mod.get(league_id)
    pd.set_option("display.width", 220)
    b = board_mod.build(lg)
    if pos:
        b = b[b.position == pos]

    print("\n{} — {} teams, {}".format(lg.name, lg.num_teams, lg.draft_type))
    print("Scoring: full PPR, 4pt pass TD, INT -2 | DST sack 2, TFL 0.5")
    print("Baseline: {} actuals scored under this league; "
          "current rosters from Sleeper\n".format(board_mod.SEASON_BASELINE))

    cols = [c for c in ["player_display_name", "position", "team", "bye",
                        "ppg", "vor", "tier", "has_baseline", "rookie",
                        "injury_status"] if c in b.columns]
    print(b.head(top_n)[cols].to_string(index=False))

    cov = board_mod.coverage_report(b)
    print("\nCoverage: {} players — {} valued from production (recency-"
          "weighted {}), {} from draft capital, {} unvalued.".format(
              cov["players"], cov["from_production"],
              "/".join(str(s) for s in board_mod.BASELINE_SEASONS),
              cov["from_draft_capital"], cov["unvalued"]))
    print("Unvalued players are deep-roster names with no NFL production and "
          "no draft capital; they sort to the bottom.")
    if leagues_mod.UNVERIFIED_RULES:
        print("\nUnverified scoring rules (confirm via Yahoo API when access "
              "lands):")
        for rule in leagues_mod.UNVERIFIED_RULES:
            print("  - {}".format(rule))
    for label, why in leagues_mod.KNOWN_GAPS:
        print("  - not scored: {} — {}".format(label, why))
    return 0


def cmd_show_refresh_token() -> int:
    """Print the stored refresh token, for seeding FFB_REFRESH_TOKEN on a
    diskless host. Deliberately CLI-only: this is a credential and must never
    be reachable over HTTP."""
    stored = tokens.load()
    if not stored or not stored.get("refresh_token"):
        print("No refresh token stored. Run: python -m ffb.cli login",
              file=sys.stderr)
        return 1
    print(stored["refresh_token"])
    return 0


def cmd_logout() -> int:
    tokens.clear()
    print("Tokens cleared from {}.".format(tokens.describe_store()))
    return 0


COMMANDS = {
    "login": cmd_login,
    "board": cmd_board,
    "auth-url": cmd_auth_url,
    "exchange": cmd_exchange,
    "teams": cmd_teams,
    "show-refresh-token": cmd_show_refresh_token,
    "logout": cmd_logout,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 2
    try:
        return COMMANDS[sys.argv[1]]()
    except (config.ConfigError, yahoo_client.YahooError, RuntimeError) as exc:
        print("\nError: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
