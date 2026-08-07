# FFB — Yahoo Fantasy Football optimizer

Local dashboard for managing multiple Yahoo fantasy teams: draft assistant,
waiver wire, and trade proposals — all scored under **my** league rules, not
generic PPR.

## Status

- **Phase 0 — auth + read real team data** ← current
- Phase 1 — league settings, scoring engine, team picker
- Phase 2 — projections + rankings under league scoring
- Phase 3 — draft assistant (GnG drafts Thu Aug 20, 6:00pm PDT)
- Phase 4 — waiver wire
- Phase 5 — trade assistant

## Leagues

| League | ID | Draft type | Notes |
|---|---|---|---|
| The League of Gains & Gains | 582600 | Live Standard Draft | Thu Aug 20 6:00pm PDT, 1:45/pick |
| League of Legends | 670028 | **Offline Draft** | No live Yahoo feed by definition — picks are entered manually |

Both leagues use **identical scoring**, so one scoring engine serves both.
Highlights: full PPR (1.0/rec), 4pt pass TD, INT −2, 25 yd/pt passing,
10 yd/pt rush+rec. DST **sack 2** and **TFL 0.5** are both overrides above Yahoo
default, which makes streaming defenses worth more here than generic rankings
imply. Roster: `QB, WR, WR, WR, RB, RB, TE, W/R/T, K, DEF, 5×BN`.

Three WR slots plus a flex in full PPR means WRs carry more value than standard
rankings suggest. The scoring engine accounts for this rather than assuming
a default league.

## Setup

### 1. Create a Yahoo developer app

You need to do this yourself — I can't create accounts on your behalf.
Go to <https://developer.yahoo.com/apps/create/> and set:

| Field | Value |
|---|---|
| Application Name | e.g. `FFB Draft Assistant` (Yahoo rejects names containing "Yahoo") |
| Description | optional, e.g. `Personal fantasy football tool for my own leagues` |
| Homepage URL | leave blank |
| Redirect URI(s) | `oob` — see fallback below |
| OAuth Client Type | **Confidential Client** |
| API Permissions | **check neither box** |

**Confidential Client** is the option that issues a Client Secret, which this
code uses (Basic-auth header per Yahoo's docs). Public Client can't hold a
secret and would break the flow.

`oob` ("out of band") means Yahoo shows you a code to paste rather than
redirecting. This sidesteps Yahoo's HTTPS-callback requirement. If the form
rejects `oob` as invalid, use `https://localhost:8000/callback` instead and set
`YAHOO_REDIRECT_URI` in `.env` to match exactly. The browser will show a
connection error after you approve — that's expected; copy the `?code=` value
out of the address bar. `ffb.cli login` handles both paths.

### A note on the Fantasy Sports permission

Yahoo's current app-creation form has **no Fantasy Sports permission option**.
Older community guides (and Yahoo's own older form) describe an
`Installed Application` type with a `Fantasy Sports` → `Read` checkbox; that
form no longer exists. The two boxes now shown — OpenID Connect and TW Auction
— are both irrelevant here.

Yahoo now runs a gated access application at
<https://sports.yahoo.com/developer/access/>, which asks for expected user
counts and a Client ID and says their team reviews each submission. Whether
Fantasy API access actually requires that approval, or still works implicitly
for any app, is **unverified**.

**Verified 2026-08-07: approval IS required.** With a valid token from a
freshly created app, every Fantasy endpoint returns:

```
401 oauth_problem="additional_authorization_required"
```

This fails even on `game/nfl` (public, league-agnostic), so it is an app-level
entitlement, not a private-league permission. OAuth itself works fine — the app
simply is not allowlisted for Fantasy Sports data until Yahoo approves the
access request.

### 2. Configure

```bash
cp .env.example .env
```

Paste your Client ID and Client Secret into `.env`. It is gitignored and must
never be committed.

### 3. Install

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 4. Log in

```bash
.venv/bin/python -m ffb.cli login
```

A browser opens, you approve, Yahoo shows a code, you paste it once. Tokens go
into the **macOS Keychain** (not a file). Access tokens last 1 hour and refresh
automatically afterward — you should not need to log in again.

### 5. Verify

```bash
.venv/bin/python -m ffb.cli teams
```

Lists every team you manage, its league, and draft status. It also flags if
either uploaded league is missing from the login.

## Security

- Secrets live in `.env` (gitignored); nothing is hardcoded.
- OAuth tokens live in the macOS Keychain by default. Set
  `FFB_TOKEN_STORE=file` to fall back to `.ffb_tokens.json`, created `0600`
  and gitignored.
- This directory is its own git repo, deliberately. Your home folder
  `/Users/jay` is also a git repo with no `.gitignore`, so without this
  isolation a stray `git add` there could have swept up `.env`.
- `python -m ffb.cli logout` clears stored tokens.

## Yahoo API constraints (verified, not assumed)

- **No streaming or webhooks.** The Fantasy API is REST-only; live draft
  tracking means polling `draftresults` plus league `draft_status`.
- **No endpoint exists to submit a draft pick.** Yahoo's documented write
  operations cover transactions and roster moves, not draft selections. True
  auto-pick is therefore not deliverable over the official API — the draft
  assistant is recommend-only by design.
- **Rate limits are undocumented numerically.** Third-party projects converge
  on ~1000 requests/hour. The client enforces a minimum interval between calls.
  For reference: 12 teams × 15 rounds = 180 picks over ~2 hours; polling every
  8–10s stays comfortably inside that ceiling and is far faster than the
  1:45 pick clock requires.
- **Live-draft polling latency is unverified.** Public docs don't state how
  quickly `draftresults` reflects an in-progress pick. Plan: run a Yahoo mock
  draft around Aug 15 to measure it before the real draft, with manual pick
  entry built in as a fallback regardless.

## Tests

```bash
.venv/bin/python -m tests.test_parsing
```

Yahoo's JSON uses index-keyed dicts alongside `count`, and splits single
objects across lists of partial dicts. These tests pin that parsing against
realistic fixtures so regressions surface here rather than mid-draft.
