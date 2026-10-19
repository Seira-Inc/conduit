"""One holder per connector browser, for a bounded time. A holder that stops renewing (it crashed,
or sits idle with the browser open) loses the lease, so a sign-in can take the browser over."""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


class SessionBusy(ValueError):
    """Another process holds this connector's browser."""


class SessionLease:
    def __init__(self, path: Path, *, ttl: float = 300.0):
        self.path, self.ttl = path, ttl
        self.holder = f"{os.getpid()}:{uuid4().hex[:8]}"
        self.held = False

    def _current(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _taken(self, current) -> bool:
        return bool(current) and current.get("holder") != self.holder and current.get("expires", 0) > time.time()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"holder": self.holder, "pid": os.getpid(),
                                         "expires": time.time() + self.ttl}), encoding="utf-8")
        for attempt in range(3):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
        self.held = True

    def acquire(self) -> None:
        current = self._current()
        if self._taken(current):
            left = max(1, int(current["expires"] - time.time()))
            raise SessionBusy(
                f"This connector's browser is held by another client for up to {left} more seconds. "
                "Release it there (browser_close, or Release preview in Conduit) or wait; an idle holder "
                "loses it on its own.")
        self._write()

    def renew(self) -> bool:
        """Extend the lease. False when another holder took it while this one was idle."""
        if self._taken(self._current()):
            self.held = False
            return False
        self._write()
        return True

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        current = self._current()
        if current and current.get("holder") == self.holder:
            with suppress(OSError):
                self.path.unlink()
