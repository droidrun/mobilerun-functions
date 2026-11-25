"""Validate Context declarations once, for both inspect and run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_type_hints

from .bindings import AI, Binding, Credentials, Device, Mailbox, Proxy, Tasks
from .context import _BINDING_OVERRIDES, Context, RunInfo
from .errors import EntryPointError

_RESERVED_CONTEXT_MEMBERS = (
    vars(Context).keys() | vars(Context(RunInfo())).keys()
) - _BINDING_OVERRIDES

INTERFACES = (Device, Credentials, Mailbox, Proxy, Tasks, AI)
_IMPLEMENTATION_HINT = (
    "the implementation belongs in the local configuration; "
    "declare the interface type with binding(...)"
)


@dataclass(frozen=True)
class Declaration:
    name: str
    interface: type
    options: Binding


def declarations(ctx: type[Context]) -> tuple[Declaration, ...]:
    if ctx is Context:
        return ()
    try:
        hints = get_type_hints(ctx)
    except Exception:
        raise EntryPointError("ctx: cannot resolve binding annotations") from None
    result = []
    seen: set[type] = set()
    packages: set[str] = set()
    for name, interface in hints.items():
        reserved = name.startswith("_") or name in _RESERVED_CONTEXT_MEMBERS
        if reserved:
            raise EntryPointError(f"{name}: reserved Context member; choose another name")
        default = getattr(ctx, name, Binding())
        valid = interface in INTERFACES and isinstance(default, Binding)
        if not valid:
            raise EntryPointError(f"{name}: {_IMPLEMENTATION_HINT}")
        duplicate_resource = interface in seen and interface is not Credentials
        if duplicate_resource:
            raise EntryPointError(f"{name}: at most one {interface.__name__} binding is allowed")
        _validate_options(name, interface, default)
        if interface is Credentials:
            assert default.package is not None
            if default.package in packages:
                raise EntryPointError(f"{name}: credential packages must be distinct")
            packages.add(default.package)
        seen.add(interface)
        result.append(Declaration(name, interface, default))
    missing_tasks_device = Tasks in seen and Device not in seen
    if missing_tasks_device:
        raise EntryPointError("tasks: requires a Device binding")
    missing_proxy_device = Proxy in seen and Device not in seen
    if missing_proxy_device:
        raise EntryPointError("proxy: requires a Device binding")
    return tuple(result)


def _validate_options(name: str, interface: type, options: Binding) -> None:
    if interface is Credentials:
        if not (isinstance(options.package, str) and options.package):
            raise EntryPointError(f'{name}: Credentials requires binding(package="...")')
        return
    if options.package is not None:
        raise EntryPointError(f"{name}: {interface.__name__} accepts only binding()")


def requirements(items: tuple[Declaration, ...]) -> dict[str, Any]:
    interfaces = {item.interface for item in items}
    return {
        "device": Device in interfaces,
        "credentials": sorted({
            item.options.package for item in items if item.interface is Credentials
        }),
        "mailbox": Mailbox in interfaces,
        "proxy": Proxy in interfaces,
        "tasks": Tasks in interfaces,
        "ai": AI in interfaces,
    }
