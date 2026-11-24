"""Task execution on an existing device; no resource provisioning."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..results import ExecutionResult

T = TypeVar("T", bound=BaseModel)
TASK_START_TIMEOUT = 30.0
TASK_TIMEOUT = 1800.0
POLL_INTERVAL = 2.0


class Tasks:
    def __init__(self, client: Any, device_id: str) -> None:
        self._client = client
        self._device_id = device_id

    async def run(
        self,
        instruction: str,
        *,
        output: type[T] | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult[T]:
        params: dict[str, Any] = {"device_id": self._device_id, "task": instruction}
        if output is not None:
            params["output_schema"] = output.model_json_schema()
        if max_steps is not None:
            params["max_steps"] = max_steps
        response = await self._client.tasks.run(**params, timeout=TASK_START_TIMEOUT)
        task_id = response.id
        try:
            async with asyncio.timeout(TASK_TIMEOUT if timeout is None else timeout):
                while True:
                    state = await self._client.tasks.get_status(task_id)
                    if state.terminal:
                        return self._result(state, task_id, output)
                    await asyncio.sleep(POLL_INTERVAL)
        except BaseException:
            await self._stop(task_id)
            raise

    async def _stop(self, task_id: str) -> None:
        try:
            async with asyncio.timeout(5):
                await self._client.tasks.stop(task_id)
        except Exception:
            pass  # Cleanup must not replace a timeout or cancellation.

    def _result(self, state: Any, task_id: str, output: type[T] | None) -> ExecutionResult[T]:
        parsed = None
        success = bool(state.succeeded)
        answer = state.message
        has_output = output is not None and state.output is not None
        if has_output:
            assert output is not None
            try:
                parsed = output.model_validate(state.output)
            except ValidationError:
                success = False
                answer = "task output does not match the output schema"
        return ExecutionResult[T](
            success=success,
            answer=answer,
            output=parsed,
            steps=state.steps,
            task_id=task_id,
            device="CloudDevice",
        )
