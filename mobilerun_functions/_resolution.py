"""Resolve declarations from the captured environment before calling main."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any

from pydantic import BaseModel

from ._adapters.ai import AIAdapter, ai_base_url
from ._adapters.credentials import Credential, CredentialsAdapter, read_cloud, read_file
from ._adapters.device import DeviceAdapter
from ._adapters.resources import MailboxAdapter, ProxyAdapter
from ._adapters.tasks import CloudTasks, RunTasks
from ._connection import Connection
from .bindings import AI, Credentials, Mailbox, Proxy, Tasks
from .context import Context, DeviceTarget, RunInfo, is_uuid
from .device import connect_device
from .entry import EntryPoint
from .errors import NotAvailableError

LOCAL_TASKS_REASON = "needs --cloud"


async def call_bound(
    entry: EntryPoint,
    model_input: BaseModel,
    ctx: Context,
    connection: Connection,
    credentials_file: str | None,
) -> Any:
    async with AsyncExitStack() as stack:
        await _resolve(entry, ctx, connection, credentials_file, stack)
        if entry.is_async:
            return await entry.func(model_input, ctx)
        return await asyncio.to_thread(entry.func, model_input, ctx)


async def _resolve(
    entry: EntryPoint,
    ctx: Context,
    connection: Connection,
    credentials_file: str | None,
    stack: AsyncExitStack,
) -> None:
    _check_local(entry, ctx, connection)
    client: Any = None
    device = None
    credential_file: list[Credential] | None = None
    for item in entry.bindings:
        member = item.name
        reading_credentials = item.interface is Credentials
        try:
            remote_resource = item.interface in (Mailbox, Proxy, Tasks)
            remote_credentials = item.interface is Credentials and connection.cloud
            needs_client = client is None and (remote_resource or remote_credentials)
            if needs_client:
                base_url, api_key = connection.require_cloud(member)
                from mobilerun_sdk import AsyncMobilerun

                client = await stack.enter_async_context(
                    AsyncMobilerun(api_key=api_key, base_url=base_url)
                )
            if item.interface is Credentials:
                assert item.options.package is not None
                if connection.cloud:
                    credentials = await read_cloud(client, item.options.package)
                else:
                    if not credentials_file:
                        raise NotAvailableError("credentials", "pass --credentials-file PATH")
                    if credential_file is None:
                        credential_file = await asyncio.to_thread(read_file, credentials_file)
                    credentials = credential_file
                value: Any = CredentialsAdapter(credentials, item.options.package)
            elif item.interface is Mailbox:
                mailbox_id = ctx.run.mailbox_id
                assert mailbox_id is not None
                response = await client.mailboxes.retrieve(mailbox_id)
                value = MailboxAdapter(client, mailbox_id, response.data.address)
            elif item.interface is Proxy:
                proxy = ctx.run.proxy
                device_id = ctx.run.device_id
                assert proxy is not None and device_id is not None
                await client.devices.proxy.connect(device_id, connect={"id": proxy.id})
                value = ProxyAdapter(client, device_id)
            elif item.interface is Tasks:
                continue  # Built below: it needs the device binding.
            elif item.interface is AI:
                base_url, api_key = connection.require_cloud(member)
                value = AIAdapter(ai_base_url(base_url), api_key)
            else:
                core = await _device(ctx, connection)
                if hasattr(core, "close"):
                    stack.callback(core.close)
                device = DeviceAdapter(core, ctx.run.device_id)
                value = device
            setattr(ctx, item.name, value)
        except NotAvailableError as exc:
            reason = str(exc).removeprefix(f"ctx.{exc.member} ")
            raise NotAvailableError(member, reason) from None
        except ImportError:
            raise NotAvailableError(member, "install mobilerun-functions[device]") from None
        except Exception as exc:
            raise NotAvailableError(member, _resolution_reason(exc, reading_credentials)) from None
    for item in entry.bindings:
        if item.interface is Tasks:
            assert device is not None
            setattr(ctx, item.name, _tasks(client, device, ctx.run, connection))


def _check_local(entry: EntryPoint, ctx: Context, connection: Connection) -> None:
    """Refuse a binding the run cannot satisfy before any remote call, whatever the order."""
    run = ctx.run
    for item in entry.bindings:
        name = item.name
        if item.interface is Tasks and not connection.cloud:
            raise NotAvailableError(name, LOCAL_TASKS_REASON)
        if item.interface is Mailbox:
            if run.mailbox_id is None:
                raise NotAvailableError(name, "this run has no mailbox")
            if not is_uuid(run.mailbox_id):
                raise NotAvailableError(name, "has an invalid mailbox id")
        if item.interface is Proxy:
            if run.proxy is None:
                raise NotAvailableError(name, "this run has no proxy")
            if run.proxy.kind != "connect":
                raise NotAvailableError(name, "supports only connect proxies")
            if not is_uuid(run.proxy.id):
                raise NotAvailableError(name, "has an invalid proxy id")
            if not _cloud_device(run, connection):
                raise NotAvailableError(
                    name, "needs a cloud device (use --cloud or --device-target cloud)"
                )
            if not run.device_id:
                raise NotAvailableError(name, "this run has no device")


def _tasks(client: Any, device: DeviceAdapter, run: RunInfo, connection: Connection) -> Any:
    if connection.platform:
        return RunTasks(client, device, run.run_id)
    return CloudTasks(client, device)


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
