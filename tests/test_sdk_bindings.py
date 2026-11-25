"""Real generated SDK serialization against recorded response shapes, without network."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from mobilerun_sdk import AsyncMobilerun as SDKClient
from test_bindings import source
from test_device import FakeCore, focused_device


@pytest.fixture
def sdk(monkeypatch):
    requests = []
    responses = deque()

    async def handle(request):
        requests.append(request)
        status, payload = responses.popleft()
        return httpx.Response(status, json=payload)

    def client(**kwargs):
        return SDKClient(
            **kwargs, max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr("mobilerun_sdk.AsyncMobilerun", client)
    monkeypatch.setenv("MOBILERUN_API_KEY", "test-key")
    monkeypatch.setenv("MOBILERUN_BASE_URL", "https://api.example.test/v1")
    monkeypatch.delenv("MOBILERUN_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("MOBILERUN_API_BASE_URL", raising=False)
    monkeypatch.delenv("MOBILERUN_FUNCTIONS_ENV", raising=False)
    return responses, requests


@pytest.fixture
def core(monkeypatch):
    core = FakeCore()
    core.device = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "mobilerun_core", core.module)
    return core


MAILBOX_ID = "0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8"
PROXY_ID = "7a6b5c4d-3e2f-4a10-9b8c-7d6e5f4a3b2c"
MAILBOX_ARGS = ["--mailbox-id", MAILBOX_ID]
PROXY_ARGS = ["--proxy-kind", "connect", "--proxy-id", PROXY_ID]


def package_list(items):
    return {"data": items}


def credential(package="com.example", name="work", value="vault-secret"):
    return {
        "createdBy": None, "credentialName": name, "fields": [
            {"fieldType": "password", "value": value},
        ],
        "ownerId": "org-1", "packageName": package, "secretPath": "vault/reference",
    }


def test_cloud_credentials_read_only_the_declared_packages(sdk, run_script):
    responses, requests = sdk
    responses.extend([
        (200, package_list([credential("com.example")])),
        (200, package_list([credential("com.second", "other", "second-secret")])),
    ])
    result = run_script(source(
        'vault: Credentials = binding(package="com.example")\n'
        'second: Credentials = binding(package="com.second")',
        'print(await ctx.vault.list())\n    print(await ctx.second.list())',
    ), extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert result.out.splitlines() == [
        "[{'package': 'com.example', 'name': 'work', 'fields': ['password']}]",
        "[{'package': 'com.second', 'name': 'other', 'fields': ['password']}]",
    ]
    assert [request.url.path for request in requests] == [
        "/v1/credentials/packages/com.example", "/v1/credentials/packages/com.second",
    ]
    assert requests[0].headers["authorization"] == "Bearer test-key"
    secrets = "vault-secret second-secret"
    assert not any(word in result.out + result.err + json.dumps(result.envelope)
                   for word in secrets.split())
    assert not responses


def test_cloud_credentials_never_use_the_org_wide_list(sdk, run_script):
    responses, requests = sdk
    responses.append((200, package_list([credential()])))
    result = run_script(source(
        'vault: Credentials = binding(package="com.example")', 'print("MAIN")',
    ), extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/credentials/packages/com.example"),
    ]


def test_cloud_credentials_drop_entries_of_an_undeclared_package(sdk, run_script):
    responses, _ = sdk
    responses.append((200, package_list([
        credential("com.undeclared", "leak", "undeclared-secret"), credential(),
    ])))
    result = run_script(source(
        'vault: Credentials = binding(package="com.example")',
        'print(await ctx.vault.list())',
    ), extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert result.out == "[{'package': 'com.example', 'name': 'work', 'fields': ['password']}]\n"
    assert "undeclared-secret" not in result.out + result.err + json.dumps(result.envelope)


def test_read_cloud_keeps_only_the_declared_package_in_memory():
    from mobilerun_functions._adapters.credentials import read_cloud

    async def handle(request):
        return httpx.Response(200, json=package_list([
            credential("com.undeclared", "leak", "undeclared-secret"), credential(),
        ]))

    client = SDKClient(
        api_key="test-key", base_url="https://api.example.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    items = asyncio.run(read_cloud(client, "com.example"))
    assert [(item.package, item.name) for item in items] == [("com.example", "work")]


def test_cloud_credential_typing_never_returns_a_secret(sdk, core, run_script):
    responses, requests = sdk
    responses.append((200, package_list([credential()])))
    typed = []
    core.device = focused_device(type=lambda text, **kw: typed.append(text))
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'print(await ctx.vault.type_field(ctx.device, "password"))',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert typed == ["vault-secret"]
    assert result.out == "None\n"
    assert "vault-secret" not in result.out + result.err + json.dumps(result.envelope)
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/credentials/packages/com.example"),
    ]


def test_bound_type_field_refuses_an_unknown_field_without_typing(sdk, core, run_script):
    responses, _ = sdk
    responses.append((200, package_list([credential()])))
    typed = []
    core.device = focused_device(type=lambda text, **kw: typed.append(text))
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'await ctx.vault.type_field(ctx.device, "username")',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.credentials credential field not found"
    )
    assert typed == []
    assert "vault-secret" not in result.err + json.dumps(result.envelope)


def test_bound_type_field_refuses_an_unknown_name_without_typing(sdk, core, run_script):
    responses, _ = sdk
    responses.append((200, package_list([credential()])))
    typed = []
    core.device = focused_device(type=lambda text, **kw: typed.append(text))
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'await ctx.vault.type_field(ctx.device, "password", name="absent")',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.credentials credential field not found"
    )
    assert typed == []


def test_bound_type_field_has_no_package_argument(sdk, core, run_script):
    responses, _ = sdk
    responses.append((200, package_list([credential()])))
    core.device = focused_device(type=lambda text, **kw: None)
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'await ctx.vault.type_field(ctx.device, "com.example", "password")',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 1
    assert "TypeError" in result.envelope["error"]["message"]


def test_local_mailbox_reads_address_and_waits_for_otp(sdk, run_script):
    responses, requests = sdk
    responses.extend([
        (200, {"data": {"address": "example@example.test"}}),
        (200, {"data": {
            "code": "001234", "confidence": "high", "messageId": "message-1",
            "receivedAt": "2026-11-24T12:00:00Z",
        }}),
    ])
    result = run_script(source(
        'email: Mailbox',
        'print(ctx.email.address, await ctx.email.wait_for_code(sender="sender@example.test"))',
    ), extra_args=MAILBOX_ARGS)
    assert result.code == 0, result.err
    assert result.out == "example@example.test 001234\n"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", f"/v1/mailboxes/{MAILBOX_ID}"), ("GET", f"/v1/mailboxes/{MAILBOX_ID}/otp"),
    ]
    assert requests[1].url.params["sender"] == "sender@example.test"
    baseline = datetime.fromisoformat(requests[1].url.params["after"])
    assert baseline.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - baseline) < timedelta(minutes=1)


def test_local_mailbox_without_key_fails_before_main(sdk, monkeypatch, run_script):
    _, requests = sdk
    monkeypatch.delenv("MOBILERUN_API_KEY")
    monkeypatch.setitem(sys.modules, "mobilerun_sdk", None)
    result = run_script(source('email: Mailbox', 'print("MAIN")'), extra_args=MAILBOX_ARGS)
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.email set MOBILERUN_API_KEY"
    )
    assert result.out == ""
    assert requests == []


def test_proxy_connects_existing_id_only_and_leaves_it_connected(sdk, core, run_script):
    responses, requests = sdk
    responses.extend([(204, None), (200, {"connected": True})])
    result = run_script(source(
        'device: Device\nproxy: Proxy',
        'print(await ctx.proxy.status())',
    ), extra_args=["--cloud", "--device-id", "dev-1", *PROXY_ARGS])
    assert result.code == 0, result.err
    assert result.out == (
        "{'connected': True, 'name': None, 'protocol': None, '$schema': None}\n"
    )
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/devices/dev-1/proxy"), ("GET", "/v1/devices/dev-1/proxy"),
    ]
    assert json.loads(requests[0].content) == {"connect": {"id": PROXY_ID}}
    assert not responses


def test_cloud_flag_defaults_to_public_base_and_legacy_key(sdk, monkeypatch, run_script):
    responses, requests = sdk
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    monkeypatch.delenv("MOBILERUN_API_KEY")
    monkeypatch.setenv("MOBILERUN_CLOUD_API_KEY", "legacy-test-key")
    responses.append((200, {"data": {"address": None}}))
    result = run_script(source('email: Mailbox'), extra_args=["--cloud", *MAILBOX_ARGS])
    assert result.code == 0, result.err
    assert str(requests[0].url) == f"https://api.mobilerun.ai/v1/mailboxes/{MAILBOX_ID}"
    assert requests[0].headers["authorization"] == "Bearer legacy-test-key"


def test_tasks_run_and_poll_structured_output(sdk, core, run_script):
    responses, requests = sdk
    responses.extend([
        (200, {"id": "task-1"}),
        (200, {
            "status": "completed", "terminal": True, "succeeded": True,
            "message": "done", "output": {"answer": "ready"}, "steps": 4,
        }),
    ])
    result = run_script(source(
        "device: Device\ntasks: Tasks",
        'run = await ctx.tasks.run("read screen", device=ctx.device, output=Answer, max_steps=8)\n'
        '    print(run.model_dump_json())',
        before='\nclass Answer(BaseModel):\n    answer: str\n',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert json.loads(result.out) == {
        "success": True, "answer": "done", "output": {"answer": "ready"}, "steps": 4,
        "task_id": "task-1", "trajectory_path": None, "device": "CloudDevice",
    }
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/tasks"), ("GET", "/v1/tasks/task-1/status"),
    ]
    body = json.loads(requests[0].content)
    assert body["deviceId"] == "dev-1" and body["task"] == "read screen" and body["maxSteps"] == 8
    assert body["outputSchema"]["properties"]["answer"]["type"] == "string"


def test_invalid_task_output_marks_execution_failed(sdk, core, run_script):
    responses, _ = sdk
    responses.extend([
        (200, {"id": "task-1"}),
        (200, {
            "status": "completed", "terminal": True, "succeeded": True,
            "output": {"passed": "invalid", "reason": "reason"},
        }),
    ])
    result = run_script(source(
        "device: Device\ntasks: Tasks",
        'run = await ctx.tasks.run("ready", device=ctx.device, output=Verdict)\n'
        '    print(run.model_dump_json())',
        before="\nclass Verdict(BaseModel):\n    passed: bool\n",
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    execution = json.loads(result.out)
    assert execution["success"] is False and execution["output"] is None
    assert execution["answer"] == "task output does not match the output schema"


def run_device(device_id="dev-1"):
    from mobilerun_functions._adapters.device import DeviceAdapter

    return DeviceAdapter(object(), device_id)


def test_task_deadline_interrupts_polling_and_stops_existing_task():
    from mobilerun_functions._adapters.tasks import CloudTasks

    stopped = []
    polling = []

    async def exercise():
        blocked = asyncio.Event()

        async def run(**kwargs):
            return types.SimpleNamespace(id="task-1")

        async def status(task_id):
            polling.append(task_id)
            await blocked.wait()

        async def stop(task_id):
            stopped.append(task_id)

        device = run_device()
        tasks = CloudTasks(types.SimpleNamespace(tasks=types.SimpleNamespace(
            run=run, get_status=status, stop=stop,
        )), device)
        with pytest.raises(TimeoutError):
            await tasks.run("read screen", device=device, timeout=0.01)

    asyncio.run(exercise())
    assert polling == ["task-1"]
    assert stopped == ["task-1"]


def test_mailbox_polls_after_no_code_and_forwards_after(sdk, monkeypatch, run_script):
    from unittest.mock import AsyncMock

    responses, requests = sdk
    responses.extend([
        (200, {"data": {"address": None}}),
        (204, None),
        (200, {"data": {
            "code": "0007", "confidence": "high", "messageId": "message-1",
            "receivedAt": "2026-11-24T12:00:00Z",
        }}),
    ])
    pause = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", pause)
    result = run_script(source(
        'email: Mailbox',
        'print(await ctx.email.wait_for_code(after=datetime(2026, 11, 24, tzinfo=UTC)))',
        before="\nfrom datetime import UTC, datetime\n",
    ), extra_args=MAILBOX_ARGS)
    assert result.code == 0, result.err
    assert result.out == "0007\n"
    assert [request.url.path for request in requests] == [
        f"/v1/mailboxes/{MAILBOX_ID}",
        f"/v1/mailboxes/{MAILBOX_ID}/otp",
        f"/v1/mailboxes/{MAILBOX_ID}/otp",
    ]
    assert requests[2].url.params["after"] == "2026-11-24T00:00:00+00:00"
    pause.assert_awaited_once_with(2.0)


OTP_OK = {"data": {
    "code": "0042", "confidence": "high", "messageId": "message-1",
    "receivedAt": "2026-11-25T12:00:00Z",
}}
WAIT_BEGAN = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def mailbox_with(statuses, clock=lambda: WAIT_BEGAN):
    """A MailboxAdapter on the real SDK; `statuses` is the scripted OTP response sequence."""
    from mobilerun_functions._adapters.resources import MailboxAdapter

    requests = []
    script = deque(statuses)

    async def handle(request):
        requests.append(request)
        status = script.popleft()
        return httpx.Response(status, json=OTP_OK if status == 200 else {"error": "x"})

    client = SDKClient(
        api_key="test-key", base_url="https://api.example.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    return MailboxAdapter(client, "mailbox-1", None, clock=clock), requests, script


@pytest.fixture
def pause(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return sleep


def test_mailbox_wait_sends_one_aware_baseline_on_every_poll(pause):
    ticks = iter([WAIT_BEGAN, WAIT_BEGAN + timedelta(minutes=5)])
    mailbox, requests, _ = mailbox_with([204, 204, 200], clock=lambda: next(ticks))
    assert asyncio.run(mailbox.wait_for_code()) == "0042"
    baselines = [request.url.params["after"] for request in requests]
    assert baselines == ["2026-11-25T12:00:00+00:00"] * 3
    assert "sender" not in requests[0].url.params
    assert pause.await_count == 2


def test_mailbox_wait_forwards_sender_and_after_exactly(pause):
    after = datetime(2026, 11, 25, 9, 30, tzinfo=UTC)
    mailbox, requests, _ = mailbox_with([200])
    asyncio.run(mailbox.wait_for_code(sender="no-reply@example.test", after=after))
    assert requests[0].url.params["sender"] == "no-reply@example.test"
    assert requests[0].url.params["after"] == "2026-11-25T09:30:00+00:00"


def test_mailbox_default_clock_is_aware_utc():
    from mobilerun_functions._adapters.resources import _utc_now

    assert _utc_now().utcoffset() == timedelta(0)


@pytest.mark.parametrize("status,error", [
    (404, "NotFoundError"), (409, "ConflictError"), (401, "AuthenticationError"),
    (403, "PermissionDeniedError"),
])
def test_mailbox_wait_fails_immediately_on_error_status(pause, status, error):
    import mobilerun_sdk

    mailbox, requests, script = mailbox_with([status, 200])
    with pytest.raises(getattr(mobilerun_sdk, error)):
        asyncio.run(mailbox.wait_for_code())
    assert len(requests) == 1 and list(script) == [200]
    pause.assert_not_awaited()


def test_mailbox_wait_stops_polling_at_the_deadline(monkeypatch):
    monkeypatch.setattr("mobilerun_functions._adapters.resources.POLL_INTERVAL", 0.005)
    mailbox, requests, _ = mailbox_with([204] * 10_000)
    with pytest.raises(TimeoutError):
        asyncio.run(mailbox.wait_for_code(timeout=0.05))
    polled = len(requests)
    assert 1 < polled < 10_000


def test_mailbox_wait_returns_a_code_that_arrives_before_the_deadline(monkeypatch):
    monkeypatch.setattr("mobilerun_functions._adapters.resources.POLL_INTERVAL", 0.005)
    mailbox, requests, _ = mailbox_with([204, 204, 200])
    assert asyncio.run(mailbox.wait_for_code(timeout=5)) == "0042"
    assert len(requests) == 3


def test_mailbox_wait_stops_polling_when_cancelled(monkeypatch):
    monkeypatch.setattr("mobilerun_functions._adapters.resources.POLL_INTERVAL", 0.005)
    mailbox, requests, _ = mailbox_with([204] * 10_000)

    async def exercise():
        waiting = asyncio.create_task(mailbox.wait_for_code(timeout=60))
        while not requests:
            await asyncio.sleep(0.001)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        stopped = len(requests)
        await asyncio.sleep(0.05)
        return stopped, len(requests)

    stopped, later = asyncio.run(exercise())
    assert stopped == later


def test_task_polls_through_running_until_terminal(sdk, core, monkeypatch, run_script):
    from unittest.mock import AsyncMock

    responses, requests = sdk
    responses.extend([
        (200, {"id": "task-1"}),
        (200, {"status": "running", "terminal": False}),
        (200, {"status": "completed", "terminal": True, "succeeded": True}),
    ])
    pause = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", pause)
    result = run_script(source(
        "device: Device\ntasks: Tasks",
        'print((await ctx.tasks.run("read screen", device=ctx.device)).success)',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == "True\n"
    assert [request.url.path for request in requests] == [
        "/v1/tasks", "/v1/tasks/task-1/status", "/v1/tasks/task-1/status",
    ]
    pause.assert_awaited_once_with(2.0)


@pytest.mark.parametrize("status", [401, 404])
def test_binding_api_status_error_names_field_without_body(sdk, run_script, status):
    responses, requests = sdk
    responses.append((status, {"detail": "vault-secret"}))
    result = run_script(source('inbox: Mailbox', 'print("MAIN")'), extra_args=MAILBOX_ARGS)
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        f"NotAvailableError: ctx.inbox API returned {status}"
    )
    assert result.out == ""
    assert len(requests) == 1
    assert "vault-secret" not in result.err + json.dumps(result.envelope)


def test_missing_sdk_names_binding_and_install_extra(sdk, monkeypatch, run_script):
    _, requests = sdk
    monkeypatch.setitem(sys.modules, "mobilerun_sdk", None)
    result = run_script(source('inbox: Mailbox'), extra_args=MAILBOX_ARGS)
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.inbox install mobilerun-functions[device]"
    )
    assert requests == []


def test_local_proxy_fails_before_building_any_client(sdk, monkeypatch, run_script):
    _, requests = sdk
    built = []
    monkeypatch.setattr("mobilerun_sdk.AsyncMobilerun", lambda **kw: built.append(kw))
    result = run_script(source(
        'device: Device\nemail: Mailbox\n'
        'proxy: Proxy',
        'print("MAIN")',
    ), extra_args=[
        "--device-target", "local", "--device-id", "emulator-5554", *MAILBOX_ARGS, *PROXY_ARGS,
    ])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.proxy needs a cloud device (use --cloud or --device-target cloud)"
    )
    assert built == [] and requests == []
    assert result.out == ""


def test_local_mailbox_defaults_to_public_api(sdk, monkeypatch, run_script):
    responses, requests = sdk
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    responses.append((200, {"data": {"address": None}}))
    result = run_script(source('email: Mailbox'), extra_args=MAILBOX_ARGS)
    assert result.code == 0, result.err
    assert str(requests[0].url) == f"https://api.mobilerun.ai/v1/mailboxes/{MAILBOX_ID}"


def test_task_deadline_starts_after_creation_returns():
    from mobilerun_functions._adapters.tasks import CloudTasks

    creation_options = []

    async def exercise():
        created = asyncio.Event()
        asyncio.get_running_loop().call_later(0.02, created.set)

        async def run(**kwargs):
            creation_options.append(kwargs)
            await created.wait()
            return types.SimpleNamespace(id="task-1")

        async def status(task_id):
            return types.SimpleNamespace(
                terminal=True, succeeded=True, message="done", output=None, steps=1,
            )

        client = types.SimpleNamespace(tasks=types.SimpleNamespace(run=run, get_status=status))
        device = run_device()
        return await CloudTasks(client, device).run("read screen", device=device, timeout=0.001)

    result = asyncio.run(exercise())
    assert result.success is True and result.task_id == "task-1"
    assert creation_options == [{"device_id": "dev-1", "task": "read screen", "timeout": 30.0}]


def test_credential_read_failure_remains_opaque_and_names_binding(sdk, run_script):
    responses, _ = sdk
    payload = credential()
    payload["fields"] = None
    payload["sensitive"] = "vault-secret"
    responses.append((200, package_list([payload])))
    result = run_script(source('vault: Credentials = binding(package="com.example")'),
                        extra_args=["--cloud"])
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.vault binding could not be resolved"
    )
    assert "vault-secret" not in result.err + json.dumps(result.envelope)


# run-scoped tasks (platform)


def run_task(status="pending", **fields):
    return {"data": {
        "id": "run-task-1", "runId": "run-1", "status": status, "taskId": None,
        "succeeded": None, "message": None, "output": None, "steps": None, **fields,
    }}


@pytest.fixture
def platform(sdk, core, monkeypatch):
    monkeypatch.setenv("MOBILERUN_FUNCTIONS_ENV", "cloud")
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    return sdk


PLATFORM_ARGS = ["--run-id", "run-1", "--device-id", "dev-1"]


def tasks_script(call_args=""):
    extra = f", {call_args}" if call_args else ""
    return source(
        "device: Device\ntasks: Tasks",
        f'print((await ctx.tasks.run("read screen", device=ctx.device{extra})).model_dump_json())',
        before="\nclass Answer(BaseModel):\n    answer: str\n",
    )


def test_platform_tasks_go_through_the_run_routes_and_poll(platform, run_script):
    responses, requests = platform
    responses.extend([
        (202, run_task()),
        (200, run_task("running")),
        (200, run_task(
            "completed", succeeded=True, message="done", output={"answer": "ready"}, steps=4,
            taskId="task-9",
        )),
    ])
    result = run_script(tasks_script("output=Answer, max_steps=8"), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    assert json.loads(result.out) == {
        "success": True, "answer": "done", "output": {"answer": "ready"}, "steps": 4,
        "task_id": "task-9", "trajectory_path": None, "device": "CloudDevice",
    }
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/compute/runs/run-1/tasks"),
        ("GET", "/v1/compute/runs/run-1/tasks/run-task-1"),
        ("GET", "/v1/compute/runs/run-1/tasks/run-task-1"),
    ]
    assert requests[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(requests[0].content)
    assert body["outputSchema"]["properties"]["answer"]["type"] == "string"
    assert {key: body[key] for key in ("task", "deviceId", "maxSteps")} == {
        "task": "read screen", "deviceId": "dev-1", "maxSteps": 8,
    }
    assert set(body) == {"task", "deviceId", "outputSchema", "maxSteps"}


def test_platform_task_without_a_started_task_keeps_the_run_task_id(platform, run_script):
    responses, _ = platform
    lost = run_task("failed", succeeded=False, message="lost")
    responses.extend([(202, run_task()), (200, lost)])
    result = run_script(tasks_script(), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    assert json.loads(result.out)["task_id"] == "run-task-1"


def test_platform_task_key_is_new_per_call(platform, run_script):
    responses, requests = platform
    finished = run_task("completed", succeeded=True)
    responses.extend([(202, run_task()), (200, finished), (202, run_task()), (200, finished)])
    result = run_script(source(
        "device: Device\ntasks: Tasks",
        'await ctx.tasks.run("first", device=ctx.device)\n'
        '    await ctx.tasks.run("second", device=ctx.device)',
    ), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    keys = [r.headers["idempotency-key"] for r in requests if r.method == "POST"]
    assert len(keys) == 2 and keys[0] != keys[1]
    assert all(uuid.UUID(key).version == 4 for key in keys)
    assert all("idempotency-key" not in r.headers for r in requests if r.method == "GET")


@pytest.mark.parametrize("timeout,expected", [(0.2, 1), (10.2, 11), (3000, 2700)])
def test_platform_task_timeout_is_whole_seconds_within_the_route_limits(
    platform, run_script, timeout, expected
):
    responses, requests = platform
    responses.extend([(202, run_task()), (200, run_task("completed", succeeded=True))])
    result = run_script(tasks_script(f"timeout={timeout}"), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    assert json.loads(requests[0].content)["timeoutS"] == expected


def test_platform_task_timeout_of_zero_is_sent_as_one_second(platform, run_script):
    responses, requests = platform
    responses.extend([(202, run_task()), (200, run_task("completed", succeeded=True))])
    result = run_script(tasks_script("timeout=0"), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    assert json.loads(requests[0].content)["timeoutS"] == 1


def test_platform_task_without_timeout_sends_no_limit(platform, run_script):
    responses, requests = platform
    responses.extend([(202, run_task()), (200, run_task("completed", succeeded=True))])
    result = run_script(tasks_script(), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    assert "timeoutS" not in json.loads(requests[0].content)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_platform_task_ends_on_every_terminal_status(platform, run_script, status):
    responses, requests = platform
    responses.extend([
        (202, run_task()),
        (200, run_task(status, succeeded=False, message="stopped")),
    ])
    result = run_script(tasks_script(), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    execution = json.loads(result.out)
    assert execution["success"] is False and execution["answer"] == "stopped"
    assert len(requests) == 2


def test_platform_invalid_task_output_marks_execution_failed(platform, run_script):
    responses, _ = platform
    responses.extend([
        (202, run_task()),
        (200, run_task("completed", succeeded=True, output={"answer": 1})),
    ])
    result = run_script(tasks_script("output=Answer"), extra_args=PLATFORM_ARGS)
    assert result.code == 0, result.err
    execution = json.loads(result.out)
    assert execution["success"] is False and execution["output"] is None
    assert execution["answer"] == "task output does not match the output schema"


@pytest.mark.parametrize("code,status", [
    ("device_lease_not_held", 409), ("tasks_not_declared", 403), ("run_not_active", 409),
    ("device_not_in_run", 422),
])
def test_platform_task_refusals_become_not_available(platform, run_script, code, status):
    responses, requests = platform
    responses.append((status, {"code": code, "message": "refused"}))
    result = run_script(tasks_script(), extra_args=PLATFORM_ARGS)
    assert result.code == 1
    assert result.envelope["error"]["message"] == f"NotAvailableError: ctx.tasks {code}"
    assert len(requests) == 1


def test_platform_other_task_errors_are_not_reported_as_unavailable(platform, run_script):
    responses, _ = platform
    responses.append((429, {"code": "run_task_limit", "message": "limit"}))
    result = run_script(tasks_script(), extra_args=PLATFORM_ARGS)
    assert result.code == 1
    assert "NotAvailableError" not in result.envelope["error"]["message"]


def test_platform_task_deadline_spans_polling_and_cancels_the_run_task(monkeypatch):
    from mobilerun_functions._adapters.tasks import RunTasks

    monkeypatch.setattr("mobilerun_functions._adapters.tasks.POLL_INTERVAL", 0.01)
    requests = []

    async def handle(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json=run_task())
        return httpx.Response(200, json=run_task("running"))

    async def exercise():
        client = SDKClient(
            api_key="test-key", base_url="https://api.example.test/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )
        device = run_device()
        with pytest.raises(TimeoutError):
            await RunTasks(client, device, "run-1").run("read screen", device=device, timeout=0.1)

    asyncio.run(exercise())
    task_path = "/v1/compute/runs/run-1/tasks/run-task-1"
    assert requests[0] == ("POST", "/v1/compute/runs/run-1/tasks")
    assert requests[1:-1].count(("GET", task_path)) >= 2
    assert requests[-1] == ("POST", f"{task_path}/cancel")


def test_cloud_flag_keeps_public_tasks_even_with_the_platform_signal(platform, run_script):
    responses, requests = platform
    responses.extend([
        (200, {"id": "task-1"}), (200, {"status": "completed", "terminal": True}),
    ])
    result = run_script(tasks_script(), extra_args=["--cloud", *PLATFORM_ARGS])
    assert result.code == 0, result.err
    assert [request.url.path for request in requests] == ["/v1/tasks", "/v1/tasks/task-1/status"]
    assert json.loads(requests[0].content)["deviceId"] == "dev-1"


@pytest.mark.parametrize("extra_args", [["--cloud", "--device-id", "dev-1"], PLATFORM_ARGS])
def test_task_on_another_device_fails_before_any_call(platform, run_script, extra_args):
    _, requests = platform
    result = run_script(source(
        "device: Device\ntasks: Tasks",
        'await ctx.tasks.run("read screen", device=object())',
    ), extra_args=extra_args)
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.tasks device is not the run's device"
    )
    assert requests == []


def test_tasks_may_be_declared_before_the_device(sdk, core, run_script):
    responses, requests = sdk
    responses.extend([
        (200, {"id": "task-1"}), (200, {"status": "completed", "terminal": True}),
    ])
    result = run_script(source(
        "tasks: Tasks\ndevice: Device", 'await ctx.tasks.run("x", device=ctx.device)',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert json.loads(requests[0].content)["deviceId"] == "dev-1"


# ctx.ai


AI_SCRIPT = 'print(ctx.ai.base_url, ctx.ai.api_key)'


def test_local_ai_uses_the_users_key_and_resolves_without_network(sdk, run_script):
    _, requests = sdk
    result = run_script(source("ai: AI", AI_SCRIPT))
    assert result.code == 0, result.err
    assert result.out == "https://api.example.test/v1/ai test-key\n"
    assert requests == []


def test_local_ai_defaults_to_the_public_gateway(sdk, monkeypatch, run_script):
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    result = run_script(source("ai: AI", AI_SCRIPT))
    assert result.code == 0, result.err
    assert result.out == "https://api.mobilerun.ai/v1/ai test-key\n"


def test_platform_ai_uses_the_injected_base_url_and_placeholder_key(sdk, monkeypatch, run_script):
    monkeypatch.setenv("MOBILERUN_FUNCTIONS_ENV", "cloud")
    monkeypatch.setenv("MOBILERUN_BASE_URL", "https://api.dev.example.test/v1/")
    monkeypatch.setenv("MOBILERUN_API_KEY", "placeholder-key")
    result = run_script(source("ai: AI", AI_SCRIPT))
    assert result.code == 0, result.err
    assert result.out == "https://api.dev.example.test/v1/ai placeholder-key\n"


def test_cloud_flag_ai_is_the_same_gateway(sdk, monkeypatch, run_script):
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    result = run_script(source("ai: AI", AI_SCRIPT), extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert result.out == "https://api.mobilerun.ai/v1/ai test-key\n"


@pytest.mark.parametrize("platform_env", [None, "cloud"])
def test_ai_without_key_fails_before_main(sdk, monkeypatch, run_script, platform_env):
    if platform_env:
        monkeypatch.setenv("MOBILERUN_FUNCTIONS_ENV", platform_env)
    monkeypatch.delenv("MOBILERUN_API_KEY")
    result = run_script(source("ai: AI", 'print("MAIN")'))
    assert result.code == 1
    assert result.envelope["error"]["message"] == "NotAvailableError: ctx.ai set MOBILERUN_API_KEY"
    assert result.out == ""


def test_platform_ai_without_base_url_fails_before_main(sdk, monkeypatch, run_script):
    monkeypatch.setenv("MOBILERUN_FUNCTIONS_ENV", "cloud")
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    result = run_script(source("ai: AI", 'print("MAIN")'))
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.ai set MOBILERUN_BASE_URL"
    )


def test_ai_does_not_need_the_device_sdk(sdk, monkeypatch, run_script):
    monkeypatch.setitem(sys.modules, "mobilerun_sdk", None)
    result = run_script(source("ai: AI", AI_SCRIPT))
    assert result.code == 0, result.err


def test_ai_openai_returns_a_client_for_the_gateway(sdk, run_script):
    result = run_script(source(
        "ai: AI",
        'c = ctx.ai.openai(); print(type(c).__name__, c.base_url, c.api_key)',
    ))
    assert result.code == 0, result.err
    assert result.out == "AsyncOpenAI https://api.example.test/v1/ai/ test-key\n"


def test_ai_openai_names_the_extra_when_it_is_missing(sdk, monkeypatch, run_script):
    monkeypatch.setitem(sys.modules, "openai", None)
    result = run_script(source("ai: AI", "ctx.ai.openai()"))
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.ai install mobilerun-functions[ai]"
    )


def test_ai_adapter_repr_hides_the_key():
    from mobilerun_functions._adapters.ai import AIAdapter

    text = repr(AIAdapter("https://api.example.test/v1", "secret-key"))
    assert "secret-key" not in text
    assert "https://api.example.test/v1" in text


def test_old_name_is_gone(sdk, run_script):
    old = "l" + "lm"
    result = run_script(source(f"{old}: {old.upper()}", 'print("MAIN")'))
    assert result.code != 0
    assert "MAIN" not in result.out
    ai = run_script(source("ai: AI", AI_SCRIPT))
    assert f"/v1/{old}" not in ai.out
    assert "/v1/ai" in ai.out


def test_resources_come_from_the_run_not_from_input(sdk, core, run_script):
    responses, requests = sdk
    decoy = "11111111-2222-4333-8444-555555555555"
    responses.extend([(200, {"data": {"address": None}}), (204, None)])
    result = run_script(
        source("device: Device\nemail: Mailbox\nproxy: Proxy", 'print("MAIN")'),
        {"mailbox_id": decoy, "proxy_id": decoy},
        extra_args=["--cloud", "--device-id", "dev-1", *MAILBOX_ARGS, *PROXY_ARGS],
    )
    assert result.code == 0, result.err
    assert [request.url.path for request in requests] == [
        f"/v1/mailboxes/{MAILBOX_ID}", "/v1/devices/dev-1/proxy",
    ]
    assert json.loads(requests[1].content) == {"connect": {"id": PROXY_ID}}
    assert decoy not in "".join(str(request.url) + request.content.decode() for request in requests)


@pytest.mark.parametrize("args,declared,message", [
    ([], "email: Mailbox", "ctx.email this run has no mailbox"),
    ([*PROXY_ARGS], "email: Mailbox", "ctx.email this run has no mailbox"),
    (MAILBOX_ARGS, "device: Device\nproxy: Proxy", "ctx.proxy this run has no proxy"),
    (
        ["--proxy-kind", "byo", "--proxy-id", PROXY_ID],
        "device: Device\nproxy: Proxy",
        "ctx.proxy supports only connect proxies",
    ),
])
def test_missing_or_reserved_resource_refuses_before_any_remote_call(
    sdk, core, monkeypatch, run_script, args, declared, message
):
    _, requests = sdk
    built = []
    monkeypatch.setattr("mobilerun_sdk.AsyncMobilerun", lambda **kw: built.append(kw))
    # The credentials binding comes first: it would read the cloud if the check ran in order.
    result = run_script(
        source(
            f'vault: Credentials = binding(package="com.example")\n{declared}', 'print("MAIN")'
        ),
        extra_args=["--cloud", "--device-id", "dev-1", *args],
    )
    assert result.code == 1
    assert result.envelope["error"]["message"] == f"NotAvailableError: {message}"
    assert built == [] and requests == []
    assert result.out == ""


def test_valid_mailbox_does_not_hide_a_missing_proxy_from_the_local_checks(
    sdk, core, monkeypatch, run_script
):
    _, requests = sdk
    built = []
    monkeypatch.setattr("mobilerun_sdk.AsyncMobilerun", lambda **kw: built.append(kw))
    result = run_script(
        source("device: Device\nemail: Mailbox\nproxy: Proxy", 'print("MAIN")'),
        extra_args=["--cloud", "--device-id", "dev-1", *MAILBOX_ARGS],
    )
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.proxy this run has no proxy"
    )
    assert built == [] and requests == []


def test_proxy_without_a_device_id_refuses_before_any_remote_call(sdk, monkeypatch, run_script):
    _, requests = sdk
    built = []
    monkeypatch.setattr("mobilerun_sdk.AsyncMobilerun", lambda **kw: built.append(kw))
    result = run_script(
        source("device: Device\nemail: Mailbox\nproxy: Proxy", 'print("MAIN")'),
        extra_args=["--cloud", *MAILBOX_ARGS, *PROXY_ARGS],
    )
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.proxy this run has no device"
    )
    assert built == [] and requests == []


@pytest.mark.parametrize("run_info,message", [
    ({"mailbox_id": "mailbox-1"}, "ctx.email has an invalid mailbox id"),
    ({"mailbox_id": MAILBOX_ID.upper() + " "}, "ctx.email has an invalid mailbox id"),
])
def test_programmatic_run_info_gets_the_same_validation(sdk, tmp_path, write_script, run_info,
                                                         message):
    from mobilerun_functions import RunInfo
    from mobilerun_functions.runner import RunOptions, run

    _, requests = sdk
    (tmp_path / "in.json").write_text("{}")
    path = write_script(source("email: Mailbox", 'print("MAIN")'))
    code = run(RunOptions(
        path=str(path), input_file=str(tmp_path / "in.json"),
        result_file=str(tmp_path / "out.json"), run_info=RunInfo(**run_info), cloud=True,
    ))
    envelope = json.loads((tmp_path / "out.json").read_text())
    assert code == 1
    assert envelope["error"]["message"] == f"NotAvailableError: {message}"
    assert requests == []


def test_programmatic_proxy_id_is_validated(sdk, core, tmp_path, write_script):
    from mobilerun_functions import ProxyRef, RunInfo
    from mobilerun_functions.runner import RunOptions, run

    _, requests = sdk
    (tmp_path / "in.json").write_text("{}")
    path = write_script(source("device: Device\nproxy: Proxy", 'print("MAIN")'))
    code = run(RunOptions(
        path=str(path), input_file=str(tmp_path / "in.json"),
        result_file=str(tmp_path / "out.json"),
        run_info=RunInfo(device_id="dev-1", proxy=ProxyRef("connect", "proxy-1")), cloud=True,
    ))
    envelope = json.loads((tmp_path / "out.json").read_text())
    assert code == 1
    assert envelope["error"]["message"] == "NotAvailableError: ctx.proxy has an invalid proxy id"
    assert requests == []
