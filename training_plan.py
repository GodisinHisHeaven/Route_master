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

Guidelines:
- Consider their training volume, intensity patterns, and sport types
- Include rest days as needed based on recent load
- Be specific: distance, duration, intensity zone, workout type
- If they do multiple sports (run, swim, etc.), incorporate cross-training
- Account for progressive overload but avoid injury risk
- Keep the plan practical and achievable
- Format the plan clearly day by day (Mon-Sun)
- Add brief rationale for the overall plan structure

If the user provides specific goals or requests, prioritize those.
Respond in the same language as the user's request (English or Chinese)."""


def generate_training_plan(
    activity_summary: dict,
    user_request: str = "",
    athlete_name: str = "Athlete",
) -> str:
    """Generate a 7-day training plan using Kimi K2.5 based on Strava activity summary."""

    api_key = KIMI_API_KEY or os.getenv("KIMI_API_KEY", "")
    if not api_key:
        return "⚠️ Training plan generation unavailable (Kimi API key not configured)."

    user_msg_parts = [
        f"Athlete: {athlete_name}",
        f"\n## Training Data (Past 30 Days)\n```json\n{json.dumps(activity_summary, indent=2)}\n```",
    ]
    if user_request:
        user_msg_parts.append(f"\n## Athlete's Goals/Requests\n{user_request}")
    user_msg_parts.append("\nPlease generate a 7-day training plan for next week.")

    user_msg = "\n".join(user_msg_parts)

    try:
        resp = requests.post(
            f"{KIMI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": KIMI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 2000,
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        plan = data["choices"][0]["message"]["content"]
        return plan
    except Exception as e:
        logger.error("Failed to generate training plan: %s", e)
        return f"⚠️ Error generating training plan: {e}"
