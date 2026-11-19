"""Streaming multipart upload receiver (SPEC-001 R-INGEST).

FastAPI's ``UploadFile`` parses and spools the whole multipart body to the container's ``/tmp`` before any check can
run. This reads the request stream itself, counts every byte of the body against a cap, and writes the file part
straight into a temp file under ``STORAGE_ROOT/tmp`` (same filesystem as the final file) while hashing it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from anyio import to_thread
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

FILE_FIELD = b"file"
# The retention sweep removes temp files untouched for an hour; any arriving byte (also of other parts) keeps
# the file's mtime fresh so a slow but live upload is never swept (review R9).
TOUCH_INTERVAL_SECONDS = 60


class UploadStreamError(ValueError):
    """Base class for refused upload bodies."""


class BodyTooLarge(UploadStreamError):
    pass


class FileTooLarge(UploadStreamError):
    pass


class MissingFilePart(UploadStreamError):
    pass


class InvalidMultipart(UploadStreamError):
    """The parser refused the body (bad framing, too many or too large part headers)."""


class IncompleteBody(UploadStreamError):
    """The stream ended before the closing multipart boundary."""


@dataclass(frozen=True, slots=True)
class ReceivedFile:
    path: Path
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str


class _Collector:
    """Parser callbacks: remember the headers of each part and queue the bytes of the file part."""

    def __init__(self) -> None:
        self.pending: list[bytes] = []
        self.in_file = False
        self.seen_file = False
        self.filename: str | None = None
        self.content_type: str | None = None
        self._headers: dict[bytes, bytes] = {}
        self._field = b""
        self._value = b""
        self.ended = False

    def on_end(self) -> None:
        self.ended = True

    def on_part_begin(self) -> None:
        self._headers = {}

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[self._field.lower()] = self._value
        self._field = b""
        self._value = b""

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._headers.get(b"content-disposition"))
        is_file = (
            options.get(b"name") == FILE_FIELD and b"filename" in options and not self.seen_file
        )
        self.in_file = is_file
        if is_file:
            self.seen_file = True
            self.filename = options[b"filename"].decode("utf-8", "replace")
            content_type = self._headers.get(b"content-type")
            self.content_type = content_type.decode("latin-1") if content_type else None

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.in_file:
            self.pending.append(bytes(data[start:end]))

    def on_part_end(self) -> None:
        self.in_file = False


def _write(handle: BinaryIO, data: bytes) -> None:
    handle.write(data)


def _finish(handle: BinaryIO) -> None:
    handle.flush()
    try:
        os.fsync(handle.fileno())
    except OSError:
        pass
    handle.close()


async def receive_file(
    stream: AsyncIterator[bytes],
    content_type_header: str | None,
    temp_dir: Path,
    *,
    max_file_bytes: int,
    max_body_bytes: int,
) -> ReceivedFile:
    """Parse ``stream`` and store its ``file`` part in ``temp_dir``; the caller owns (and must delete) the file."""

    media_type, options = parse_options_header(content_type_header)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise MissingFilePart("the body is not multipart/form-data")

    temp_dir.mkdir(parents=True, exist_ok=True)
    collector = _Collector()
    parser = MultipartParser(
        boundary,
        callbacks={
            "on_part_begin": collector.on_part_begin,
            "on_header_field": collector.on_header_field,
            "on_header_value": collector.on_header_value,
            "on_header_end": collector.on_header_end,
            "on_headers_finished": collector.on_headers_finished,
            "on_part_data": collector.on_part_data,
            "on_part_end": collector.on_part_end,
            "on_end": collector.on_end,
        },
    )
    # python-multipart (>= 0.0.34) caps each part's headers (count 8, 4224 bytes), so header buffers stay small.
    descriptor, name = tempfile.mkstemp(dir=temp_dir, prefix="upload-", suffix=".part")
    path = Path(name)
    handle = os.fdopen(descriptor, "wb")
    digest = hashlib.sha256()
    body_bytes = 0
    file_bytes = 0
    try:
        last_touch = time.monotonic()
        async for chunk in stream:
            body_bytes += len(chunk)
            now = time.monotonic()
            if now - last_touch >= TOUCH_INTERVAL_SECONDS:
                os.utime(path)
                last_touch = now
            if body_bytes > max_body_bytes:
                raise BodyTooLarge("the request body exceeds the maximum size")
            try:
                parser.write(chunk)
            except MultipartParseError as exc:
                raise InvalidMultipart(str(exc)) from None
            if collector.pending:
                data = b"".join(collector.pending)
                collector.pending.clear()
                file_bytes += len(data)
                if file_bytes > max_file_bytes:
                    raise FileTooLarge("the uploaded file exceeds the maximum size")
                digest.update(data)
                await to_thread.run_sync(_write, handle, data)
        parser.finalize()
        if not collector.seen_file or collector.filename is None:
            raise MissingFilePart("no file part named 'file'")
        if not collector.ended:
            raise IncompleteBody("the body ended before the closing boundary")
        await to_thread.run_sync(_finish, handle)
        return ReceivedFile(
            path=path,
            filename=collector.filename,
            content_type=collector.content_type,
            size_bytes=file_bytes,
            sha256=digest.hexdigest(),
        )
    except BaseException:
        try:
            handle.close()
        except OSError:
            pass  # a failed flush on a full disk must not keep the partial file
        finally:
            path.unlink(missing_ok=True)
        raise
