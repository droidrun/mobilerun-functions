"""Temporary core adapter; replaced by the framework factory when it ships."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from typing import Any

from ..bindings import UIState
from ..errors import NotAvailableError

SCREENSHOT_PREFIX = "mobilerun-screenshot-"


class _Snapshot:
    def __init__(self, raw: Any, width: int, height: int) -> None:
        self.elements: list[dict[str, Any]] = raw.nodes
        self.formatted_text = json.dumps(self.elements, ensure_ascii=False)
        self.phone_state: dict[str, Any] = raw.get("phone_state") or {}
        focused = self.phone_state.get("focusedElement")
        self.focused_text = str(focused.get("text", "")) if isinstance(focused, dict) else ""
        self.screen_width = width
        self.screen_height = height


class StateAdapter:
    def __init__(self, core: Any) -> None:
        self._core = core
        self._current: UIState | None = None

    async def refresh(self) -> UIState:
        raw = await asyncio.to_thread(self._core.ui)
        width, height = await asyncio.to_thread(self._core.screen_size)
        self._current = _Snapshot(raw, width, height)
        return self._current

    @property
    def current(self) -> UIState:
        if self._current is None:
            raise NotAvailableError(
                "device.state.current",
                "UI state has not been refreshed; call await device.state.refresh()",
            )
        return self._current


def _is_screenshot_temp_file(path: Path) -> bool:
    """Only the core's own temp file is a path; anything else is base64 (a JPEG starts `/9j/`)."""
    in_temp_dir = path.parent == Path(tempfile.gettempdir())
    return in_temp_dir and path.name.startswith(SCREENSHOT_PREFIX)


class DeviceAdapter:
    def __init__(self, core: Any, device_id: str | None) -> None:
        self._core = core
        self.device_id = device_id
        self._state = StateAdapter(core)

    @property
    def state(self) -> StateAdapter:
        return self._state

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        value = await asyncio.to_thread(self._core.screenshot, hide_overlay=hide_overlay)
        path = Path(value)
        if _is_screenshot_temp_file(path):
            try:
                return await asyncio.to_thread(path.read_bytes)
            finally:
                await asyncio.to_thread(path.unlink)
        return base64.b64decode(value, validate=True)

    async def tap(self, x: int, y: int) -> None:
        await asyncio.to_thread(self._core.tap, x, y)

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: float = 1000
    ) -> None:
        await asyncio.to_thread(self._core.swipe, x1, y1, x2, y2, ms=int(duration_ms))

    async def drag(
        self, x1: int, y1: int, x2: int, y2: int, duration: float = 3.0
    ) -> None:
        await self.swipe(x1, y1, x2, y2, duration * 1000)

    async def input_text(
        self, text: str, clear: bool = False, stealth: bool = False, wpm: int = 0
    ) -> bool:
        await asyncio.to_thread(
            self._core.type, text, clear=clear, humanize=stealth, wpm=wpm or None
        )
        return True

    async def press_button(self, button: str) -> None:
        await asyncio.to_thread(self._core.key, button)

    async def press_key_code(self, key_code: int) -> None:
        await asyncio.to_thread(self._core.key, key_code)

    async def start_app(self, package: str, activity: str | None = None) -> str:
        await asyncio.to_thread(self._core.start_app, package, activity=activity)
        return ""

    async def list_apps(self, include_system: bool = False) -> list[dict[str, str]]:
        apps = await asyncio.to_thread(self._core.list_apps, include_system_apps=include_system)
        return [{key: str(value) for key, value in app.items()} for app in apps]

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
