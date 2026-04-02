"""Strava API integration for Route Master.

Handles OAuth token refresh and fetching athlete activities.
"""

from __future__ import annotations

import os
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Strava OAuth endpoints
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"

# Credentials from env
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")

# Token state — loaded from disk if available
_TOKEN_FILE = os.getenv("STRAVA_TOKEN_FILE", "strava_token.json")
_token_state: dict = {}


def _load_token():
    global _token_state
    if _token_state:
        return
    try:
        if os.path.exists(_TOKEN_FILE):
            with open(_TOKEN_FILE, "r") as f:
                _token_state = json.load(f)
    except Exception:
        _token_state = {}


def _save_token():
    try:
        with open(_TOKEN_FILE, "w") as f:
            json.dump(_token_state, f)
    except Exception as e:
        logger.warning("Failed to save token: %s", e)


def _refresh_if_needed() -> str:
    """Return a valid access token, refreshing if expired."""
    _load_token()

    access_token = _token_state.get("access_token", os.getenv("STRAVA_ACCESS_TOKEN", ""))
    expires_at = _token_state.get("expires_at", 0)
    refresh_token = _token_state.get("refresh_token", os.getenv("STRAVA_REFRESH_TOKEN", ""))

    # If token is still valid (with 5 min buffer), return it
    if access_token and time.time() < (expires_at - 300):
        return access_token

    if not refresh_token:
        raise RuntimeError("No refresh token available. Set STRAVA_REFRESH_TOKEN.")

    logger.info("Refreshing Strava access token...")
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    _token_state["access_token"] = data["access_token"]
    _token_state["refresh_token"] = data.get("refresh_token", refresh_token)
    _token_state["expires_at"] = data["expires_at"]
    _save_token()

    logger.info("Token refreshed, expires at %s", datetime.fromtimestamp(data["expires_at"]))
    return data["access_token"]


def get_athlete_activities(days: int = 30, per_page: int = 50) -> list[dict]:
    """Fetch the authenticated athlete's activities for the past N days."""
    token = _refresh_if_needed()
    after = int((datetime.now() - timedelta(days=days)).timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    activities = []
    page = 1

    while True:
        resp = requests.get(
            f"{API_BASE}/athlete/activities",
            headers=headers,
            params={"after": after, "per_page": per_page, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    logger.info("Fetched %d activities from the past %d days", len(activities), days)
    return activities


def get_athlete_profile_by_id(athlete_id: int) -> Optional[dict]:
    """Fetch a public athlete profile. Note: limited info for non-authenticated athletes."""
    token = _refresh_if_needed()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{API_BASE}/athletes/{athlete_id}", headers=headers, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    logger.warning("Failed to fetch athlete %d: %s", athlete_id, resp.status_code)
    return None


def get_athlete_activities_by_token(access_token: str, days: int = 30, per_page: int = 50) -> list[dict]:
    """Fetch activities using a specific user's access token."""
    after = int((datetime.now() - timedelta(days=days)).timestamp())
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    page = 1

    while True:
        resp = requests.get(
            f"{API_BASE}/athlete/activities",
            headers=headers,
            params={"after": after, "per_page": per_page, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    return activities


def summarize_activities(activities: list[dict]) -> dict:
    """Produce a training summary from raw activities for LLM consumption."""
    summary = {
        "total_activities": len(activities),
        "by_type": {},
        "weekly_breakdown": {},
        "recent_activities": [],
    }

    for act in activities:
        sport = act.get("sport_type") or act.get("type", "Unknown")
        dist_mi = round(act.get("distance", 0) / 1609.34, 1)
        duration_min = round(act.get("moving_time", 0) / 60, 1)
        elev_ft = round(act.get("total_elevation_gain", 0) * 3.28084, 0)
        date_str = act.get("start_date_local", "")[:10]
        suffer = act.get("suffer_score")
        avg_hr = act.get("average_heartrate")
        avg_speed_mph = round(act.get("average_speed", 0) * 2.23694, 1) if act.get("average_speed") else None

        # By type
        if sport not in summary["by_type"]:
            summary["by_type"][sport] = {"count": 0, "total_miles": 0, "total_minutes": 0}
        summary["by_type"][sport]["count"] += 1
        summary["by_type"][sport]["total_miles"] = round(summary["by_type"][sport]["total_miles"] + dist_mi, 1)
        summary["by_type"][sport]["total_minutes"] = round(summary["by_type"][sport]["total_minutes"] + duration_min, 1)

        # Weekly breakdown
        try:
            dt = datetime.fromisoformat(date_str)
            week_start = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        except Exception:
            week_start = "unknown"
        if week_start not in summary["weekly_breakdown"]:
            summary["weekly_breakdown"][week_start] = {"miles": 0, "minutes": 0, "count": 0}
        summary["weekly_breakdown"][week_start]["miles"] = round(
            summary["weekly_breakdown"][week_start]["miles"] + dist_mi, 1
        )
        summary["weekly_breakdown"][week_start]["minutes"] = round(
            summary["weekly_breakdown"][week_start]["minutes"] + duration_min, 1
        )
        summary["weekly_breakdown"][week_start]["count"] += 1

        # Keep last 10 activities with detail
        if len(summary["recent_activities"]) < 10:
            entry = {
                "date": date_str,
                "type": sport,
                "distance_mi": dist_mi,
                "duration_min": duration_min,
                "elevation_ft": elev_ft,
                "name": act.get("name", ""),
            }
            if avg_hr:
                entry["avg_hr"] = avg_hr
            if avg_speed_mph:
                entry["avg_speed_mph"] = avg_speed_mph
            if suffer:
                entry["suffer_score"] = suffer
            summary["recent_activities"].append(entry)

    return summary


def parse_athlete_id_from_url(url: str) -> Optional[int]:
    """Extract athlete ID from a Strava profile URL like https://www.strava.com/athletes/12345."""
    import re
    m = re.search(r"strava\.com/athletes/(\d+)", url)
    if m:
        return int(m.group(1))
    return None
