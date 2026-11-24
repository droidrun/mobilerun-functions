"""Declare resource needs independently of their local or cloud implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .results import ExecutionResult, ExpectationResult

T = TypeVar("T", bound=BaseModel)


class UIState(Protocol):
    elements: list[dict[str, Any]]
    formatted_text: str
    focused_text: str
    phone_state: dict[str, Any]
    screen_width: int
    screen_height: int


class DeviceState(Protocol):
    async def refresh(self) -> UIState: ...
    @property
    def current(self) -> UIState: ...


class Device(Protocol):
    async def screenshot(self, hide_overlay: bool = True) -> bytes: ...
    async def tap(self, x: int, y: int) -> None: ...
    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: float = 1000
    ) -> None: ...
    async def drag(
        self, x1: int, y1: int, x2: int, y2: int, duration: float = 3.0
    ) -> None: ...
    async def input_text(
        self, text: str, clear: bool = False, stealth: bool = False, wpm: int = 0
    ) -> bool: ...
    async def press_button(self, button: str) -> None: ...
    async def start_app(self, package: str, activity: str | None = None) -> str: ...
    async def list_apps(self, include_system: bool = False) -> list[dict[str, str]]: ...
    async def wait(self, seconds: float) -> None: ...
    @property
    def state(self) -> DeviceState: ...
    async def do(
        self,
        instruction: str,
        *,
        output: type[T] | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult[T]: ...
    async def expect(
        self, condition: str, *, timeout: float | None = None
    ) -> ExpectationResult:
        """Return on pass; raise ExpectationFailed or ExpectationInconclusive otherwise."""
        ...
    async def press_key_code(self, key_code: int) -> None: ...


class Credentials(Protocol):
    async def list(self, package: str | None = None) -> list[dict[str, Any]]: ...
    async def type_field(
        self, device: Device, package: str | None, field: str, name: str | None = None
    ) -> None: ...


class Mailbox(Protocol):
    @property
    def address(self) -> str | None: ...
    async def wait_for_code(
        self,
        *,
        sender: str | None = None,
        timeout: float = 60,
        after: datetime | None = None,
    ) -> str: ...


class Proxy(Protocol):
    async def status(self) -> dict[str, Any]: ...


class Operator(Protocol):
    """Declaring this binding enables the device's task operations."""


@dataclass(frozen=True)
class Binding:
    package: str | None = None
    input: str | None = None


def binding(*, package: str | None = None, input: str | None = None) -> Any:
    """A declaration, replaced by the runner before the entry point is called."""
    return Binding(package=package, input=input)
