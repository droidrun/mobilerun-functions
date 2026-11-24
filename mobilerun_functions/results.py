"""Task result models and expectation exceptions matching the framework.

These become re-exports of the framework classes when the adapters switch to its factory.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ExecutionResult(BaseModel, Generic[T]):
    success: bool
    answer: str | None = None
    output: T | None = None
    steps: int | None = None
    task_id: str | None = None
    trajectory_path: str | None = None
    device: str


class ExpectationVerdict(BaseModel):
    passed: bool
    reason: str


class ExpectationResult(BaseModel):
    passed: bool
    condition: str
    reason: str
    task: ExecutionResult[ExpectationVerdict]


class ExpectationFailed(Exception):
    def __init__(self, result: ExpectationResult) -> None:
        self.result = result
        self.condition = result.condition
        self.reason = result.reason
        super().__init__(self.reason or f"Expectation failed: {self.condition}")


class ExpectationInconclusive(Exception):
    def __init__(self, condition: str, task: ExecutionResult[ExpectationVerdict]) -> None:
        self.condition = condition
        self.task = task
        super().__init__(
            "Could not determine whether the expectation holds: no structured verdict"
        )
