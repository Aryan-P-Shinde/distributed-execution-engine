"""Worker process entrypoint:  python -m worker.main"""
import logging
import os
import signal
import socket
import sys
import threading

from shared.db import make_session_factory
from shared.wakeup import make_blocking_client, make_client
from worker.config import WorkerConfig
from worker.docker_executor import DockerExecutor
from worker.heartbeat import DuplicateWorkerId, Heartbeat
from worker.loop import run_forever

log = logging.getLogger("worker")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = WorkerConfig.from_env()
    stop = threading.Event()

    def request_stop(signum, _frame):
        log.info("worker=%s event=stop_requested signal=%s", cfg.worker_id, signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    session_factory = make_session_factory(cfg.database_url)
    heartbeat = Heartbeat(session_factory, cfg.worker_id, socket.gethostname(),
                          cfg.runtimes, cfg.health)
    try:
        heartbeat.register_with_retry()
    except DuplicateWorkerId as exc:
        log.error("worker=%s event=duplicate_id %s", cfg.worker_id, exc)
        sys.exit(1)

    # Separate stop event: SIGTERM lets the current job finish, and the worker
    # must keep heartbeating until then or it would look dead mid-job.
    hb_stop = threading.Event()
    hb_thread = threading.Thread(target=heartbeat.run, args=(hb_stop,), name="heartbeat", daemon=True)
    hb_thread.start()

    log.info("worker=%s event=started runtimes=%s poll_interval=%s heartbeat=%ss",
             cfg.worker_id, ",".join(cfg.runtimes), cfg.poll_interval, cfg.health.heartbeat_interval)
    try:
        run_forever(
            session_factory,
            DockerExecutor(),
            cfg.worker_id,
            cfg.runtimes,
            make_blocking_client(cfg.redis_url, cfg.poll_interval),
            cfg.poll_interval,
            stop,
            notifier=make_client(cfg.redis_url),
        )
    finally:
        hb_stop.set()
        hb_thread.join(timeout=5)
        try:
            heartbeat.deregister()
        except Exception:
            log.warning("worker=%s event=deregister_failed", cfg.worker_id, exc_info=True)
    log.info("worker=%s event=stopped", cfg.worker_id)


if __name__ == "__main__":
    main()
