"""Write routes must fail closed: no token (or a weak one) means no writes.

Before this gate, an unset VIDEO_EDITOR_API_TOKEN let anyone who could reach the API create
projects, upload media and queue transcription jobs. Local, loopback-only use now has to opt in
explicitly with VIDEO_EDITOR_ALLOW_UNAUTHENTICATED=true.
"""

from __future__ import annotations

import pytest

from video_postproduction.api.access import MIN_TOKEN_LENGTH, token_matches
from video_postproduction.api.dependencies import get_session, get_settings
from video_postproduction.config import Settings
from video_postproduction.main import app

STRONG_TOKEN = "s" * MIN_TOKEN_LENGTH


@pytest.fixture
def api_with(session):
    def configure(**overrides: object) -> None:
        base: dict[str, object] = {"video_editor_allow_unauthenticated": False}
        base.update(overrides)
        settings = Settings(**base)  # type: ignore[arg-type]
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: settings

    try:
        yield configure
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unset_token_refuses_writes(client, api_with):
    api_with(video_editor_api_token=None)

    response = await client.post("/api/v1/projects", json={"name": "Demo"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


@pytest.mark.asyncio
async def test_short_token_refuses_writes_even_with_the_right_header(client, api_with):
    api_with(video_editor_api_token="short")

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Demo"},
        headers={"Authorization": "Bearer short"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


@pytest.mark.asyncio
async def test_strong_token_requires_a_matching_bearer(client, api_with):
    api_with(video_editor_api_token=STRONG_TOKEN)

    missing = await client.post("/api/v1/projects", json={"name": "Demo"})
    wrong = await client.post(
        "/api/v1/projects",
        json={"name": "Demo"},
        headers={"Authorization": "Bearer " + "x" * MIN_TOKEN_LENGTH},
    )
    right = await client.post(
        "/api/v1/projects",
        json={"name": "Demo"},
        headers={"Authorization": f"Bearer {STRONG_TOKEN}"},
    )

    assert (missing.status_code, wrong.status_code, right.status_code) == (401, 401, 201)


@pytest.mark.asyncio
async def test_explicit_local_opt_in_allows_unauthenticated_writes(client, api_with):
    api_with(video_editor_api_token=None, video_editor_allow_unauthenticated=True)

    response = await client.post("/api/v1/projects", json={"name": "Local demo"})

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_reads_stay_open_without_a_token(client, api_with):
    api_with(video_editor_api_token=None)

    response = await client.get("/api/v1/projects")

    assert response.status_code == 200


def test_token_matches_is_exact():
    assert token_matches(STRONG_TOKEN, STRONG_TOKEN)
    assert not token_matches(STRONG_TOKEN, STRONG_TOKEN + "x")
    assert not token_matches(STRONG_TOKEN, "")
    assert not token_matches(STRONG_TOKEN, None)


def test_allow_unauthenticated_reads_the_environment(monkeypatch):
    monkeypatch.setenv("VIDEO_EDITOR_ALLOW_UNAUTHENTICATED", "true")
    assert Settings().video_editor_allow_unauthenticated is True
    monkeypatch.setenv("VIDEO_EDITOR_ALLOW_UNAUTHENTICATED", "false")
    assert Settings().video_editor_allow_unauthenticated is False
