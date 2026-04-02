"""Strava OAuth2 flow for multi-user authorization.

Runs a lightweight Flask server to handle the OAuth2 callback.
Users click an authorization link → Strava redirects back with a code →
we exchange it for tokens and store per-user.

Usage:
  Set env vars: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, OAUTH_REDIRECT_URI
  Run alongside the Discord bot (or as a separate process).
"""

from __future__ import annotations

import json
import os
import logging
import threading
from typing import Optional

import requests
from flask import Flask, request, redirect

logger = logging.getLogger(__name__)

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5050/callback")
TOKEN_STORE_PATH = os.getenv("STRAVA_TOKEN_STORE", "strava_tokens.json")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"

# In-memory store: { discord_user_id: { access_token, refresh_token, expires_at, athlete_id, athlete_name } }
_token_store: dict[str, dict] = {}

# Pending auth requests: { state_token: discord_user_id }
_pending_auth: dict[str, str] = {}

app = Flask(__name__)


def _load_store():
    global _token_store
    try:
        if os.path.exists(TOKEN_STORE_PATH):
            with open(TOKEN_STORE_PATH, "r") as f:
                _token_store = json.load(f)
            logger.info("Loaded %d user tokens from disk", len(_token_store))
    except Exception as e:
        logger.warning("Failed to load token store: %s", e)


def _save_store():
    try:
        with open(TOKEN_STORE_PATH, "w") as f:
            json.dump(_token_store, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save token store: %s", e)


def get_auth_url(discord_user_id: str) -> str:
    """Generate a Strava OAuth authorization URL for a Discord user."""
    import secrets
    state = secrets.token_urlsafe(16)
    _pending_auth[state] = discord_user_id

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "read,activity:read",
        "state": state,
        "approval_prompt": "auto",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{qs}"


@app.route("/callback")
def oauth_callback():
    """Handle Strava OAuth2 callback."""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return f"<h2>Authorization denied</h2><p>{error}</p>", 400

    if not code or not state:
        return "<h2>Missing code or state</h2>", 400

    discord_user_id = _pending_auth.pop(state, None)
    if not discord_user_id:
        return "<h2>Invalid or expired state token</h2><p>Please try !trainme again.</p>", 400

    # Exchange code for tokens
    try:
        resp = requests.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Token exchange failed: %s", e)
        return f"<h2>Token exchange failed</h2><p>{e}</p>", 500

    athlete = data.get("athlete", {})
    _token_store[discord_user_id] = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
        "athlete_id": athlete.get("id"),
        "athlete_name": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
    }
    _save_store()

    name = _token_store[discord_user_id]["athlete_name"] or "Athlete"
    return (
        f"<h2>✅ Connected!</h2>"
        f"<p>Welcome, {name}! Your Strava account is now linked.</p>"
        f"<p>Go back to Discord and use <code>!trainme</code> to get your training plan.</p>"
    )


@app.route("/health")
def health():
    return "ok"


def get_user_token(discord_user_id: str) -> Optional[dict]:
    """Get stored token for a Discord user, refreshing if expired."""
    _load_store()
    user_data = _token_store.get(discord_user_id)
    if not user_data:
        return None

    import time
    # Refresh if expired (5 min buffer)
    if time.time() > (user_data.get("expires_at", 0) - 300):
        try:
            resp = requests.post(TOKEN_URL, data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": user_data["refresh_token"],
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            user_data["access_token"] = data["access_token"]
            user_data["refresh_token"] = data.get("refresh_token", user_data["refresh_token"])
            user_data["expires_at"] = data["expires_at"]
            _token_store[discord_user_id] = user_data
            _save_store()
            logger.info("Refreshed token for user %s", discord_user_id)
        except Exception as e:
            logger.error("Failed to refresh token for %s: %s", discord_user_id, e)
            return None

    return user_data


def is_user_connected(discord_user_id: str) -> bool:
    _load_store()
    return discord_user_id in _token_store


def start_oauth_server(port: int = 5050):
    """Start the OAuth callback server in a background thread."""
    _load_store()
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info("OAuth server started on port %d", port)
