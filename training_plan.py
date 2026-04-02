"""Training plan generator using Kimi K2.5 (via NVIDIA NIM) based on Strava activity data.

Uses OpenAI-compatible API format with NVIDIA's endpoint.
"""

from __future__ import annotations

import os
import json
import logging

import requests

logger = logging.getLogger(__name__)

# Kimi K2.5 via NVIDIA NIM
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://integrate.api.nvidia.com/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "moonshotai/kimi-k2.5")

SYSTEM_PROMPT = """You are an expert cycling and endurance sports coach.
Given an athlete's recent training data (past ~30 days from Strava), generate a personalized
7-day training plan for the upcoming week.

You MUST respond with ONLY a valid JSON object, no markdown, no code fences, no extra text.
The JSON schema:
{
  "summary": "1-sentence overall rationale",
  "total_hours": 7.5,
  "days": [
    {
      "day": "Monday",
      "emoji": "🚴",
      "activity": "Cycling",
      "description": "60 min Zone 2, flat terrain, 18 mi",
      "duration_min": 60
    }
  ]
}

Rules:
- Exactly 7 days (Mon-Sun)
- emoji: use sport emoji (🚴🏻🏃🏻🏊🏻💪😴 etc.)
- description: 1 line, include distance/duration/intensity
- For rest days: activity="Rest", emoji="😴", description="Recovery", duration_min=0
- Use the training_load data (estimated TSS, weekly TSS trends) to calibrate intensity:
  * If avg daily TSS is high (>60), include more recovery
  * If weekly TSS is trending up, consider a deload week
  * Target a reasonable weekly TSS based on their recent load
  * Include estimated_tss for each day in the JSON
- If the user provides specific goals, prioritize those.
- Respond in the same language as the user's request for description/summary fields."""


def generate_training_plan(
    activity_summary: dict,
    user_request: str = "",
    athlete_name: str = "Athlete",
    target_hours: float = None,
) -> str:
    """Generate a 7-day training plan using Kimi K2.5 based on Strava activity summary."""

    api_key = KIMI_API_KEY or os.getenv("KIMI_API_KEY", "")
    if not api_key:
        return "⚠️ Training plan generation unavailable (Kimi API key not configured)."

    user_msg_parts = [
        f"Athlete: {athlete_name}",
        f"\n## Training Data (Past 30 Days)\n```json\n{json.dumps(activity_summary, indent=2)}\n```",
    ]
    if target_hours:
        user_msg_parts.append(f"\n## Time Constraint\nTotal training time for next week: {target_hours} hours. Distribute across the 7 days accordingly. The total_hours in your JSON output MUST equal {target_hours}.")
    if user_request:
        user_msg_parts.append(f"\n## Athlete's Goals/Requests\n{user_request}")
    user_msg_parts.append("\nPlease generate a 7-day training plan for next week.")

    user_msg = "\n".join(user_msg_parts)

    payload = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 16384,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{KIMI_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"].get("content")
            # Kimi K2.5 may return content=null with reasoning in the message;
            # fall back to reasoning text if content is empty.
            if not content:
                reasoning = data["choices"][0]["message"].get("reasoning", "")
                content = reasoning or "(No plan generated — model returned empty response)"
            return content
        except Exception as e:
            last_err = e
            logger.warning("Training plan attempt %d failed: %s", attempt + 1, e)
            import time
            time.sleep(2)

    logger.error("All attempts failed for training plan: %s", last_err)
    return f"⚠️ Error generating training plan: {last_err}"
