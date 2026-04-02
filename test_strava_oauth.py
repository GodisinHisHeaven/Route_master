"""Tests for strava_oauth.py and training_plan.py (Kimi)."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch, MagicMock

import pytest

import strava_oauth
from strava_oauth import (
    get_auth_url,
    get_user_token,
    is_user_connected,
    _token_store,
    _pending_auth,
    _save_store,
)
from training_plan import generate_training_plan


# ── Fixtures ──────────────────────────────────────────────

USER_A = "111222333"
USER_B = "444555666"

MOCK_TOKEN = {
    "access_token": "act_token_123",
    "refresh_token": "ref_token_xyz",
    "expires_at": int(time.time()) + 7200,
    "athlete_id": 12345,
    "athlete_name": "Alice Cyclist",
}

MOCK_TOKEN_EXPIRED = {
    "access_token": "old_token",
    "refresh_token": "ref_token_old",
    "expires_at": int(time.time()) - 100,
    "athlete_id": 99999,
    "athlete_name": "Bob Runner",
}


def _clear_store():
    strava_oauth._token_store.clear()
    strava_oauth._pending_auth.clear()


# ── Tests: get_auth_url ───────────────────────────────────


class TestGetAuthUrl:
    def setup_method(self):
        _clear_store()

    def test_returns_strava_url(self):
        url = get_auth_url(USER_A)
        assert "strava.com/oauth/authorize" in url

    def test_contains_client_id(self):
        with patch.object(strava_oauth, "CLIENT_ID", "167899"):
            url = get_auth_url(USER_A)
            assert "client_id=167899" in url

    def test_contains_activity_scope(self):
        url = get_auth_url(USER_A)
        assert "activity%3Aread" in url or "activity:read" in url

    def test_registers_pending_state(self):
        url = get_auth_url(USER_A)
        # state should be stored in _pending_auth
        assert USER_A in strava_oauth._pending_auth.values()

    def test_different_users_get_different_states(self):
        url_a = get_auth_url(USER_A)
        url_b = get_auth_url(USER_B)
        states = list(strava_oauth._pending_auth.keys())
        assert len(set(states)) == len(states)  # all unique


# ── Tests: is_user_connected ──────────────────────────────


@patch("strava_oauth._load_store")  # prevent disk reads overwriting in-memory state
class TestIsUserConnected:
    def setup_method(self):
        _clear_store()

    def test_not_connected_by_default(self, _):
        assert not is_user_connected(USER_A)

    def test_connected_after_store(self, _):
        strava_oauth._token_store[USER_A] = MOCK_TOKEN.copy()
        assert is_user_connected(USER_A)

    def test_other_user_not_affected(self, _):
        strava_oauth._token_store[USER_A] = MOCK_TOKEN.copy()
        assert not is_user_connected(USER_B)


# ── Tests: get_user_token ─────────────────────────────────


@patch("strava_oauth._load_store")  # prevent disk reads overwriting in-memory state
@patch("strava_oauth._save_store")  # prevent disk writes during tests
class TestGetUserToken:
    def setup_method(self):
        _clear_store()

    def test_returns_none_for_unknown_user(self, _save, _load):
        assert get_user_token(USER_A) is None

    def test_returns_valid_token(self, _save, _load):
        strava_oauth._token_store[USER_A] = MOCK_TOKEN.copy()
        result = get_user_token(USER_A)
        assert result is not None
        assert result["access_token"] == "act_token_123"

    @patch("strava_oauth.requests.post")
    def test_refreshes_expired_token(self, mock_post, _save, _load):
        strava_oauth._token_store[USER_B] = MOCK_TOKEN_EXPIRED.copy()

        new_expires = int(time.time()) + 7200
        mock_post.return_value = MagicMock(
            json=lambda: {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_at": new_expires,
            }
        )
        mock_post.return_value.raise_for_status = MagicMock()

        result = get_user_token(USER_B)
        assert result is not None
        assert result["access_token"] == "new_access_token"
        mock_post.assert_called_once()

    @patch("strava_oauth.requests.post")
    def test_returns_none_on_refresh_failure(self, mock_post, _save, _load):
        user_c = "777888999"
        strava_oauth._token_store[user_c] = MOCK_TOKEN_EXPIRED.copy()
        mock_post.side_effect = Exception("Network error")

        result = get_user_token(user_c)
        assert result is None


# ── Tests: OAuth callback endpoint ────────────────────────


class TestOAuthCallback:
    def setup_method(self):
        _clear_store()
        strava_oauth.app.config["TESTING"] = True
        self.client = strava_oauth.app.test_client()

    def test_missing_code_returns_400(self):
        resp = self.client.get("/callback?state=abc")
        assert resp.status_code == 400

    def test_error_param_returns_400(self):
        resp = self.client.get("/callback?error=access_denied")
        assert resp.status_code == 400

    def test_invalid_state_returns_400(self):
        resp = self.client.get("/callback?code=abc&state=bad_state")
        assert resp.status_code == 400

    @patch("strava_oauth.requests.post")
    def test_valid_callback_stores_token(self, mock_post):
        # Setup a pending auth
        strava_oauth._pending_auth["valid_state"] = USER_A
        mock_post.return_value = MagicMock(
            json=lambda: {
                "access_token": "new_act",
                "refresh_token": "new_ref",
                "expires_at": int(time.time()) + 7200,
                "athlete": {"id": 12345, "firstname": "Alice", "lastname": "Cyclist"},
            }
        )
        mock_post.return_value.raise_for_status = MagicMock()

        resp = self.client.get("/callback?code=authcode123&state=valid_state")
        assert resp.status_code == 200
        assert USER_A in strava_oauth._token_store
        assert strava_oauth._token_store[USER_A]["access_token"] == "new_act"
        assert strava_oauth._token_store[USER_A]["athlete_name"] == "Alice Cyclist"

    @patch("strava_oauth.requests.post")
    def test_valid_callback_removes_pending_state(self, mock_post):
        strava_oauth._pending_auth["state_xyz"] = USER_B
        mock_post.return_value = MagicMock(
            json=lambda: {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_at": int(time.time()) + 7200,
                "athlete": {"id": 1, "firstname": "Bob", "lastname": ""},
            }
        )
        mock_post.return_value.raise_for_status = MagicMock()

        self.client.get("/callback?code=code&state=state_xyz")
        assert "state_xyz" not in strava_oauth._pending_auth

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    @patch("strava_oauth.requests.post")
    def test_token_exchange_failure_returns_500(self, mock_post):
        strava_oauth._pending_auth["state_fail"] = USER_A
        mock_post.side_effect = Exception("Strava is down")
        resp = self.client.get("/callback?code=code&state=state_fail")
        assert resp.status_code == 500


# ── Tests: training_plan with Kimi ────────────────────────


class TestTrainingPlanKimi:
    def test_no_api_key(self):
        import training_plan
        original = training_plan.KIMI_API_KEY
        training_plan.KIMI_API_KEY = ""
        result = generate_training_plan({})
        training_plan.KIMI_API_KEY = original
        assert "unavailable" in result.lower()

    @patch("training_plan.requests.post")
    def test_uses_nvidia_endpoint(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-kimi-key"
        mock_post.return_value = MagicMock(
            json=lambda: {"choices": [{"message": {"content": "## Mon\nRest\n## Tue\n50km ride"}}]}
        )
        mock_post.return_value.raise_for_status = MagicMock()

        generate_training_plan({"total_activities": 5}, "century prep", "Alice")

        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        assert "nvidia" in url or "integrate.api" in url

    @patch("training_plan.requests.post")
    def test_athlete_name_in_prompt(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-key"
        mock_post.return_value = MagicMock(
            json=lambda: {"choices": [{"message": {"content": "plan"}}]}
        )
        mock_post.return_value.raise_for_status = MagicMock()

        generate_training_plan({}, athlete_name="Speedy Gonzales")

        call_json = mock_post.call_args[1]["json"]
        messages = call_json["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Speedy Gonzales" in user_content

    @patch("training_plan.requests.post")
    def test_goals_included_in_prompt(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-key"
        mock_post.return_value = MagicMock(
            json=lambda: {"choices": [{"message": {"content": "plan"}}]}
        )
        mock_post.return_value.raise_for_status = MagicMock()

        generate_training_plan({}, user_request="I want to improve my FTP by 10%")

        call_json = mock_post.call_args[1]["json"]
        messages = call_json["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "FTP" in user_content

    @patch("training_plan.requests.post")
    def test_api_error_handled_gracefully(self, mock_post):
        import training_plan
        training_plan.KIMI_API_KEY = "test-key"
        mock_post.side_effect = Exception("Timeout")
        result = generate_training_plan({})
        assert "Error" in result or "error" in result.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
