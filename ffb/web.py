"""Local web dashboard.

Serves on the LAN so a phone or tablet can reach it — which is what makes
pick entry work at the in-person offline draft. Because it is reachable by
anything on the network and it can read Yahoo data, it is PIN-gated.

Run:
    .venv/bin/uvicorn ffb.web:app --host 0.0.0.0 --port 8000
"""
import os
import secrets
import time
from typing import Dict, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import board as board_mod
from . import config, draft, league_state, leagues, trades, waivers, yahoo_auth

TEMPLATES = Jinja2Templates(directory=str(config.ROOT / "ffb" / "templates"))

app = FastAPI(title="FFB")
app.mount("/static",
          StaticFiles(directory=str(config.ROOT / "ffb" / "static")),
          name="static")

PIN = os.getenv("FFB_PIN", "").strip()
COOKIE = "ffb_session"
SESSION_TTL = 30 * 24 * 3600

# token -> expiry. In-memory: a restart logs you out, which is the right
# trade for a single-user local tool (no key management, no storage).
_sessions: Dict[str, float] = {}

# Building the board reads several CSVs and does real pandas work, so it is
# cached rather than rebuilt per request.
_board_cache: Dict[str, tuple] = {}
BOARD_TTL = 900


def _authed(request: Request) -> bool:
    if not PIN:
        return True  # no PIN configured — see the warning on the dashboard
    tok = request.cookies.get(COOKIE)
    if not tok:
        return False
    exp = _sessions.get(tok)
    if not exp or exp < time.time():
        _sessions.pop(tok, None)
        return False
    return True


def get_board(league_id: str):
    hit = _board_cache.get(league_id)
    if hit and hit[0] > time.time():
        return hit[1]
    built = board_mod.build(leagues.get(league_id))
    _board_cache[league_id] = (time.time() + BOARD_TTL, built)
    return built


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, bad: int = 0):
    return TEMPLATES.TemplateResponse(
        "login.html", {"request": request, "bad": bad})


_login_attempts: Dict[str, list] = {}
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW = 300


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _login_attempts.get(ip, []) if t > now - ATTEMPT_WINDOW]
    _login_attempts[ip] = hits
    return len(hits) >= MAX_ATTEMPTS


@app.post("/login")
def login_submit(request: Request, pin: str = Form(...)):
    # Public URL means an unthrottled PIN form is a brute-force target.
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        return RedirectResponse("/login?bad=2", status_code=303)
    # compare_digest so a wrong PIN cannot be found by timing the response.
    if not PIN or not secrets.compare_digest(pin.strip(), PIN):
        _login_attempts.setdefault(ip, []).append(time.time())
        return RedirectResponse("/login?bad=1", status_code=303)
    _login_attempts.pop(ip, None)
    tok = secrets.token_urlsafe(32)
    _sessions[tok] = time.time() + SESSION_TTL
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, tok, max_age=SESSION_TTL, httponly=True,
                    samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request):
    tok = request.cookies.get(COOKIE)
    if tok:
        _sessions.pop(tok, None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/", response_class=HTMLResponse)
def index(request: Request, league: str = "582600", pos: str = "",
          q: str = "", limit: int = 60):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)

    lg = leagues.get(league)
    df = get_board(league)

    if pos:
        df = df[df.position == pos.upper()]
    if q:
        df = df[df.player_display_name.str.contains(q, case=False, na=False)]

    rows = df.head(limit).to_dict("records")
    cov = board_mod.coverage_report(get_board(league))

    ctx = {
        "request": request,
        "rows": rows,
        "league": lg,
        "leagues": leagues.LEAGUES,
        "league_id": league,
        "pos": pos.upper(),
        "q": q,
        "coverage": cov,
        "unverified": leagues.UNVERIFIED_RULES,
        "gaps": leagues.KNOWN_GAPS,
        "no_pin": not PIN,
        "baseline_seasons": board_mod.BASELINE_SEASONS,
        "page": "board",
    }
    template = "_rows.html" if request.headers.get("HX-Request") else "board.html"
    return TEMPLATES.TemplateResponse(template, ctx)


def _draft_context(request: Request, league_id: str) -> dict:
    lg = leagues.get(league_id)
    board = get_board(league_id)
    d = draft.active_draft(league_id)
    if not d:
        return {"request": request, "league": lg, "leagues": leagues.LEAGUES,
                "league_id": league_id, "draft": None, "page": "draft"}

    rows = draft.picks(d["id"])
    drafted = [r["player"] for r in rows]
    mine = [r["player"] for r in rows if r["team_slot"] == d["my_slot"]]
    my_players = board[board.player_display_name.isin(mine)]

    made = len(rows)
    upcoming = draft.my_next_picks(d["my_slot"], lg.num_teams, made, count=2)
    next_pick = upcoming[0] if upcoming else None
    following = upcoming[1] if len(upcoming) > 1 else None
    on_clock = draft.slot_on_clock(made + 1, lg.num_teams)

    recs = draft.recommend(
        board, drafted, my_players, lg, d["my_slot"],
        next_pick or made + 1, following, top_n=6)

    return {
        "request": request, "league": lg, "leagues": leagues.LEAGUES,
        "league_id": league_id, "draft": d, "picks": rows[::-1],
        "recs": recs, "my_players": my_players.to_dict("records"),
        "needs": draft.roster_needs(my_players, lg),
        "made": made, "on_clock": on_clock,
        "my_turn": on_clock == d["my_slot"],
        "next_pick": next_pick, "round": made // lg.num_teams + 1,
        "page": "draft",
    }


@app.get("/draft", response_class=HTMLResponse)
def draft_view(request: Request, league: str = "582600"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    ctx = _draft_context(request, league)
    template = "_draft_body.html" if request.headers.get("HX-Request") \
        else "draft.html"
    return TEMPLATES.TemplateResponse(template, ctx)


@app.post("/draft/start")
def draft_start(request: Request, league: str = Form(...),
                my_slot: int = Form(...), rounds: int = Form(15)):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    draft.create_draft(leagues.get(league), my_slot, rounds)
    return RedirectResponse("/draft?league=" + league, status_code=303)


@app.post("/draft/pick", response_class=HTMLResponse)
def draft_pick(request: Request, league: str = Form(...),
               player: str = Form(...)):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    lg = leagues.get(league)
    d = draft.active_draft(league)
    if d and player.strip():
        board = get_board(league)
        match = board[board.player_display_name.str.lower()
                      == player.strip().lower()]
        pos = match.iloc[0].position if len(match) else None
        draft.add_pick(d["id"], player.strip(), pos, lg.num_teams)
    return TEMPLATES.TemplateResponse("_draft_body.html",
                                      _draft_context(request, league))


@app.post("/draft/undo", response_class=HTMLResponse)
def draft_undo(request: Request, league: str = Form(...)):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    d = draft.active_draft(league)
    if d:
        draft.undo_pick(d["id"])
    return TEMPLATES.TemplateResponse("_draft_body.html",
                                      _draft_context(request, league))


# --------------------------------------------------------------------------
# Yahoo OAuth over the web. Required when hosted: there is no Keychain on
# Linux and no terminal to run the oob flow. Yahoo requires an HTTPS redirect
# URI, which is exactly what the host provides.
# --------------------------------------------------------------------------
_oauth_states: Dict[str, float] = {}


@app.get("/auth/start")
def auth_start(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time() + 600
    return RedirectResponse(yahoo_auth.authorize_url(state=state),
                            status_code=303)


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request, code: str = "", state: str = "",
                  error: str = ""):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    if error:
        return HTMLResponse("<pre>Yahoo returned: {}</pre>".format(error),
                            status_code=400)
    # CSRF: only accept a state we issued and have not already consumed.
    exp = _oauth_states.pop(state, None)
    if not exp or exp < time.time():
        return HTMLResponse(
            "<pre>Invalid or expired state. Start again at /auth/start</pre>",
            status_code=400)
    try:
        yahoo_auth.exchange_code(code)
    except Exception as exc:
        return HTMLResponse("<pre>{}</pre>".format(str(exc)[:800]),
                            status_code=400)
    league_state.invalidate()
    return RedirectResponse("/waivers", status_code=303)


@app.get("/waivers", response_class=HTMLResponse)
def waivers_view(request: Request, league: str = "582600", week: int = 1):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    lg = leagues.get(league)
    conn = league_state.status()
    recs = []
    if conn["connected"]:
        try:
            roster = league_state.attach_values(
                league_state.my_roster(league), league)
            avail = league_state.attach_values(
                league_state.available(league), league)
            recs = waivers.recommend(avail, roster, lg)
        except Exception as exc:  # surface, never fake results
            conn = {"connected": False, "reason": "api_error",
                    "detail": str(exc)[:300]}
    return TEMPLATES.TemplateResponse("waivers.html", {
        "request": request, "page": "waivers", "league": lg,
        "leagues": leagues.LEAGUES, "league_id": league,
        "conn": conn, "recs": recs, "week": week})


@app.get("/trades", response_class=HTMLResponse)
def trades_view(request: Request, league: str = "582600"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    lg = leagues.get(league)
    conn = league_state.status()
    proposals = []
    if conn["connected"]:
        try:
            my_key = league_state.my_team_key(league)
            raw = league_state.all_rosters(league)
            names = {t["team_key"]: t.get("name", t["team_key"])
                     for t in league_state.teams(league)}
            mine = league_state.attach_values(raw.get(my_key, []), league)
            others = {k: league_state.attach_values(v, league)
                      for k, v in raw.items() if k != my_key}
            proposals = trades.propose(mine, others, lg, names)
        except Exception as exc:
            conn = {"connected": False, "reason": "api_error",
                    "detail": str(exc)[:300]}
    return TEMPLATES.TemplateResponse("trades.html", {
        "request": request, "page": "trades", "league": lg,
        "leagues": leagues.LEAGUES, "league_id": league,
        "conn": conn, "proposals": proposals})


@app.get("/health")
def health():
    return {"ok": True, "pin_enabled": bool(PIN),
            "yahoo": league_state.status()}
