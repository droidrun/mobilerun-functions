"""Declare resource needs independently of their local or cloud implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel

from .results import ExecutionResult

if TYPE_CHECKING:
    import openai

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
    async def press_key_code(self, key_code: int) -> None: ...


class Credentials(Protocol):
    """Credentials of the package declared with `binding(package=...)`."""

    async def list(self) -> list[dict[str, Any]]: ...
    async def type_field(
        self, device: Device, field: str, *, name: str | None = None
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


class AI(Protocol):
    """OpenAI-compatible access to the platform AI, billed to the run's organization."""

    @property
    def base_url(self) -> str: ...
    @property
    def api_key(self) -> str: ...
    def openai(self) -> openai.AsyncOpenAI:
        """A new client for `base_url`; needs `mobilerun-functions[ai]`."""
        ...


class Tasks(Protocol):
    """Run natural-language tasks on the run's device; spends normal task usage."""

    async def run(
        self,
        task: str,
        *,
        device: Device,
        output: type[T] | None = None,
        max_steps: int | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult[T]: ...


@dataclass(frozen=True)
class Binding:
    package: str | None = None


def binding(*, package: str | None = None) -> Any:
    """A declaration, replaced by the runner before the entry point is called."""
    return Binding(package=package)
