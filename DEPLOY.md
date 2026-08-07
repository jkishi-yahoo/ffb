# Deploying to Render

## Before you start

Two facts that shape everything:

- **The URL is public.** Anyone who finds it can try the PIN. Use a long
  random string, not four digits. Login is rate-limited to 8 attempts per 5
  minutes per IP, but that is a backstop, not a substitute for a real secret.
- **Free plan has no persistent disk and sleeps after 15 minutes.** Tokens and
  the data cache are wiped on restart, so you re-authorise Yahoo each time and
  the first request after waking takes ~10s. Fine for a weekly waiver check.
  Not fine on a 1:45 draft clock — run `starter` for draft week.

## 1. Push the repo

Render deploys from GitHub, so the repo has to exist first.

## 2. Create the service

New → Blueprint → point at this repo. `render.yaml` defines everything except
the secrets.

## 3. Set env vars in the Render dashboard

| Key | Value |
|---|---|
| `YAHOO_CLIENT_ID` | from your Yahoo app |
| `YAHOO_CLIENT_SECRET` | from your Yahoo app |
| `YAHOO_REDIRECT_URI` | `https://<your-service>.onrender.com/auth/callback` |
| `FFB_PIN` | a long random string |

Generate a PIN:

    python3 -c "import secrets; print(secrets.token_urlsafe(24))"

## 4. Register the redirect URI with Yahoo

At <https://developer.yahoo.com/apps/>, edit your app and add:

    https://<your-service>.onrender.com/auth/callback

as a Redirect URI. It must match `YAHOO_REDIRECT_URI` **exactly** — a trailing
slash difference is enough to fail. Keep `oob` in the list too if you still
want the local CLI login to work.

## 5. Authorise Yahoo from the web

Once deployed, log in with your PIN and visit `/auth/start`. That runs the
OAuth flow in the browser and stores the token on the mounted disk — no
terminal needed, which is the point.

## Local development is unchanged

    .venv/bin/uvicorn ffb.web:app --host 0.0.0.0 --port 8000

Locally `FFB_DATA_DIR` defaults to `./data` and tokens go to the macOS
Keychain. Nothing about the hosted config affects that.

## What does NOT get deployed

`.env` and your OAuth tokens are gitignored and never leave your machine. The
hosted instance gets its own credentials from Render env vars and its own
token from its own `/auth/start` flow.
