"""Phase 0 CLI: log in to Yahoo and read real team data.

    python -m ffb.cli login          # one-time OAuth (interactive)
    python -m ffb.cli auth-url       # print the authorize URL only
    python -m ffb.cli exchange CODE  # non-interactive half of login
    python -m ffb.cli teams          # list my teams + leagues
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


def cmd_logout() -> int:
    tokens.clear()
    print("Tokens cleared from {}.".format(tokens.describe_store()))
    return 0


COMMANDS = {
    "login": cmd_login,
    "auth-url": cmd_auth_url,
    "exchange": cmd_exchange,
    "teams": cmd_teams,
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
