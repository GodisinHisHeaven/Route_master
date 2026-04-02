"""
Route Master Agent Layer — Kimi K2.5 via NVIDIA NIM
Handles natural language cycling route recommendations and community notes.
"""

import json
import os
import time
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List

import requests

from wind import get_wind_direction_at_hour
from repo import download_and_parse_xlsx

logger = logging.getLogger(__name__)

# ─── Kimi / NVIDIA NIM config ───────────────────────────────────────────────
# Read config lazily so dotenv has time to load in bot.py
def _get_nvidia_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "")

NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
KIMI_MODEL = os.getenv("KIMI_MODEL", "moonshotai/kimi-k2.5")
KIMI_MAX_TOKENS = int(os.getenv("KIMI_MAX_TOKENS", "1024"))
KIMI_TEMPERATURE = float(os.getenv("KIMI_TEMPERATURE", "0.7"))
KIMI_TIMEOUT = float(os.getenv("KIMI_TIMEOUT", "30"))

# ─── Route notes (community memory) ─────────────────────────────────────────
NOTES_PATH = os.getenv("ROUTE_NOTES_PATH", "route_notes.json")
# Rate limit: 1 note per user per route per day
_NOTE_COOLDOWN_SECONDS = 86400


def _load_notes() -> dict:
    try:
        if os.path.exists(NOTES_PATH):
            with open(NOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Failed to load route notes: %s", e)
    return {}


def _save_notes(notes: dict):
    try:
        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to save route notes: %s", e)


def add_route_note(route_name: str, author: str, note: str, rating: Optional[int] = None) -> str:
    """Add a community note to a route. Returns status message."""
    notes = _load_notes()
    route_key = route_name.strip()

    if route_key not in notes:
        notes[route_key] = []

    # Rate limit check
    today = datetime.now().strftime("%Y-%m-%d")
    for existing in notes[route_key]:
        if existing.get("author") == author and existing.get("date") == today:
            return f"You've already left a note for {route_key} today."

    entry = {
        "author": author,
        "note": note.strip(),
        "date": today,
    }
    if rating is not None:
        entry["rating"] = max(1, min(5, rating))

    notes[route_key].append(entry)

    # Keep last 20 notes per route
    notes[route_key] = notes[route_key][-20:]

    _save_notes(notes)
    return f"Note saved for {route_key}."


def get_route_notes(route_name: str, limit: int = 5) -> List[dict]:
    """Get recent notes for a route."""
    notes = _load_notes()
    return notes.get(route_name.strip(), [])[-limit:]


# ─── Route data formatting ──────────────────────────────────────────────────
def _format_routes_for_prompt() -> str:
    """Format route data for system prompt injection."""
    raw = download_and_parse_xlsx()
    if not raw:
        return "No route data available."
    try:
        routes = json.loads(raw)
    except json.JSONDecodeError:
        return "No route data available."

    lines = []
    for r in routes:
        name = r.get("Route name", "Unknown")
        dist = r.get("Distance (mi)", "?")
        wind = r.get("Ideal Wind Direction", "?")
        link = r.get("Ride with GPS link", "")
        author = r.get("Author", "")
        notes_text = r.get("Notes") or ""

        # Append community notes
        community = get_route_notes(name, limit=3)
        community_str = ""
        if community:
            snippets = [f"  - {n['author']} ({n['date']}): {n['note']}" for n in community]
            community_str = "\n  Community notes:\n" + "\n".join(snippets)

        lines.append(
            f"- **{name}** | {dist} mi | Wind: {wind} | Author: {author}\n"
            f"  Link: {link}\n"
            f"  Notes: {notes_text}{community_str}"
        )

    return "\n".join(lines)


def _build_system_prompt() -> str:
    """Build the system prompt with route data and current wind."""
    routes_block = _format_routes_for_prompt()

    return f"""You are Route Master, a friendly and knowledgeable cycling route advisor for the Champaign-Urbana area in Illinois.

## Your Capabilities
- Recommend cycling routes based on wind conditions, distance preferences, and rider needs
- Share community notes and feedback about routes
- Answer general cycling questions about the local area
- Record rider feedback about routes when they share it

## Route Database
You have access to {len(json.loads(download_and_parse_xlsx() or '[]'))} routes:

{routes_block}

## How Route Recommendations Work
- Wind direction matters: each route has an "Ideal Wind Direction" — this means the wind will be at the rider's BACK for most of the ride
- When recommending, always check the current/forecasted wind and match it to routes
- Consider distance preferences (suggest closest match, offer alternatives)
- Mention community notes if relevant (especially recent ones about road conditions)
- Always include the Ride with GPS link

## Handling Feedback
When a user shares feedback about a route (e.g., "Stone Creek was muddy today", "Great ride on Monticello route"), respond with:
1. Acknowledge the feedback
2. Respond with a JSON block on a NEW line that starts with `>>>SAVE_NOTE:` followed by the JSON, like:
>>>SAVE_NOTE:{{"route":"Stone Creek","note":"Muddy after rain","rating":3}}

Rating scale: 1=terrible, 2=poor, 3=okay, 4=good, 5=excellent. Infer from context.

## Wind Queries
When the user asks about wind or you need wind data for a recommendation, respond with a JSON block:
>>>WIND_QUERY:{{"hour":14}}

I will inject the wind result and you can continue your recommendation.

## Rules
- Always respond in English
- Be concise but helpful — no walls of text
- If you can't determine what the user wants, ask a short clarifying question
- When suggesting routes, suggest 1-2 best matches, not a full list
- Include the GPS link for recommended routes
- If a user asks something unrelated to cycling, politely redirect
"""


# ─── Kimi API call ──────────────────────────────────────────────────────────
def _call_kimi(messages: List[dict]) -> Optional[str]:
    """Call Kimi K2.5 via NVIDIA NIM. Returns response text or None on failure."""
    api_key = _get_nvidia_key()
    if not api_key:
        logger.error("NVIDIA_API_KEY not set")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": KIMI_MODEL,
        "messages": messages,
        "max_tokens": KIMI_MAX_TOKENS,
        "temperature": KIMI_TEMPERATURE,
    }

    try:
        resp = requests.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=KIMI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # Kimi may return content in "content" or use reasoning with tool_calls
        content = msg.get("content")
        if content:
            return content
        # If content is null, check for reasoning_content
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if reasoning:
            return reasoning
        # Last resort: return a generic fallback
        logger.warning("Kimi returned empty content: %s", json.dumps(msg)[:500])
        return None
    except requests.exceptions.Timeout:
        logger.error("Kimi API timeout after %ss", KIMI_TIMEOUT)
        return None
    except requests.exceptions.RequestException as e:
        logger.error("Kimi API error: %s", e)
        return None
    except (KeyError, IndexError) as e:
        logger.error("Unexpected Kimi response structure: %s", e)
        return None


# ─── Conversation manager ───────────────────────────────────────────────────
# Per-channel conversation history (kept short to stay within budget)
_conversations: Dict[str, List[dict]] = {}
_MAX_HISTORY = 10  # messages per channel


def _get_history(channel_id: str) -> List[dict]:
    if channel_id not in _conversations:
        _conversations[channel_id] = []
    return _conversations[channel_id]


def _trim_history(channel_id: str):
    hist = _conversations.get(channel_id, [])
    if len(hist) > _MAX_HISTORY:
        _conversations[channel_id] = hist[-_MAX_HISTORY:]


# ─── Main agent entry point ─────────────────────────────────────────────────
def handle_message(user_message: str, author_name: str, channel_id: str) -> Optional[str]:
    """
    Process a natural language message. Returns response text or None if Kimi is unavailable.
    Handles tool calls (wind queries, note saving) internally.
    """
    history = _get_history(channel_id)

    # Build messages
    system_prompt = _build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": f"[{author_name}]: {user_message}"})

    # First Kimi call
    response = _call_kimi(messages)
    if response is None:
        return None  # Signal to caller: fall back to slash commands

    # Process tool calls embedded in response
    response = _process_tool_calls(response, author_name, messages)

    # Update history
    history.append({"role": "user", "content": f"[{author_name}]: {user_message}"})
    history.append({"role": "assistant", "content": response})
    _trim_history(channel_id)

    # Clean out any remaining tool call markers from final response
    response = _clean_response(response)

    return response


def _process_tool_calls(response: str, author_name: str, messages: list[dict]) -> str:
    """Handle >>>WIND_QUERY and >>>SAVE_NOTE markers in Kimi's response."""

    # Handle wind queries
    wind_match = re.search(r">>>WIND_QUERY:\s*(\{.*?\})", response, re.DOTALL)
    if wind_match:
        try:
            wind_req = json.loads(wind_match.group(1))
            hour = int(wind_req.get("hour", 14))
            result = get_wind_direction_at_hour(hour)
            if result and result[0]:
                direction, forecast_time = result
                wind_info = f"Wind at {forecast_time}: {direction}"
            else:
                wind_info = "Could not retrieve wind data."

            # Re-call Kimi with wind info injected
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"[SYSTEM] Wind data: {wind_info}"})
            second_response = _call_kimi(messages)
            if second_response:
                response = second_response
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse wind query: %s", e)

    # Handle note saving
    note_match = re.search(r">>>SAVE_NOTE:\s*(\{.*?\})", response, re.DOTALL)
    if note_match:
        try:
            note_data = json.loads(note_match.group(1))
            route = note_data.get("route", "")
            note = note_data.get("note", "")
            rating = note_data.get("rating")
            if route and note:
                add_route_note(route, author_name, note, rating)
                logger.info("Saved note for route '%s' by %s", route, author_name)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse note save: %s", e)

    return response


def _clean_response(text: str) -> str:
    """Remove tool call markers from the final user-facing response."""
    text = re.sub(r">>>WIND_QUERY:\s*\{.*?\}", "", text, flags=re.DOTALL)
    text = re.sub(r">>>SAVE_NOTE:\s*\{.*?\}", "", text, flags=re.DOTALL)
    return text.strip()


# ─── Health check ────────────────────────────────────────────────────────────
def is_available() -> bool:
    """Quick check if Kimi API is reachable."""
    api_key = _get_nvidia_key()
    if not api_key:
        return False
    try:
        resp = requests.get(
            f"{NVIDIA_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False
