"""Recovery controller entrypoint:  python -m controller.main

Run one or several replicas. Each pass scans for orphaned jobs and recovers
them; a heartbeat is recorded only after a pass succeeds, so a stale heartbeat
means recovery is not working (the API's /ready reports it).
"""
import logging
import os
import signal
import socket
import threading

from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from controller.config import ControllerConfig
from controller.recovery import recover_once
from shared.db import make_session_factory
from shared.models import Controller
from shared.wakeup import make_client

log = logging.getLogger("controller")


def record_heartbeat(session_factory: sessionmaker[Session], controller_id: str, hostname: str) -> None:
    with session_factory() as s:
        updated = s.execute(
            update(Controller).where(Controller.id == controller_id).values(last_heartbeat_at=func.now())
        ).rowcount
        if updated == 0:
            s.add(Controller(id=controller_id, hostname=hostname,
                             started_at=func.now(), last_heartbeat_at=func.now()))
        s.commit()


def run_forever(session_factory, cfg: ControllerConfig, notifier, stop: threading.Event) -> None:
    hostname = socket.gethostname()
    while not stop.is_set():
        try:
            recover_once(session_factory, cfg, notifier)
            record_heartbeat(session_factory, cfg.controller_id, hostname)
        except Exception:
            log.exception("controller=%s event=pass_failed", cfg.controller_id)
        stop.wait(cfg.interval)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = ControllerConfig.from_env()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("controller=%s event=started interval=%ss overdue_grace=%ss dead_after=%ss",
             cfg.controller_id, cfg.interval, cfg.overdue_grace, cfg.health.dead_after)
    run_forever(make_session_factory(cfg.database_url), cfg, make_client(cfg.redis_url), stop)
    log.info("controller=%s event=stopped", cfg.controller_id)


if __name__ == "__main__":
    main()
