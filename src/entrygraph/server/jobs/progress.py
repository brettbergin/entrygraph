"""Bridge from the scanner's progress callback to Job-row updates.

Runs inside the job's worker thread. Writes are throttled (≥0.5s apart) so a
fast parse loop doesn't hammer the app DB; each write is a short-lived session.
The callback's return value carries cancellation back into the scanner.
"""

from __future__ import annotations

import time

from sqlalchemy import select

from entrygraph.server.models import Job

# phase -> (progress floor, progress ceiling): extraction dominates wall time,
# so it gets most of the bar; resolve/write are cheap but visible.
_PHASE_SPAN = {
    "cloning": (0.0, 0.10),
    "walking": (0.10, 0.15),
    "extracting": (0.15, 0.85),
    "resolving": (0.85, 0.95),
    "writing": (0.95, 1.0),
}

_MIN_WRITE_INTERVAL_S = 0.5


class ProgressReporter:
    def __init__(self, session_factory, job_id: str) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self._last_write = 0.0

    def __call__(self, phase: str, done: int, total: int) -> bool:
        """Scanner-compatible callback: records progress, returns False when the
        job row has cancel_requested set."""
        now = time.monotonic()
        force = phase in ("walking", "resolving", "writing")  # phase edges always land
        if not force and (now - self._last_write) < _MIN_WRITE_INTERVAL_S:
            return True
        self._last_write = now

        floor, ceil = _PHASE_SPAN.get(phase, (0.0, 1.0))
        fraction = (done / total) if total else 1.0
        progress = floor + (ceil - floor) * min(1.0, fraction)
        message = f"{phase}: {done}/{total}" if total > 1 else phase

        with self._session_factory() as session:
            job = session.execute(select(Job).where(Job.id == self._job_id)).scalar_one_or_none()
            if job is None:
                return False
            job.phase = phase
            job.progress = round(progress, 4)
            job.message = message
            cancelled = job.cancel_requested
            session.commit()
        return not cancelled

    def set_phase(self, phase: str, message: str | None = None) -> bool:
        """Mark a coarse phase (e.g. "cloning") outside the scanner callback."""
        return self(phase, 0, 1) and (message is None or self._note(message))

    def _note(self, message: str) -> bool:
        with self._session_factory() as session:
            job = session.execute(select(Job).where(Job.id == self._job_id)).scalar_one_or_none()
            if job is None:
                return False
            job.message = message
            session.commit()
        return True
