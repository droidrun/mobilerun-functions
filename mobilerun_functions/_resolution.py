"""Resolve declarations from the captured environment before calling main."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any

from pydantic import BaseModel

from ._adapters.credentials import CredentialsAdapter, read_cloud, read_file
from ._adapters.device import DeviceAdapter
from ._adapters.resources import MailboxAdapter, ProxyAdapter
from ._adapters.tasks import Tasks
from ._connection import Connection
from .bindings import Credentials, Mailbox, Operator, Proxy
from .context import Context, DeviceTarget, RunInfo
from .device import connect_device
from .entry import EntryPoint
from .errors import NotAvailableError

LOCAL_TASKS_REASON = "needs the framework agent add-on or --cloud"


async def call_bound(
    entry: EntryPoint,
    model_input: BaseModel,
    ctx: Context,
    connection: Connection,
    credentials_file: str | None,
) -> Any:
    async with AsyncExitStack() as stack:
        await _resolve(entry, model_input, ctx, connection, credentials_file, stack)
        if entry.is_async:
            return await entry.func(model_input, ctx)
        return await asyncio.to_thread(entry.func, model_input, ctx)


async def _resolve(
    entry: EntryPoint,
    model_input: BaseModel,
    ctx: Context,
    connection: Connection,
    credentials_file: str | None,
    stack: AsyncExitStack,
) -> None:
    interfaces = {item.interface for item in entry.bindings}
    local_tasks = Operator in interfaces and not connection.cloud
    if local_tasks:
        raise NotAvailableError("tasks", LOCAL_TASKS_REASON)
    local_proxy = Proxy in interfaces and not _cloud_device(ctx.run, connection)
    if local_proxy:
        raise NotAvailableError(
            "proxy", "needs a cloud device (use --cloud or --device-target cloud)"
        )
    client: Any = None
    device = None
    credentials = None
    packages = {item.options.package for item in entry.bindings if item.interface is Credentials}
    for item in entry.bindings:
        member = item.name
        reading_credentials = item.interface is Credentials and credentials is None
        try:
            remote_resource = item.interface in (Mailbox, Proxy, Operator)
            remote_credentials = item.interface is Credentials and connection.cloud
            needs_client = client is None and (remote_resource or remote_credentials)
            if needs_client:
                base_url, api_key = connection.require_cloud(member)
                from mobilerun_sdk import AsyncMobilerun

                client = await stack.enter_async_context(
                    AsyncMobilerun(api_key=api_key, base_url=base_url)
                )
            if item.interface is Credentials:
                if credentials is None:
                    if connection.cloud:
                        credentials = await read_cloud(
                            client, {p for p in packages if p is not None}
                        )
                    else:
                        if not credentials_file:
                            raise NotAvailableError("credentials", "pass --credentials-file PATH")
                        credentials = await asyncio.to_thread(read_file, credentials_file)
                assert item.options.package is not None
                value: Any = CredentialsAdapter(credentials, item.options.package)
            elif item.interface is Mailbox:
                mailbox_id = getattr(model_input, item.options.input or "")
                response = await client.mailboxes.retrieve(mailbox_id)
                value = MailboxAdapter(client, mailbox_id, response.data.address)
            elif item.interface is Proxy:
                if not ctx.run.device_id:
                    raise NotAvailableError("proxy", "this run has no device")
                proxy_id = getattr(model_input, item.options.input or "")
                await client.devices.proxy.connect(ctx.run.device_id, connect={"id": proxy_id})
                value = ProxyAdapter(client, ctx.run.device_id)
            elif item.interface is Operator:
                value = object()  # Marker only; the device gets the task client below.
            else:
                core = await _device(ctx, connection)
                if hasattr(core, "close"):
                    stack.callback(core.close)
                device = DeviceAdapter(core)
                value = device
            setattr(ctx, item.name, value)
        except NotAvailableError as exc:
            reason = str(exc).removeprefix(f"ctx.{exc.member} ")
            raise NotAvailableError(member, reason) from None
        except ImportError:
            raise NotAvailableError(member, "install mobilerun-functions[device]") from None
        except Exception as exc:
            raise NotAvailableError(member, _resolution_reason(exc, reading_credentials)) from None
    if Operator in interfaces:
        assert device is not None
        assert ctx.run.device_id is not None
        device._tasks = Tasks(client, ctx.run.device_id)


def _cloud_device(run: RunInfo, connection: Connection) -> bool:
    return connection.cloud or run.device_target == "cloud"


async def _device(ctx: Context, connection: Connection) -> Any:
    run = ctx.run
    target: DeviceTarget = "cloud" if _cloud_device(run, connection) else "local"
    return await asyncio.to_thread(
        connect_device, run.device_id, target=target, platform=run.device_platform,
        url=run.device_url, connection=(connection.base_url, connection.api_key),
    )


def _resolution_reason(exc: Exception, reading_credentials: bool) -> str:
    status_error = getattr(sys.modules.get("mobilerun_sdk"), "APIStatusError", ())
    if isinstance(exc, status_error):
        return f"API returned {getattr(exc, 'status_code', None)}"
    if reading_credentials:
        return "binding could not be resolved"
    return type(exc).__name__
