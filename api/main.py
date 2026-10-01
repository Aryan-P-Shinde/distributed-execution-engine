from contextlib import asynccontextmanager

import redis
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from api.db import engine, get_session
from api.jobs import router as jobs_router
from api.redis_client import get_redis
from api.stats import router as stats_router
from api.workers import get_thresholds
from api.workers import router as workers_router
from shared import models  # noqa: F401  (registers tables on Base)
from shared.db import Base
from shared.models import Controller
from shared.workers import HealthThresholds, db_now, heartbeat_age


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all is a stopgap until we add Alembic migrations.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Distributed Workload Execution Engine", lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(workers_router)
app.include_router(stats_router)


@app.get("/health")
def health() -> dict:
    """Liveness: the process is up. Touches no dependencies."""
    return {"status": "ok"}


@app.get("/ready")
def ready(
    session: Session = Depends(get_session),
    wakeup: redis.Redis = Depends(get_redis),
    thresholds: HealthThresholds = Depends(get_thresholds),
) -> dict:
    """Readiness. Postgres down -> 503. Anything else degrades but stays 200.

    - redis: only shortens worker wait time; workers poll Postgres regardless.
    - controller: without a recent heartbeat nothing recovers jobs from dead
      workers. Jobs still run; failures just are not cleaned up.
    """
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    try:
        wakeup.ping()
        redis_state = "ok"
    except redis.RedisError:
        redis_state = "unavailable"

    last = session.scalar(select(func.max(Controller.last_heartbeat_at)))
    if last is None:
        controller_state = "absent"
    elif heartbeat_age(db_now(session), last) >= thresholds.unhealthy_after:
        controller_state = "stale"
    else:
        controller_state = "ok"

    healthy = redis_state == "ok" and controller_state == "ok"
    return {
        "status": "ready" if healthy else "degraded",
        "database": "ok",
        "redis": redis_state,
        "controller": controller_state,
    }
