"""Job specification: the declarative description of a workload.

This is the API's input contract and what a worker receives. It carries no
execution state.
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobType(StrEnum):
    SCRIPT = "script"
    TEST = "test"
    BUILD = "build"


class Runtime(StrEnum):
    PYTHON = "python"
    NODE = "node"


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: float = Field(default=1.0, ge=0.1, le=4.0)
    memory_mb: int = Field(default=512, ge=64, le=4096)


# Inside the container, `timeout` kills the workload this long after its limit,
# as a backstop if the worker dies. The controller's overdue grace must exceed it.
EXECUTOR_BACKSTOP_SECONDS = 5

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_FILES = 50


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: JobType
    runtime: Runtime
    command: str = Field(min_length=1, max_length=2000)
    # MVP source model: inline files, path -> content. No git cloning yet.
    files: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    resources: Resources = Field(default_factory=Resources)
    # Retries apply only to failures that are not the workload's fault (worker
    # lost, executor error). A retry may run the workload twice, so set 0 for
    # jobs with side effects that are not idempotent.
    max_retries: int = Field(default=2, ge=0, le=5)
    network_enabled: bool = False

    @field_validator("command")
    @classmethod
    def _command_sane(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("command must not be blank")
        if "\x00" in v:
            raise ValueError("command must not contain NUL bytes")
        return v

    @field_validator("files")
    @classmethod
    def _files_sane(cls, files: dict[str, str]) -> dict[str, str]:
        if len(files) > MAX_FILES:
            raise ValueError(f"at most {MAX_FILES} files")
        total = 0
        for path, content in files.items():
            parts = path.split("/")
            if (
                not path
                or path.startswith("/")
                or "\\" in path
                or "\x00" in path
                or any(p in ("", ".", "..") for p in parts)
            ):
                raise ValueError(f"unsafe file path: {path!r}")
            size = len(content.encode("utf-8"))
            if size > MAX_FILE_BYTES:
                raise ValueError(f"file too large: {path!r}")
            total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("total source size too large")
        return files
