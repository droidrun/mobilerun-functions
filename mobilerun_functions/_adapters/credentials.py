"""Credential metadata and direct typing; secret values never cross the interface."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bindings import Device
from ..errors import NotAvailableError


@dataclass(repr=False)
class Credential:
    package: str | None
    name: str
    fields: dict[str, str] = field(repr=False)


class CredentialsAdapter:
    def __init__(self, credentials: list[Credential], package: str) -> None:
        self._credentials = [item for item in credentials if item.package in (None, package)]
        if not self._credentials:
            raise NotAvailableError("credentials", "no credentials for the declared package")

    async def list(self, package: str | None = None) -> list[dict[str, Any]]:
        items = self._credentials
        if package is not None:
            items = [item for item in items if item.package == package]
        return [
            {"package": item.package, "name": item.name, "fields": list(item.fields)}
            for item in items
        ]

    async def type_field(
        self, device: Device, package: str | None, field: str, name: str | None = None
    ) -> None:
        for item in self._credentials:
            matches = (
                (package is None or item.package == package)
                and (name is None or item.name == name)
                and field in item.fields
            )
            if not matches:
                continue
            phone_state = (await device.state.refresh()).phone_state
            focused = phone_state.get("focusedElement")
            has_focused_input = (
                isinstance(focused, dict) and bool(focused)
                and phone_state.get("isEditable") is True
            )
            if not has_focused_input:
                raise NotAvailableError(
                    "credentials",
                    "no text field is focused; tap the field before typing a credential",
                )
            try:
                accepted = await device.input_text(item.fields[field])
            except Exception:
                # Driver exceptions can include the typed text. Do not propagate them.
                raise NotAvailableError("credentials", "device rejected credential input") from None
            if not accepted:
                raise NotAvailableError("credentials", "device rejected credential input")
            return
        raise NotAvailableError("credentials", "credential field not found")


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


async def read_cloud(client: Any, packages: set[str]) -> list[Credential]:
    result: list[Credential] = []
    page = 1
    while True:
        response = await client.credentials.list(page=page, page_size=100)
        result.extend(
            Credential(
                item.package_name, item.credential_name,
                {f.field_type: f.value for f in item.fields},
            )
            for item in response.items
            if item.package_name in packages
        )
        if not response.pagination.has_next:
            return result
        page += 1
