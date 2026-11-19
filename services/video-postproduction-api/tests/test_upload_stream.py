"""SPEC-001 R-INGEST: the upload body is parsed as a stream and the file part goes straight to STORAGE_ROOT/tmp."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import pytest

from video_postproduction.api.upload_stream import (
    BodyTooLarge,
    FileTooLarge,
    IncompleteBody,
    MissingFilePart,
    UploadStreamError,
    receive_file,
)

BOUNDARY = "----spike-boundary"


def multipart(*parts: tuple[str, str | None, str | None, bytes]) -> bytes:
    out = b""
    for name, filename, content_type, data in parts:
        out += f"--{BOUNDARY}\r\n".encode()
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        out += f"Content-Disposition: {disposition}\r\n".encode()
        if content_type is not None:
            out += f"Content-Type: {content_type}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    return out + f"--{BOUNDARY}--\r\n".encode()


async def chunks(body: bytes, size: int = 7) -> AsyncIterator[bytes]:
    for start in range(0, len(body), size):
        yield body[start : start + size]


HEADER = f"multipart/form-data; boundary={BOUNDARY}"


@pytest.mark.asyncio
async def test_file_part_is_streamed_into_the_tmp_dir_with_size_and_hash(tmp_path) -> None:
    data = b"video-bytes-" * 1000
    body = multipart(("note", None, None, b"ignored"), ("file", "clip.mp4", "video/mp4", data))
    received = await receive_file(chunks(body), HEADER, tmp_path, max_file_bytes=len(data), max_body_bytes=len(body))
    assert received.filename == "clip.mp4"
    assert received.content_type == "video/mp4"
    assert received.size_bytes == len(data)
    assert received.sha256 == hashlib.sha256(data).hexdigest()
    assert received.path.parent == tmp_path
    assert received.path.read_bytes() == data


@pytest.mark.asyncio
async def test_file_over_the_cap_is_refused_and_removed(tmp_path) -> None:
    body = multipart(("file", "clip.mp4", "video/mp4", b"x" * 100))
    with pytest.raises(FileTooLarge):
        await receive_file(chunks(body), HEADER, tmp_path, max_file_bytes=99, max_body_bytes=10_000)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_whole_body_over_the_cap_is_refused_even_without_a_big_file(tmp_path) -> None:
    body = multipart(("padding", None, None, b"p" * 500), ("file", "clip.mp4", "video/mp4", b"x" * 10))
    with pytest.raises(BodyTooLarge):
        await receive_file(chunks(body), HEADER, tmp_path, max_file_bytes=10_000, max_body_bytes=200)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_file_part_is_refused(tmp_path) -> None:
    body = multipart(("note", None, None, b"hello"))
    with pytest.raises(MissingFilePart):
        await receive_file(chunks(body), HEADER, tmp_path, max_file_bytes=10, max_body_bytes=10_000)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_not_multipart_is_refused(tmp_path) -> None:
    with pytest.raises(MissingFilePart):
        await receive_file(chunks(b"raw"), "application/octet-stream", tmp_path, max_file_bytes=10, max_body_bytes=10)


@pytest.mark.asyncio
async def test_body_without_the_closing_boundary_is_refused(tmp_path) -> None:
    body = multipart(("file", "clip.mp4", "video/mp4", b"x" * 100))
    truncated = body[: body.index(b"\r\n--" + BOUNDARY.encode() + b"--")]
    with pytest.raises(IncompleteBody):
        await receive_file(chunks(truncated), HEADER, tmp_path, max_file_bytes=10_000, max_body_bytes=10_000)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_oversized_part_headers_are_refused(tmp_path) -> None:
    head = f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.mp4\"\r\n".encode()
    body = head + b"X-Pad: " + b"p" * 10_000 + b"\r\n\r\nxx\r\n" + f"--{BOUNDARY}--\r\n".encode()
    with pytest.raises(UploadStreamError):
        await receive_file(chunks(body, 512), HEADER, tmp_path, max_file_bytes=10_000, max_body_bytes=100_000)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_an_open_upload_holds_a_lock_the_sweep_can_see(tmp_path) -> None:
    """Review R11: a stalled but open upload stays marked active, however long it is idle."""
    from video_postproduction.retention import temp_in_use

    body = multipart(("file", "clip.mp4", "video/mp4", b"x" * 50))
    seen: list[bool] = []

    async def stalled() -> AsyncIterator[bytes]:
        async for chunk in chunks(body):
            seen.extend(temp_in_use(part) for part in tmp_path.glob("upload-*.part"))
            yield chunk

    received = await receive_file(stalled(), HEADER, tmp_path, max_file_bytes=100, max_body_bytes=10_000)
    assert seen and all(seen)
    assert temp_in_use(received.path) is False
