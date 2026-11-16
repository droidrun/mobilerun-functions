"""`Context`: what the platform provides to the entry point."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, JsonValue, StrictInt, TypeAdapter

from ._io import atomic_write_text, dumps_json, one_line
from .dataset import DatasetSink
from .device import connect_device
from .errors import NotAvailableError

if TYPE_CHECKING:
    from mobilerun_core import Device

RESULT_MAX_BYTES = 524288
STATE_MAX_BYTES = 524288
STATUS_FILE_ENV = "MOBILERUN_ROUTINES_STATUS_FILE"


RunMode = Literal["live", "dry_run", "verification"]
DeviceTarget = Literal["local", "cloud"]
DevicePlatform = Literal["android", "ios"]
_STRICT_INT = TypeAdapter(StrictInt)


@dataclass(frozen=True)
class RunInfo:
    run_id: str = "local"
    attempt: int = 1
    deadline: datetime | None = None
    mode: RunMode = "live"
    device_id: str | None = None
    device_target: DeviceTarget = "cloud"
    device_platform: DevicePlatform | None = None
    device_url: str | None = None


class Log:
    """Log lines go to stderr; `status` publishes the latest status through a file."""

    def _write(self, level: str, msg: object) -> None:
        sys.stderr.write(f"[{level}] {one_line(msg)}\n")
        sys.stderr.flush()

    def info(self, msg: object) -> None:
        self._write("INFO", msg)

    def warning(self, msg: object) -> None:
        self._write("WARNING", msg)

    def error(self, msg: object) -> None:
        self._write("ERROR", msg)

    def status(self, msg: object) -> None:
        path = os.environ.get(STATUS_FILE_ENV)
        if not path:
            return
        try:
            atomic_write_text(path, one_line(msg) + "\n")
        except OSError:
            pass  # status is best effort and never fails the script


@dataclass(frozen=True)
class StateSnapshot:
    """A snapshot recorded by `ctx.state.save`; `data` is JSON-compatible."""

    schema: int
    data: Any


class State:
    """Data carried from the last successful run to the next."""

    def __init__(
        self,
        configured: bool = True,
        previous: JsonValue = None,
        previous_schema: int | None = None,
    ) -> None:
        self._configured = configured
        self._previous = previous
        self._previous_schema = previous_schema
        self._saved: StateSnapshot | None = None

    @property
    def previous(self) -> JsonValue:
        """Saved data of the last successful run, or `None`."""
        return self._previous

    @property
    def previous_schema(self) -> int | None:
        """The `schema` the previous snapshot was saved with, or `None` without one."""
        return self._previous_schema

    def save(self, data: Any, schema: int = 1) -> None:
        """Record the snapshot the platform stores after a successful run (last call wins)."""
        if not self._configured:
            raise NotAvailableError(
                "state.save", "is not available in this run: pass --state-file PATH to run"
            )
        schema = _STRICT_INT.validate_python(schema)
        payload = (
            data.model_dump_json(by_alias=True) if isinstance(data, BaseModel) else dumps_json(data)
        )
        # Copy now so later changes to `data` do not alter the snapshot.
        self._saved = StateSnapshot(schema=schema, data=json.loads(payload))

    @property
    def saved(self) -> StateSnapshot | None:
        return self._saved


class Context:
    """Passed to the entry point as `ctx`."""

    def __init__(
        self,
        run: RunInfo,
        log: Log | None = None,
        state: State | None = None,
        dataset: DatasetSink | None = None,
    ) -> None:
        self.run = run
        self.log = log if log is not None else Log()
        self.state = state if state is not None else State()
        self.dataset = dataset if dataset is not None else DatasetSink()
        self._partial_reason: str | None = None
        self._device: Any = None

    def partial(self, reason: str) -> None:
        """Mark the run as partial; the entry point still returns its result."""
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")
        self._partial_reason = str.__str__(reason)  # a plain str

    @property
    def partial_reason(self) -> str | None:
        return self._partial_reason

    def device(self) -> Device:
        """The run's `mobilerun_core` device (connected once, then reused)."""
        if self._device is None:
            self._device = connect_device(
                self.run.device_id,
                target=self.run.device_target,
                platform=self.run.device_platform,
                url=self.run.device_url,
            )
        return self._device

    @property
    def credentials(self) -> Any:
        raise NotAvailableError("credentials")

    @property
    def files(self) -> Any:
        raise NotAvailableError("files")

    @property
    def llm(self) -> Any:
        raise NotAvailableError("llm")
