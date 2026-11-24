from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from mobilerun_functions import Context, NotAvailableError
from mobilerun_functions.context import RunInfo
from mobilerun_functions.device import (
    API_BASE_URL_ENV,
    API_KEY_ENV,
    LEGACY_API_BASE_URL_ENV,
    LEGACY_API_KEY_ENV,
)

SECRET = "sk-placeholder-secret-value"
BASE_URL = "https://api.example.test"


def make_ctx(device_id: str | None = "dev-1") -> Context:
    run = RunInfo(run_id="r", attempt=1, deadline=None, mode="live", device_id=device_id)
    return Context(run)


class FakeCore:
    def __init__(self) -> None:
        self.clients: list[dict[str, Any]] = []
        self.connects: list[tuple[str, dict[str, Any]]] = []
        self.device = object()
        core = self

        class Mobilerun:
            def __init__(self, *, bearer: str | None = None, base_url: str | None = None) -> None:
                core.clients.append({"bearer": bearer, "base_url": base_url})

            def connect(self, device_id: str, **kwargs: Any) -> object:
                core.connects.append((device_id, kwargs))
                return core.device

        self.module = types.ModuleType("mobilerun_core")
        self.module.Mobilerun = Mobilerun  # type: ignore[attr-defined]


def focused_device(**methods):
    raw = {"phone_state": {"focusedElement": {"text": ""}, "isEditable": True}}
    ui = types.SimpleNamespace(nodes=[], get=raw.get)
    return types.SimpleNamespace(ui=lambda: ui, screen_size=lambda: (1080, 1920), **methods)


@pytest.fixture
def fake_core(monkeypatch: pytest.MonkeyPatch) -> FakeCore:
    core = FakeCore()
    monkeypatch.setitem(sys.modules, "mobilerun_core", core.module)
    monkeypatch.setenv(API_BASE_URL_ENV, BASE_URL)
    monkeypatch.setenv(API_KEY_ENV, SECRET)
    monkeypatch.delenv(LEGACY_API_BASE_URL_ENV, raising=False)
    monkeypatch.delenv(LEGACY_API_KEY_ENV, raising=False)
    return core


def test_no_device_id_raises(fake_core: FakeCore) -> None:
    with pytest.raises(NotAvailableError, match="no device"):
        make_ctx(None).device()
    assert fake_core.connects == []


@pytest.mark.parametrize("missing", [API_BASE_URL_ENV, API_KEY_ENV])
@pytest.mark.parametrize("empty", [False, True])
def test_missing_env_names_the_variable(
    fake_core: FakeCore, monkeypatch: pytest.MonkeyPatch, missing: str, empty: bool
) -> None:
    if empty:
        monkeypatch.setenv(missing, "")
    else:
        monkeypatch.delenv(missing)
    with pytest.raises(NotAvailableError) as info:
        make_ctx().device()
    assert missing in str(info.value)
    assert SECRET not in str(info.value)
    assert fake_core.connects == []


def test_new_key_name_wins_over_the_legacy_one(
    fake_core: FakeCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_API_KEY_ENV, "legacy-value")
    make_ctx().device()
    assert fake_core.clients == [{"bearer": SECRET, "base_url": BASE_URL}]


@pytest.mark.parametrize("empty", [False, True])
def test_legacy_key_name_alone_works(
    fake_core: FakeCore, monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    if empty:
        monkeypatch.setenv(API_KEY_ENV, "")
    else:
        monkeypatch.delenv(API_KEY_ENV)
    monkeypatch.setenv(LEGACY_API_KEY_ENV, "legacy-value")
    make_ctx().device()
    assert fake_core.clients == [{"bearer": "legacy-value", "base_url": BASE_URL}]


def test_new_base_url_name_wins_over_the_legacy_one(
    fake_core: FakeCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_API_BASE_URL_ENV, "https://legacy.example.test")
    make_ctx().device()
    assert fake_core.clients == [{"bearer": SECRET, "base_url": BASE_URL}]


@pytest.mark.parametrize("empty", [False, True])
def test_legacy_base_url_name_alone_works(
    fake_core: FakeCore, monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    if empty:
        monkeypatch.setenv(API_BASE_URL_ENV, "")
    else:
        monkeypatch.delenv(API_BASE_URL_ENV)
    monkeypatch.setenv(LEGACY_API_BASE_URL_ENV, "https://legacy.example.test")
    make_ctx().device()
    assert fake_core.clients == [{"bearer": SECRET, "base_url": "https://legacy.example.test"}]


def test_missing_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mobilerun_core", None)  # makes the import fail
    monkeypatch.setenv(API_BASE_URL_ENV, BASE_URL)
    monkeypatch.setenv(API_KEY_ENV, SECRET)
    with pytest.raises(NotAvailableError, match="mobilerun-core is not installed") as info:
        make_ctx().device()
    assert SECRET not in str(info.value)


def test_happy_path_returns_library_object(fake_core: FakeCore) -> None:
    device = make_ctx("dev-42").device()
    assert device is fake_core.device
    assert fake_core.clients == [{"bearer": SECRET, "base_url": BASE_URL}]
    assert fake_core.connects == [("dev-42", {"cloud": True})]


def test_connects_once_per_run(fake_core: FakeCore) -> None:
    ctx = make_ctx()
    assert ctx.device() is ctx.device()
    assert len(fake_core.connects) == 1


def test_connect_failure_is_not_cached(fake_core: FakeCore) -> None:
    ctx = make_ctx()
    original = fake_core.module.Mobilerun.connect  # type: ignore[attr-defined]

    def boom(self: Any, device_id: str, **kwargs: Any) -> object:
        raise RuntimeError("down")

    fake_core.module.Mobilerun.connect = boom  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        ctx.device()
    fake_core.module.Mobilerun.connect = original  # type: ignore[attr-defined]
    assert ctx.device() is fake_core.device


def test_other_members_still_unavailable() -> None:
    ctx = make_ctx()
    for name in ("credentials", "files", "llm"):
        with pytest.raises(NotAvailableError):
            getattr(ctx, name)


def local_ctx(device_id: str | None = None, **kw: Any) -> Context:
    run = RunInfo(
        run_id="r", attempt=1, deadline=None, mode="live", device_id=device_id,
        device_target="local", **kw,
    )  # fmt: skip
    return Context(run)


@pytest.fixture
def fake_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mobilerun_core_local", types.ModuleType("x"))


def test_local_adb_serial_has_no_bearer_and_needs_no_cloud_env(
    fake_core: FakeCore, fake_local: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(API_BASE_URL_ENV)
    monkeypatch.delenv(API_KEY_ENV)
    device = local_ctx("emulator-5554").device()
    assert device is fake_core.device
    assert fake_core.clients == [{"bearer": None, "base_url": None}]
    assert fake_core.connects == [("emulator-5554", {"backend": "local-android-adb", "url": None})]


def test_local_android_never_opens_a_cloud_device(fake_core: FakeCore, fake_local: None) -> None:
    uuid = "11111111-2222-3333-4444-555555555555"
    local_ctx(uuid, device_platform="android").device()
    assert fake_core.connects == [(uuid, {"backend": "local-android-adb", "url": None})]
    assert all(c["bearer"] is None for c in fake_core.clients)


def test_local_android_url(fake_core: FakeCore, fake_local: None) -> None:
    local_ctx(None, device_platform="android", device_url="http://h:8080").device()
    assert fake_core.connects == [(None, {"backend": "local-android-http", "url": "http://h:8080"})]


def test_local_ios_url(fake_core: FakeCore, fake_local: None) -> None:
    local_ctx(None, device_platform="ios", device_url="http://10.0.0.5:6643").device()
    assert fake_core.connects == [
        (None, {"backend": "local-ios-http", "url": "http://10.0.0.5:6643"})
    ]


def test_local_without_device_or_url_uses_core_message(
    fake_core: FakeCore, fake_local: None
) -> None:
    def boom(self: Any, device_id: str | None, **kwargs: Any) -> object:
        raise ValueError("local-android-adb connect requires an ADB serial")

    fake_core.module.Mobilerun.connect = boom  # type: ignore[attr-defined]
    with pytest.raises(NotAvailableError, match="requires an ADB serial"):
        local_ctx(None).device()


def test_local_core_value_error_is_one_line(fake_core: FakeCore, fake_local: None) -> None:
    def boom(self: Any, device_id: str | None, **kwargs: Any) -> object:
        raise ValueError("local-android-http connect requires\nurl=...")

    fake_core.module.Mobilerun.connect = boom  # type: ignore[attr-defined]
    with pytest.raises(NotAvailableError) as info:
        local_ctx("x").device()
    assert "\n" not in str(info.value)
    assert "requires url=..." in str(info.value)


@pytest.mark.parametrize("missing", ["mobilerun_core", "mobilerun_core_local"])
def test_local_backend_missing_names_extra(
    fake_core: FakeCore, fake_local: None, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setitem(sys.modules, missing, None)
    with pytest.raises(NotAvailableError, match=r"mobilerun-functions\[local\]"):
        local_ctx("x").device()
    assert fake_core.connects == []


SCRIPT = """
from pydantic import BaseModel
from mobilerun_functions import Context, script

class In(BaseModel):
    pass

@script
def main(input: In, ctx: Context) -> None:
    ctx.device()
"""
CLOUD_UUID = "11111111-2222-3333-4444-555555555555"


def test_executor_argv_is_the_cloud_call(fake_core: FakeCore, run_script: Any) -> None:
    result = run_script(SCRIPT, extra_args=["--device-id", CLOUD_UUID])
    assert result.code == 0 and result.envelope["ok"] is True
    assert fake_core.clients == [{"bearer": SECRET, "base_url": BASE_URL}]
    assert fake_core.connects == [(CLOUD_UUID, {"cloud": True})]
    assert SECRET not in json.dumps(result.envelope) + result.err


def test_cli_local_flags(fake_core: FakeCore, fake_local: None, run_script: Any) -> None:
    result = run_script(
        SCRIPT,
        extra_args=[
            "--device-target", "local", "--device-platform", "ios", "--device-url", "http://h:1",
        ],
    )  # fmt: skip
    assert result.code == 0 and result.envelope["ok"] is True
    assert fake_core.connects == [(None, {"backend": "local-ios-http", "url": "http://h:1"})]
    assert SECRET not in json.dumps(result.envelope) + result.err


@pytest.mark.parametrize("flags", [["--device-url", "http://h:1"], ["--device-platform", "ios"]])
def test_local_only_flags_need_local_target(
    fake_core: FakeCore, run_script: Any, capsys: Any, flags: list[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        run_script(SCRIPT, extra_args=["--device-id", CLOUD_UUID, *flags])
    assert info.value.code == 64  # the parser's usual usage-error exit
    assert "--device-target local" in capsys.readouterr().err
    assert fake_core.connects == []


def test_legacy_device_warns_once_per_context(fake_core, capsys):
    ctx = make_ctx()
    with pytest.warns(DeprecationWarning, match=r"class Ctx\(Context\)") as caught:
        assert ctx.device() is ctx.device() is fake_core.device
    assert len(caught) == 1
    assert capsys.readouterr().err == (
        "[WARNING] ctx.device() is deprecated; declare class Ctx(Context): device: Device "
        "and use await ctx.device.tap(...) instead\n"
    )


@pytest.mark.parametrize("environment,cloud_flags", [("cloud", []), ("local", ["--cloud"])])
def test_binding_cloud_ignores_local_flags_and_import_environment_changes(
    fake_core, fake_local, monkeypatch, run_script, environment, cloud_flags
):
    from test_bindings import source

    monkeypatch.setenv("MOBILERUN_FUNCTIONS_ENV", environment)
    flags = [
        "--device-id", "dev-1", "--device-target", "local", "--device-url", "http://local",
        *cloud_flags,
    ]
    script = source("device: Device", 'await ctx.device.tap(3, 7)', before='''
import os
os.environ["MOBILERUN_FUNCTIONS_ENV"] = "local"
os.environ["MOBILERUN_API_KEY"] = "changed-by-script"
os.environ["MOBILERUN_BASE_URL"] = "https://changed.example.test"
''')
    calls = []
    fake_core.device = types.SimpleNamespace(tap=lambda x, y: calls.append((x, y)))
    result = run_script(script, extra_args=flags)
    assert result.code == 0, result.err
    assert fake_core.connects == [("dev-1", {"cloud": True})]
    assert fake_core.clients == [{"bearer": SECRET, "base_url": BASE_URL}]
    assert calls == [(3, 7)]
    assert SECRET not in result.err + json.dumps(result.envelope)


def test_binding_local_flags_and_file_credentials(fake_core, fake_local, tmp_path, run_script):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('com.example:\n  work:\n    password: "file-secret-value"\n')
    path.chmod(0o600)
    typed = []
    fake_core.device = focused_device(type=lambda text, **kw: typed.append((text, kw)))
    script = source(
        'device: Device\ncredentials: Credentials = binding(package="com.example")',
        'await ctx.credentials.type_field(ctx.device, None, "password", name="work")',
    )
    result = run_script(script, extra_args=[
        "--device-id", "emulator-5554", "--device-target", "local",
        "--credentials-file", str(path),
    ])
    assert result.code == 0, result.err
    assert fake_core.connects == [
        ("emulator-5554", {"backend": "local-android-adb", "url": None})
    ]
    assert fake_core.clients == [{"bearer": None, "base_url": None}]
    assert typed == [("file-secret-value", {"clear": False, "humanize": False, "wpm": None})]
    assert "file-secret-value" not in result.err + json.dumps(result.envelope)


@pytest.mark.parametrize("contents", [
    'com.example:\n  work:\n    password: "file-secret-value"\n',
    'work: "file-secret-value"\n',
])
def test_file_credentials_metadata_never_returns_values(tmp_path, run_script, contents):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text(contents)
    path.chmod(0o600)
    script = source(
        'vault: Credentials = binding(package="com.example")',
        'print(await ctx.vault.list())',
    )
    result = run_script(script, extra_args=["--credentials-file", str(path)])
    assert result.code == 0, result.err
    assert "work" in result.out and "password" in result.out
    assert "file-secret-value" not in result.out + result.err + json.dumps(result.envelope)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_shared_credentials_file_fails_before_main(tmp_path, run_script, mode):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('work: "file-secret-value"\n')
    path.chmod(mode)
    result = run_script(source(
        'vault: Credentials = binding(package="com.example")', 'print("MAIN")',
    ), extra_args=["--credentials-file", str(path)])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.vault credentials file must have mode 600"
    )
    assert result.out == ""
    assert "file-secret-value" not in result.err + json.dumps(result.envelope)


def test_credential_driver_failure_does_not_leak_the_value(
    fake_core, fake_local, tmp_path, run_script
):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('work: "file-secret-value"\n')
    path.chmod(0o600)

    def reject(text, **kwargs):
        raise ValueError(text)

    fake_core.device = focused_device(type=reject)
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'await ctx.vault.type_field(ctx.device, None, "password")',
    ), extra_args=[
        "--device-id", "serial", "--device-target", "local", "--credentials-file", str(path),
    ])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.credentials device rejected credential input"
    )
    assert "file-secret-value" not in result.err + json.dumps(result.envelope)


def test_binding_state_current_requires_refresh(fake_core, run_script):
    from test_bindings import source

    fake_core.device = types.SimpleNamespace()
    result = run_script(
        source("device: Device", "print(ctx.device.state.current)"),
        extra_args=["--device-id", "dev-1"],
    )
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.device.state.current UI state has not been refreshed; "
        "call await device.state.refresh()"
    )


def test_binding_state_refresh_returns_and_caches_six_attributes(fake_core, run_script):
    from test_bindings import source

    raw = {"phone_state": {"focusedElement": {"text": "focused"}}}
    ui = types.SimpleNamespace(nodes=[{"text": "Hello"}], get=raw.get)
    calls = []
    fake_core.device = types.SimpleNamespace(
        ui=lambda: calls.append("ui") or ui,
        screen_size=lambda: (1080, 1920),
    )
    result = run_script(source("device: Device", '''
state = await ctx.device.state.refresh()
print(state is ctx.device.state.current)
print(state.elements, state.formatted_text, state.focused_text, state.phone_state,
      state.screen_width, state.screen_height)
'''.strip().replace("\n", "\n    ")), extra_args=["--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == (
        "True\n[{'text': 'Hello'}] [{\"text\": \"Hello\"}] focused "
        "{'focusedElement': {'text': 'focused'}} 1080 1920\n"
    )
    assert calls == ["ui"]


@pytest.mark.parametrize("expression,method,args,kwargs,returned", [
    ("tap(3, 7)", "tap", (3, 7), {}, None),
    ("swipe(1, 2, 3, 4, duration_ms=750)", "swipe", (1, 2, 3, 4), {"ms": 750}, None),
    ("drag(1, 2, 3, 4, duration=2.5)", "swipe", (1, 2, 3, 4), {"ms": 2500}, None),
    ("input_text('hello', clear=True, stealth=True, wpm=30)", "type", ("hello",),
     {"clear": True, "humanize": True, "wpm": 30}, True),
    ("input_text('hello')", "type", ("hello",),
     {"clear": False, "humanize": False, "wpm": None}, True),
    ("press_button('home')", "key", ("home",), {}, None),
    ("press_key_code(4)", "key", (4,), {}, None),
    ("start_app('com.example', activity='Main')", "start_app", ("com.example",),
     {"activity": "Main"}, ""),
])
def test_binding_device_primitives_use_core_backend(
    fake_core, run_script, expression, method, args, kwargs, returned
):
    from test_bindings import source

    calls = []
    fake_core.device = types.SimpleNamespace(**{
        method: lambda *args, **kwargs: calls.append((args, kwargs)),
    })
    result = run_script(source(
        "device: Device", f"print(repr(await ctx.device.{expression}))",
    ), extra_args=["--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == repr(returned) + "\n"
    assert calls == [(args, kwargs)]


def test_binding_screenshot_decodes_local_base64(fake_core, run_script):
    from test_bindings import source

    calls = []
    fake_core.device = types.SimpleNamespace(
        screenshot=lambda **kwargs: calls.append(kwargs) or "cG5n",
    )
    result = run_script(source("device: Device", "print(await ctx.device.screenshot())"),
                        extra_args=["--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == "b'png'\n"
    assert calls == [{"hide_overlay": True}]


def test_binding_screenshot_reads_cloud_file_bytes(fake_core, tmp_path, run_script):
    from test_bindings import source

    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    fake_core.device = types.SimpleNamespace(screenshot=lambda **kwargs: str(screenshot))
    result = run_script(source("device: Device", "print(await ctx.device.screenshot(False))"),
                        extra_args=["--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == "b'png'\n"
    assert not screenshot.exists()


def test_sync_bound_context_can_run_its_own_event_loop(tmp_path, run_script):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('work: "file-secret-value"\n')
    path.chmod(0o600)
    script = source(
        'vault: Credentials = binding(package="com.example")',
        'print(asyncio.run(ctx.vault.list()))',
        before="\nimport asyncio\n",
    ).replace("async def main", "def main")
    result = run_script(script, extra_args=["--credentials-file", str(path)])
    assert result.code == 0, result.err
    assert result.out == "[{'package': None, 'name': 'work', 'fields': ['password']}]\n"


@pytest.mark.parametrize("phone_state", [
    {"focusedElement": {}, "isEditable": True},
    {"focusedElement": {"text": "search"}, "isEditable": False},
    {"focusedElement": None, "isEditable": True},
    {"focusedElement": "not-a-field", "isEditable": True},
])
def test_credentials_refuse_unfocused_input(
    fake_core, fake_local, tmp_path, run_script, phone_state
):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('work: "file-secret-value"\n')
    path.chmod(0o600)
    typed = []
    raw = {"phone_state": phone_state}
    ui = types.SimpleNamespace(nodes=[], get=raw.get)
    fake_core.device = types.SimpleNamespace(
        ui=lambda: ui, screen_size=lambda: (1080, 1920),
        type=lambda text, **kwargs: typed.append(text),
    )
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'await ctx.vault.type_field(ctx.device, None, "password")',
    ), extra_args=[
        "--device-target", "local", "--device-id", "serial", "--credentials-file", str(path),
    ])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.credentials no text field is focused; "
        "tap the field before typing a credential"
    )
    assert typed == []
    assert "file-secret-value" not in result.err + json.dumps(result.envelope)


def test_missing_yaml_reports_install_extra(tmp_path, monkeypatch, run_script):
    from test_bindings import source

    path = tmp_path / "credentials.yaml"
    path.write_text('work: "file-secret-value"\n')
    path.chmod(0o600)
    monkeypatch.setitem(sys.modules, "yaml", None)
    result = run_script(source('vault: Credentials = binding(package="com.example")'),
                        extra_args=["--credentials-file", str(path)])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.vault install mobilerun-functions[device]"
    )
