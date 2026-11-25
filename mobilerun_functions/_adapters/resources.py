"""Existing mailbox and device proxy operations through the generated SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .tasks import POLL_INTERVAL


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MailboxAdapter:
    def __init__(
        self,
        client: Any,
        mailbox_id: str,
        address: str | None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._id = mailbox_id
        self._address = address
        self._clock = clock

    @property
    def address(self) -> str | None:
        return self._address

    async def wait_for_code(
        self,
        *,
        sender: str | None = None,
        timeout: float = 60,
        after: datetime | None = None,
    ) -> str:
        # One baseline per wait, so a code that arrived before it is never returned.
        params: dict[str, Any] = {"after": after if after is not None else self._clock()}
        if sender is not None:
            params["sender"] = sender
        async with asyncio.timeout(timeout):
            while True:
                # 204 means no code yet; 404, 409 and auth errors propagate and end the wait.
                raw = await self._client.mailboxes.with_raw_response.otp(self._id, **params)
                if raw.status_code != 204:
                    response = await raw.parse()
                    return str(response.data.code)
                await asyncio.sleep(POLL_INTERVAL)


class ProxyAdapter:
    def __init__(self, client: Any, device_id: str) -> None:
        self._client = client
        self._device_id = device_id

    async def status(self) -> dict[str, Any]:
        response = await self._client.devices.proxy.status(self._device_id)
        result: dict[str, Any] = response.model_dump(mode="json", by_alias=True)
        return result
