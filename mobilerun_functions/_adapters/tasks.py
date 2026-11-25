"""Task execution on the run's existing device; no resource provisioning."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..errors import NotAvailableError
from ..results import ExecutionResult
from .device import DeviceAdapter

T = TypeVar("T", bound=BaseModel)
S = TypeVar("S")
TASK_START_TIMEOUT = 30.0
TASK_TIMEOUT = 1800.0
POLL_INTERVAL = 2.0
RUN_TASK_TIMEOUT_MAX_S = 2700
RUN_TASK_TERMINAL = frozenset({"completed", "failed", "cancelled"})
RUN_TASK_REFUSALS = frozenset({
    "device_lease_not_held", "tasks_not_declared", "run_not_active", "device_not_in_run",
})


class _Tasks:
    def __init__(self, client: Any, device: DeviceAdapter) -> None:
        self._client = client
        self._device = device

    def _device_id(self, device: Any) -> str:
        device_id = device.device_id if device is self._device else None
        if device_id is None:
            raise NotAvailableError("tasks", "device is not the run's device")
        return str(device_id)


class CloudTasks(_Tasks):
    """Public `/v1/tasks` with the caller's own key (`--cloud` outside the platform)."""

    async def run(
        self,
        task: str,
        *,
        device: Any,
        output: type[T] | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult[T]:
        params: dict[str, Any] = {"device_id": self._device_id(device), "task": task}
        if output is not None:
            params["output_schema"] = output.model_json_schema()
        if max_steps is not None:
            params["max_steps"] = max_steps
        response = await self._client.tasks.run(**params, timeout=TASK_START_TIMEOUT)
        task_id = response.id

        async def fetch() -> Any:
            state = await self._client.tasks.get_status(task_id)
            return state if state.terminal else None

        async def stop() -> None:
            await self._client.tasks.stop(task_id)

        state: Any = await _poll(fetch, stop, timeout)
        return _execution_result(
            task_id, output, state.succeeded, state.message, state.output, state.steps
        )


class RunTasks(_Tasks):
    """The run's own task routes: the platform holds the device lease for the run."""

    def __init__(self, client: Any, device: DeviceAdapter, run_id: str) -> None:
        super().__init__(client, device)
        self._path = f"/compute/runs/{run_id}/tasks"

    async def run(
        self,
        task: str,
        *,
        device: Any,
        output: type[T] | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult[T]:
        body: dict[str, Any] = {"task": task, "deviceId": self._device_id(device)}
        if output is not None:
            body["outputSchema"] = output.model_json_schema()
        if max_steps is not None:
            body["maxSteps"] = max_steps
        if timeout is not None:
            body["timeoutS"] = min(max(math.ceil(timeout), 1), RUN_TASK_TIMEOUT_MAX_S)
        options = {"headers": {"Idempotency-Key": str(uuid4())}, "timeout": TASK_START_TIMEOUT}
        started = await self._call(
            self._client.post(self._path, cast_to=object, body=body, options=options)
        )
        task_id = started["id"]
        task_path = f"{self._path}/{task_id}"

        async def fetch() -> Any:
            state = await self._call(self._client.get(task_path, cast_to=object))
            return state if state["status"] in RUN_TASK_TERMINAL else None

        async def stop() -> None:
            await self._client.post(f"{task_path}/cancel", cast_to=object)

        state: Any = await _poll(fetch, stop, timeout)
        # The public task id (readable with /v1/tasks); the run-task id only if no task started.
        public_id = state.get("taskId") or task_id
        return _execution_result(
            public_id, output, state.get("succeeded"), state.get("message"),
            state.get("output"), state.get("steps"),
        )

    async def _call(self, request: Awaitable[Any]) -> dict[str, Any]:
        from mobilerun_sdk import APIStatusError

        try:
            response = await request
        except APIStatusError as exc:
            code = exc.body.get("code") if isinstance(exc.body, dict) else None
            if code in RUN_TASK_REFUSALS:
                raise NotAvailableError("tasks", code) from None
            raise
        data: dict[str, Any] = response["data"]
        return data


async def _poll(
    fetch: Callable[[], Awaitable[S | None]],
    stop: Callable[[], Awaitable[None]],
    timeout: float | None,
) -> S:
    try:
        async with asyncio.timeout(TASK_TIMEOUT if timeout is None else timeout):
            while True:
                state = await fetch()
                if state is not None:
                    return state
                await asyncio.sleep(POLL_INTERVAL)
    except BaseException:
        try:
            async with asyncio.timeout(5):
                await stop()
        except Exception:
            pass  # Cleanup must not replace a timeout or cancellation.
        raise


def _execution_result(
    task_id: str,
    output: type[T] | None,
    succeeded: Any,
    message: Any,
    raw_output: Any,
    steps: Any,
) -> ExecutionResult[T]:
    parsed = None
    success = bool(succeeded)
    answer = message
    has_output = output is not None and raw_output is not None
    if has_output:
        assert output is not None
        try:
            parsed = output.model_validate(raw_output)
        except ValidationError:
            success = False
            answer = "task output does not match the output schema"
    return ExecutionResult[T](
        success=success,
        answer=answer,
        output=parsed,
        steps=steps,
        task_id=task_id,
        device="CloudDevice",
    )
