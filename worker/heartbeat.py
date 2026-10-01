"""Worker registration and heartbeats.

The heartbeat runs on its own thread because the main loop blocks for the whole
duration of a job. It proves the process is alive, not that jobs make progress;
overdue jobs on a live worker are a separate check (recovery, step 14).
"""
import logging
import threading
import time

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from shared.models import Worker
from shared.workers import HealthThresholds, db_now, heartbeat_age

log = logging.getLogger(__name__)


class DuplicateWorkerId(Exception):
    """Another live worker is already using this id."""


class Heartbeat:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        worker_id: str,
        hostname: str,
        runtimes: list[str],
        thresholds: HealthThresholds,
    ):
        self._sf = session_factory
        self.worker_id = worker_id
        self.hostname = hostname
        self.runtimes = runtimes
        self.thresholds = thresholds

    def register(self, check_duplicate: bool = True) -> None:
        """Create or refresh this worker's row.

        Job ownership is fenced on worker_id, so two live processes sharing an
        id would each believe they own the same job. Refuse to start when a
        non-stopped row with a fresh heartbeat exists.
        """
        for _ in range(2):
            with self._sf() as s:
                row = s.get(Worker, self.worker_id)
                if check_duplicate and row is not None and row.stopped_at is None:
                    age = heartbeat_age(db_now(s), row.last_heartbeat_at)
                    if age < self.thresholds.unhealthy_after:
                        raise DuplicateWorkerId(
                            f"worker id {self.worker_id!r} heartbeated {age:.0f}s ago; "
                            "is another worker running with the same id? "
                            f"(a crashed one becomes reusable after {self.thresholds.unhealthy_after:.0f}s)"
                        )
                try:
                    if row is None:
                        s.add(Worker(
                            id=self.worker_id, hostname=self.hostname, runtimes=self.runtimes,
                            started_at=func.now(), last_heartbeat_at=func.now(), stopped_at=None,
                        ))
                    else:
                        row.hostname = self.hostname
                        row.runtimes = self.runtimes
                        row.started_at = func.now()
                        row.last_heartbeat_at = func.now()
                        row.stopped_at = None
                    s.commit()
                    return
                except IntegrityError:  # lost a registration race; retry as an update
                    s.rollback()
        raise RuntimeError(f"could not register worker {self.worker_id!r}")

    def register_with_retry(self, give_up_after: float = 60.0, delay: float = 2.0) -> None:
        """Wait for the database (and schema) at startup. Duplicate ids are fatal."""
        deadline = time.monotonic() + give_up_after
        while True:
            try:
                self.register()
                return
            except DuplicateWorkerId:
                raise
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise
                log.warning("worker=%s event=register_retry error=%s", self.worker_id, exc)
                time.sleep(delay)

    def beat(self) -> None:
        with self._sf() as s:
            res = s.execute(
                update(Worker).where(Worker.id == self.worker_id).values(last_heartbeat_at=func.now())
            )
            s.commit()
            missing = res.rowcount == 0
        if missing:  # row was deleted underneath us
            log.warning("worker=%s event=reregister", self.worker_id)
            self.register(check_duplicate=False)

    def deregister(self) -> None:
        with self._sf() as s:
            s.execute(update(Worker).where(Worker.id == self.worker_id).values(stopped_at=func.now()))
            s.commit()

    def run(self, stop: threading.Event) -> None:
        """Beat until `stop` is set. Failures are logged and retried next tick;
        a database outage must not kill the thread."""
        while not stop.wait(self.thresholds.heartbeat_interval):
            try:
                self.beat()
            except Exception:
                log.warning("worker=%s event=heartbeat_failed", self.worker_id, exc_info=True)
