"""Background thread that runs job recovery at API start and every few minutes (SPEC-001 R-LEASE)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..error_reporting import report_exception

_logger = logging.getLogger(__name__)


class RecoveryLoop:
    def __init__(self, recover: Callable[[], object], *, interval_seconds: float) -> None:
        self._recover = recover
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._interval <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="job-recovery", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._recover()
            except Exception as exc:  # keep recovering on the next pass
                _logger.exception("job recovery pass failed")
                report_exception(exc)
            self._stop.wait(self._interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
