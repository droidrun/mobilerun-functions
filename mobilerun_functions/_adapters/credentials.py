"""Credential metadata and direct typing; secret values never cross the interface."""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bindings import Device
from ..errors import NotAvailableError

# The core reads the foreground app (Android package, iOS bundle id) from these keys.
FOREGROUND_KEYS = ("app_id", "package_name", "bundle_id", "packageName")
# SDK debug logs include request bodies; the typed value must never reach them.
SDK_LOGGERS = ("mobilerun_sdk", "httpx", "httpcore")


@dataclass(repr=False)
class Credential:
    package: str | None
    name: str
    fields: dict[str, str] = field(repr=False)


class CredentialsAdapter:
    """One declared package; entries of other packages never reach it."""

    def __init__(self, credentials: list[Credential], package: str) -> None:
        self._package = package
        self.run_device: Device | None = None  # Set once the run's device is bound.
        self._credentials = [item for item in credentials if item.package in (None, package)]
        if not self._credentials:
            raise NotAvailableError("credentials", "no credentials for the declared package")

    async def list(self) -> list[dict[str, Any]]:
        return [
            {"package": self._package, "name": item.name, "fields": list(item.fields)}
            for item in self._credentials
        ]

    async def type_field(self, device: Device, field: str, *, name: str | None = None) -> None:
        if device is not self.run_device:
            raise NotAvailableError("credentials", "device is not the run's device")
        for item in self._credentials:
            matches = (name is None or item.name == name) and field in item.fields
            if not matches:
                continue
            phone_state = (await device.state.refresh()).phone_state
            self._require_foreground_package(phone_state)
            focused = phone_state.get("focusedElement")
            if not (isinstance(focused, dict) and phone_state.get("isEditable") is True):
                raise NotAvailableError(
                    "credentials",
                    "no text field is focused; tap the field before typing a credential",
                )
            try:
                with _sdk_logs_at_least_info():
                    accepted = await device.input_text(item.fields[field])
            except Exception:
                # Driver exceptions can include the typed text. Do not propagate them.
                raise NotAvailableError("credentials", "device rejected credential input") from None
            if not accepted:
                raise NotAvailableError("credentials", "device rejected credential input")
            return
        raise NotAvailableError("credentials", "credential field not found")

    def _require_foreground_package(self, phone_state: dict[str, Any]) -> None:
        foreground = next(
            (phone_state[key] for key in FOREGROUND_KEYS if isinstance(phone_state.get(key), str)),
            None,
        )
        if foreground is None:
            raise NotAvailableError(
                "credentials", "the foreground app is unknown; credentials are not typed"
            )
        if foreground != self._package:
            raise NotAvailableError(
                "credentials", "the bound package is not in the foreground; open it first"
            )


@contextmanager
def _sdk_logs_at_least_info() -> Iterator[None]:
    saved = [(logging.getLogger(name), logging.getLogger(name).level) for name in SDK_LOGGERS]
    try:
        for logger, _ in saved:
            logger.setLevel(max(logger.getEffectiveLevel(), logging.INFO))
        yield
    finally:
        for logger, level in saved:
            logger.setLevel(level)


def read_file(path: str) -> list[Credential]:
    import yaml

    try:
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) & 0o077:
                raise NotAvailableError("credentials", "credentials file must have mode 600")
            data = yaml.safe_load(stream) or {}
        return _normalize(data)
    except NotAvailableError:
        raise
    except Exception:
        raise NotAvailableError("credentials", "cannot read credentials file") from None


def _normalize(data: Any) -> list[Credential]:
    if not isinstance(data, dict):
        raise NotAvailableError("credentials", "credentials file must contain a mapping")
    result: list[Credential] = []
    for package, names in data.items():
        if isinstance(names, str):
            result.append(Credential(None, package, {"password": names}))
            continue
        if not isinstance(names, dict):
            raise NotAvailableError("credentials", "invalid credentials file format")
        for name, fields in names.items():
            valid = isinstance(fields, dict) and all(isinstance(v, str) for v in fields.values())
            if not valid:
                raise NotAvailableError("credentials", "invalid credentials file format")
            result.append(Credential(package, name, fields))
    return result


def _stored_package_name(name: str) -> str:
    # credentials-api stores package names as `name.trim().replace(/\./g, '-')`.
    return name.strip().replace(".", "-")


async def read_cloud(client: Any, package: str) -> list[Credential]:
    """Read one declared package; an org-wide list would put other packages' values in the run."""
    response = await client.credentials.packages.list(package)
    stored = _stored_package_name(package)
    return [
        Credential(
            package, item.credential_name,
            {f.field_type: f.value for f in item.fields},
        )
        for item in response.data
        if item.package_name == stored
    ]
