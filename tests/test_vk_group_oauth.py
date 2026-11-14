from urllib.parse import parse_qs, urlparse

from app.web.vk_group_oauth import (
    build_authorize_url,
    extract_group_token,
    make_state,
    read_state,
)


def test_oauth_state_is_signed_and_short_lived():
    state = make_state(
        user_id="f4c9d531-9c16-4f6d-a4c6-0b85fd973915",
        group_id="69207505",
        title="Aerovir",
        secret="test-secret",
        now=1000,
    )

    assert read_state(state, secret="test-secret", now=1599) == {
        "user_id": "f4c9d531-9c16-4f6d-a4c6-0b85fd973915",
        "group_id": "69207505",
        "title": "Aerovir",
    }
    assert read_state(state, secret="wrong-secret", now=1000) is None
    assert read_state(state, secret="test-secret", now=1601) is None


def test_oauth_authorize_url_requests_only_the_selected_community():
    url = build_authorize_url(
        app_id=12345,
        redirect_uri="https://tickets.example/api/me/vk-groups/oauth/callback",
        group_id="69207505",
        state="signed-state",
    )
    params = parse_qs(urlparse(url).query)

    assert url.startswith("https://oauth.vk.com/authorize?")
    assert params["client_id"] == ["12345"]
    assert params["response_type"] == ["code"]
    assert params["group_ids"] == ["69207505"]
    assert params["redirect_uri"] == ["https://tickets.example/api/me/vk-groups/oauth/callback"]
    assert "wall" in params["scope"][0].split(",")


def test_oauth_extracts_only_requested_group_token():
    payload = {"access_tokens": {"12": "wrong-group-key", "69207505": "target-key"}}
    assert extract_group_token(payload, "69207505") == "target-key"
    assert extract_group_token(payload, "999") is None


def test_oauth_extracts_list_response_by_group_id():
    payload = {"access_tokens": [{"group_id": 69207505, "access_token": "target-key"}]}
    assert extract_group_token(payload, "69207505") == "target-key"


def test_frontend_exposes_separate_oauth_path_without_removing_bridge_flow():
    from pathlib import Path

    app_js = Path("app/web/static/app.js").read_text(encoding="utf-8")
    assert "connectMyVKGroupOAuth" in app_js
    assert '"/api/me/vk-groups/oauth/start"' in app_js
    assert "getVKCommunityTokenMethod(window.vkBridge)" in app_js
