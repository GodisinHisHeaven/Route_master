"""Tests for strava.py and training_plan.py"""

import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from strava import (
    summarize_activities,
    parse_athlete_id_from_url,
    get_athlete_activities,
    _refresh_if_needed,
    _token_state,
)
from training_plan import generate_training_plan


# ── Fixtures ──────────────────────────────────────────────


def _make_activity(
    sport="Ride",
    distance_m=32000,
    moving_time_s=3600,
    elevation_m=150,
    date_offset_days=0,
    avg_hr=145,
    avg_speed=8.9,
    name="Morning Ride",
    suffer=72,
):
    dt = datetime.now() - timedelta(days=date_offset_days)
    return {
        "sport_type": sport,
        "type": sport,
        "distance": distance_m,
        "moving_time": moving_time_s,
        "total_elevation_gain": elevation_m,
        "start_date_local": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "average_heartrate": avg_hr,
        "average_speed": avg_speed,
        "name": name,
        "suffer_score": suffer,
    }


SAMPLE_ACTIVITIES = [
    _make_activity("Ride", 48000, 5400, 300, 1, 152, 8.9, "Long Ride", 95),
    _make_activity("Ride", 32000, 3600, 150, 3, 145, 8.9, "Easy Spin", 55),
    _make_activity("Run", 8000, 2400, 30, 4, 160, 3.3, "Recovery Run", 40),
    _make_activity("Ride", 64000, 7200, 500, 7, 158, 8.9, "Weekend Century Prep", 120),
    _make_activity("Ride", 24000, 2700, 100, 8, 140, 8.9, "Coffee Ride", 35),
    _make_activity("Run", 10000, 3000, 50, 10, 155, 3.3, "Tempo Run", 65),
    _make_activity("Ride", 40000, 4500, 200, 14, 148, 8.9, "Group Ride", 80),
    _make_activity("Ride", 32000, 3600, 150, 17, 142, 8.9, "Solo Ride", 50),
    _make_activity("Ride", 56000, 6300, 400, 21, 155, 8.9, "Hilly Route", 110),
    _make_activity("Run", 5000, 1500, 10, 25, 135, 3.3, "Easy Jog", 20),
    _make_activity("Ride", 30000, 3400, 120, 28, 143, 8.8, "Commute", 40),
]


# ── Unit Tests: summarize_activities ──────────────────────


class TestSummarizeActivities:
    def test_empty(self):
        result = summarize_activities([])
        assert result["total_activities"] == 0
        assert result["by_type"] == {}
        assert result["recent_activities"] == []

    def test_counts(self):
        result = summarize_activities(SAMPLE_ACTIVITIES)
        assert result["total_activities"] == 11
        assert "Ride" in result["by_type"]
        assert "Run" in result["by_type"]
        assert result["by_type"]["Ride"]["count"] == 8
        assert result["by_type"]["Run"]["count"] == 3

    def test_distance_conversion(self):
        """Distances should be in miles (converted from meters)."""
        acts = [_make_activity("Ride", 1609.34, 600, 0, 0)]  # exactly 1 mile
        result = summarize_activities(acts)
        assert result["by_type"]["Ride"]["total_miles"] == 1.0

    def test_recent_activities_capped_at_10(self):
        result = summarize_activities(SAMPLE_ACTIVITIES)
        assert len(result["recent_activities"]) == 10

    def test_weekly_breakdown(self):
        result = summarize_activities(SAMPLE_ACTIVITIES)
        assert len(result["weekly_breakdown"]) > 0
        for week, data in result["weekly_breakdown"].items():
            assert "miles" in data
            assert "minutes" in data
            assert "count" in data

    def test_activity_fields(self):
        result = summarize_activities(SAMPLE_ACTIVITIES)
        act = result["recent_activities"][0]
        assert "date" in act
        assert "type" in act
        assert "distance_mi" in act
        assert "duration_min" in act
        assert "elevation_ft" in act
        assert "avg_hr" in act

    def test_no_heartrate(self):
        acts = [_make_activity(avg_hr=None)]
        result = summarize_activities(acts)
        assert "avg_hr" not in result["recent_activities"][0]


# ── Unit Tests: parse_athlete_id_from_url ─────────────────


class TestParseAthleteId:
    def test_valid_url(self):
        assert parse_athlete_id_from_url("https://www.strava.com/athletes/12345") == 12345

    def test_with_trailing_slash(self):
        assert parse_athlete_id_from_url("https://www.strava.com/athletes/99999/") == 99999

    def test_invalid_url(self):
        assert parse_athlete_id_from_url("https://www.google.com/foo") is None

    def test_no_number(self):
        assert parse_athlete_id_from_url("https://www.strava.com/athletes/abc") is None

    def test_embedded_in_text(self):
        assert parse_athlete_id_from_url("check out https://www.strava.com/athletes/42 cool") == 42


# ── Unit Tests: Token refresh ─────────────────────────────


class TestTokenRefresh:
    @patch("strava.os.path.exists", return_value=False)
    def test_uses_env_when_valid(self, _mock_exists):
        """Should use env token when it hasn't expired."""
        import strava
        strava._token_state = {
            "access_token": "test_token_123",
            "expires_at": time.time() + 3600,
            "refresh_token": "refresh_xyz",
        }
        token = _refresh_if_needed()
        assert token == "test_token_123"

    @patch("strava.requests.post")
    @patch("strava.os.path.exists", return_value=False)
    def test_refreshes_when_expired(self, _mock_exists, mock_post):
        """Should call Strava token endpoint when token is expired."""
        import strava
        strava._token_state = {
            "access_token": "old_token",
            "expires_at": time.time() - 100,
            "refresh_token": "refresh_xyz",
        }
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "new_token_456",
                "refresh_token": "new_refresh",
                "expires_at": int(time.time()) + 3600,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()

        token = _refresh_if_needed()
        assert token == "new_token_456"
        mock_post.assert_called_once()


# ── Unit Tests: get_athlete_activities ────────────────────


class TestGetActivities:
    @patch("strava._refresh_if_needed", return_value="fake_token")
    @patch("strava.requests.get")
    def test_single_page(self, mock_get, _mock_token):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_ACTIVITIES[:3]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_athlete_activities(days=30, per_page=50)
        assert len(result) == 3
        mock_get.assert_called_once()

    @patch("strava._refresh_if_needed", return_value="fake_token")
    @patch("strava.requests.get")
    def test_pagination(self, mock_get, _mock_token):
        page1 = [_make_activity(name=f"Act {i}") for i in range(50)]
        page2 = [_make_activity(name=f"Act {i}") for i in range(50, 53)]
        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status = MagicMock()
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = page2
        mock_resp2.raise_for_status = MagicMock()
        mock_get.side_effect = [mock_resp1, mock_resp2]

        result = get_athlete_activities(days=30, per_page=50)
        assert len(result) == 53


# ── Unit Tests: generate_training_plan ────────────────────


class TestGenerateTrainingPlan:
    def test_no_api_key(self):
        import training_plan
        original = training_plan.KIMI_API_KEY
        training_plan.KIMI_API_KEY = ""
        result = generate_training_plan({})
        training_plan.KIMI_API_KEY = original
        assert "unavailable" in result.lower()

    @patch("training_plan.requests.post")
    def test_successful_generation(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-key"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "## Monday\nRest day\n## Tuesday\n30mi ride Z2"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        summary = summarize_activities(SAMPLE_ACTIVITIES)
        result = generate_training_plan(summary, "Preparing for a century ride")
        assert "Monday" in result
        assert "Tuesday" in result

    @patch("training_plan.requests.post")
    def test_api_error_handled(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-key"

        mock_post.side_effect = Exception("Connection timeout")
        result = generate_training_plan({})
        assert "Error" in result


# ── Integration-style test (uses real summary pipeline) ───


class TestEndToEnd:
    def test_summary_to_plan_pipeline(self):
        """Verify the data flows correctly from activities → summary → plan input."""
        summary = summarize_activities(SAMPLE_ACTIVITIES)

        # Summary should be JSON-serializable (needed for LLM prompt)
        json_str = json.dumps(summary)
        assert len(json_str) > 100

        # Should have meaningful data
        assert summary["total_activities"] == 11
        assert summary["by_type"]["Ride"]["total_miles"] > 100
        assert len(summary["weekly_breakdown"]) >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
