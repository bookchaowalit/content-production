"""POST /projects/{id}/edl/silence-cut saves the cut as the next EDL version (spec V2, AC-V2d)."""

from __future__ import annotations

import pytest

from video_postproduction.api.dependencies import get_session
from video_postproduction.main import app
from video_postproduction.models import EDL, MediaAsset, Project, Transcript

from .conftest import valid_initial_edl


@pytest.fixture
def silence_api(session):
    project = Project(name="Silence demo", status="ready_for_edit")
    session.add(project)
    session.flush()
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/silence/source.mp4",
        original_filename="source.mp4",
        media_type="video/mp4",
        size_bytes=10,
        sha256="e" * 64,
        metadata_json={"duration": 30.0},
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    document = valid_initial_edl(project.id)
    document["source_assets"][0]["asset_id"] = str(asset.id)
    document["source_assets"][0]["duration"] = 30.0
    document["timeline"][0]["source"]["asset_id"] = str(asset.id)
    session.add(
        EDL(
            project_id=project.id,
            source_asset_id=asset.id,
            version=1,
            schema_version="edl.v1",
            document=document,
            status="draft",
        )
    )
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield project, asset, session
    finally:
        app.dependency_overrides.clear()


def _add_transcript(session, project, asset) -> None:
    segments = [
        {"id": "s1", "start": 0.5, "end": 3.0, "text": "hello", "words": []},
        {"id": "s2", "start": 6.0, "end": 8.0, "text": "again", "words": []},
    ]
    session.add(
        Transcript(
            project_id=project.id,
            asset_id=asset.id,
            provider="deterministic",
            model="test",
            language="en",
            duration=30.0,
            normalized_json={
                "schema_version": "transcript.v1",
                "provider": "deterministic",
                "model": "test",
                "language": "en",
                "duration": 30.0,
                "segments": segments,
            },
            content_fingerprint="0" * 64,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_silence_cut_route_saves_next_edl_version(client, silence_api):
    project, asset, session = silence_api
    url = f"/api/v1/projects/{project.id}/edl/silence-cut"
    refused = await client.post(url, json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "transcript_not_ready"
    _add_transcript(session, project, asset)
    response = await client.post(
        url, json={"min_gap_s": 0.6, "padding_s": 0.2, "remove_fillers": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2
    ranges = [
        (item["source"]["start"], item["source"]["end"]) for item in body["document"]["timeline"]
    ]
    assert ranges == [(0.3, 3.2), (5.8, 8.2)]
    assert body["document"]["timeline"][0]["provenance"]["reason"] == "silence_cut"
    bad = await client.post(url, json={"min_gap_s": 0})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_silence_cut_route_reports_no_speech(client, silence_api):
    project, asset, session = silence_api
    session.add(
        Transcript(
            project_id=project.id,
            asset_id=asset.id,
            provider="deterministic",
            model="test",
            language="en",
            duration=30.0,
            normalized_json={
                "schema_version": "transcript.v1",
                "provider": "deterministic",
                "model": "test",
                "language": "en",
                "duration": 30.0,
                "segments": [{"id": "s1", "start": 20.0, "end": 22.0, "text": "late", "words": []}],
            },
            content_fingerprint="0" * 64,
        )
    )
    session.commit()
    response = await client.post(f"/api/v1/projects/{project.id}/edl/silence-cut", json={})
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "silence_cut_no_speech"
    )
