"""Runtime coordination for AutoRewarder processes.

The GUI and scheduled CLI can be active in separate processes.  This module
uses a small lock file in the application's runtime-data directory so those
processes can coordinate without touching repository files or browser state.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .config import APP_DIR

RUN_ORIGIN_BATCH = "batch"
RUN_ORIGIN_SCHEDULED = "scheduled"
RUN_ORIGIN_INTERACTIVE = "interactive"
_VALID_ORIGINS = {RUN_ORIGIN_BATCH, RUN_ORIGIN_SCHEDULED, RUN_ORIGIN_INTERACTIVE}


def _default_pid_alive(pid: int) -> bool:
    """Return whether *pid* appears to identify a live local process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class RunLease:
    """Ownership token returned by :class:`RunCoordinator.acquire`."""

    coordinator: "RunCoordinator"
    session_id: str
    origin: str
    released: bool = False

    def stop_requested(self) -> bool:
        """Return whether another process asked this scheduled run to stop."""
        return self.coordinator.stop_requested(self)

    def release(self) -> None:
        """Release this lease once, leaving another owner's lock untouched."""
        if self.released:
            return
        self.coordinator.release(self)
        self.released = True


class RunCoordinator:
    """Coordinate one active AutoRewarder run across local processes."""

    def __init__(
        self,
        state_path: Optional[str] = None,
        pid_alive: Optional[Callable[[int], bool]] = None,
    ):
        self.state_path = state_path or os.path.join(APP_DIR, "active_run.json")
        self.stop_path = self.state_path + ".stop"
        self._pid_alive = pid_alive or _default_pid_alive

    def acquire(self, origin: str) -> Optional[RunLease]:
        """Acquire the shared lease, returning ``None`` when another run owns it."""
        if origin not in _VALID_ORIGINS:
            raise ValueError(f"Unknown run origin: {origin}")

        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        for _ in range(2):
            session_id = uuid.uuid4().hex
            state = {
                "origin": origin,
                "pid": os.getpid(),
                "session_id": session_id,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }
            try:
                with open(self.state_path, "x", encoding="utf-8") as handle:
                    json.dump(state, handle)
                return RunLease(self, session_id, origin)
            except FileExistsError:
                active = self.active_run()
                if active is not None:
                    return None
        return None

    def active_run(self) -> Optional[dict]:
        """Return the live active-run record, removing malformed or stale locks."""
        record = self._read_json(self.state_path)
        if record is None:
            return None
        if self._is_stale(record):
            self._remove_file(self.state_path)
            self._remove_file(self.stop_path)
            return None
        return record

    def request_stop_for_scheduled(self) -> bool:
        """Ask the active scheduled run to stop; never interrupts interactive runs."""
        active = self.active_run()
        if active is None or active.get("origin") != RUN_ORIGIN_SCHEDULED:
            return False

        payload = {"session_id": active["session_id"]}
        with open(self.stop_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return True

    def stop_requested(self, lease: RunLease) -> bool:
        """Return True only for a stop request addressed to this active lease."""
        payload = self._read_json(self.stop_path)
        return bool(payload and payload.get("session_id") == lease.session_id)

    def release(self, lease: RunLease) -> None:
        """Remove the lock only when this lease still owns it."""
        active = self._read_json(self.state_path)
        if active and active.get("session_id") == lease.session_id:
            self._remove_file(self.state_path)
            stop = self._read_json(self.stop_path)
            if stop and stop.get("session_id") == lease.session_id:
                self._remove_file(self.stop_path)

    def wait_for_release(self, timeout: float, interval: float = 0.2) -> bool:
        """Wait for no active lease, returning False after *timeout* seconds."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.active_run() is not None:
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, float(interval)))
        return True

    def _is_stale(self, record: dict) -> bool:
        session_id = record.get("session_id")
        origin = record.get("origin")
        pid = record.get("pid")
        if (
            not isinstance(session_id, str)
            or origin not in _VALID_ORIGINS
            or not isinstance(pid, int)
            or isinstance(pid, bool)
        ):
            return True
        return not self._pid_alive(pid)

    @staticmethod
    def _read_json(path: str) -> Optional[dict]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
