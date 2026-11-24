"""Existing mailbox and device proxy operations through the generated SDK."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from .tasks import POLL_INTERVAL


class MailboxAdapter:
    def __init__(self, client: Any, mailbox_id: str, address: str | None) -> None:
        self._client = client
        self._id = mailbox_id
        self._address = address

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
        from mobilerun_sdk import NotFoundError

        params: dict[str, Any] = {}
        if sender is not None:
            params["sender"] = sender
        if after is not None:
            params["after"] = after
        async with asyncio.timeout(timeout):
            while True:
                try:
                    raw = await self._client.mailboxes.with_raw_response.otp(self._id, **params)
                except NotFoundError:
                    raw = None
                has_code = raw is not None and raw.status_code != 204
                if has_code:
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
