import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://engine:engine@localhost:5432/engine"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
