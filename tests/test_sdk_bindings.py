"""Real generated SDK serialization against recorded response shapes, without network."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections import deque

import httpx
import pytest
from mobilerun_sdk import AsyncMobilerun as SDKClient
from test_bindings import source
from test_device import FakeCore


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


def credentials_page(items, *, has_next=False):
    return {
        "items": items,
        "pagination": {
            "hasNext": has_next, "hasPrev": False, "page": 1,
            "pages": 2, "pageSize": 100, "total": 2,
        },
    }


def credential(package="com.example", name="work", value="vault-secret"):
    return {
        "createdBy": None, "credentialName": name, "fields": [
            {"fieldType": "password", "value": value},
        ],
        "ownerId": "org-1", "packageName": package, "secretPath": "vault/reference",
    }


def test_cloud_credentials_are_paginated_filtered_and_never_return_values(sdk, run_script):
    responses, requests = sdk
    responses.extend([
        (200, credentials_page([credential("com.other")], has_next=True)),
        (200, credentials_page([credential()])),
    ])
    result = run_script(source(
        'vault: Credentials = binding(package="com.example")',
        'print(await ctx.vault.list())',
    ), extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert result.out.strip() == (
        "[{'package': 'com.example', 'name': 'work', 'fields': ['password']}]"
    )
    assert [request.url.params["page"] for request in requests] == ["1", "2"]
    assert [request.url.path for request in requests] == ["/v1/credentials", "/v1/credentials"]
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert "vault-secret" not in result.out + result.err + json.dumps(result.envelope)
    assert not responses


def test_cloud_credential_typing_never_returns_a_secret(sdk, core, run_script):
    responses, requests = sdk
    responses.append((200, credentials_page([credential()])))
    typed = []
    core.device.type = lambda text, **kw: typed.append(text)
    result = run_script(source(
        'device: Device\nvault: Credentials = binding(package="com.example")',
        'print(await ctx.vault.type_field(ctx.device, None, "password"))',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert typed == ["vault-secret"]
    assert result.out == "None\n"
    assert "vault-secret" not in result.out + result.err + json.dumps(result.envelope)
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/credentials"),
    ]


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
        'email: Mailbox = binding(input="mailbox_id")',
        'print(ctx.email.address, await ctx.email.wait_for_code(sender="sender@example.test"))',
    ))
    assert result.code == 0, result.err
    assert result.out == "example@example.test 001234\n"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/mailboxes/mailbox-1"), ("GET", "/v1/mailboxes/mailbox-1/otp"),
    ]
    assert requests[1].url.params["sender"] == "sender@example.test"


def test_local_mailbox_without_key_fails_before_main(sdk, monkeypatch, run_script):
    _, requests = sdk
    monkeypatch.delenv("MOBILERUN_API_KEY")
    monkeypatch.setitem(sys.modules, "mobilerun_sdk", None)
    result = run_script(source('email: Mailbox = binding(input="mailbox_id")', 'print("MAIN")'))
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.mailbox set MOBILERUN_API_KEY"
    )
    assert result.out == ""
    assert requests == []


def test_proxy_connects_existing_id_only_and_leaves_it_connected(sdk, run_script):
    responses, requests = sdk
    responses.extend([(204, None), (200, {"connected": True})])
    result = run_script(source(
        'proxy: Proxy = binding(input="proxy_id")',
        'print(await ctx.proxy.status())',
    ), extra_args=["--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == (
        "{'connected': True, 'name': None, 'protocol': None, '$schema': None}\n"
    )
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/devices/dev-1/proxy"), ("GET", "/v1/devices/dev-1/proxy"),
    ]
    assert json.loads(requests[0].content) == {"connect": {"id": "proxy-1"}}
    assert not responses


def test_cloud_flag_defaults_to_public_base_and_legacy_key(sdk, monkeypatch, run_script):
    responses, requests = sdk
    monkeypatch.delenv("MOBILERUN_BASE_URL")
    monkeypatch.delenv("MOBILERUN_API_KEY")
    monkeypatch.setenv("MOBILERUN_CLOUD_API_KEY", "legacy-test-key")
    responses.append((200, {"data": {"address": None}}))
    result = run_script(source('email: Mailbox = binding(input="mailbox_id")'),
                        extra_args=["--cloud"])
    assert result.code == 0, result.err
    assert str(requests[0].url) == "https://api.mobilerun.ai/v1/mailboxes/mailbox-1"
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
        'device: Device\noperator: Operator',
        'print((await ctx.device.do("read screen", output=Answer, max_steps=8)).model_dump_json())',
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


@pytest.mark.parametrize("status,succeeded,passed", [
    ("completed", True, True), ("failed", False, False), ("cancelled", False, False),
])
def test_expect_maps_terminal_task_and_verdict(sdk, core, run_script, status, succeeded, passed):
    responses, requests = sdk
    responses.extend([
        (200, {"id": "task-1"}),
        (200, {
            "status": status, "terminal": True, "succeeded": succeeded,
            "message": "done", "output": {"passed": True, "reason": "screen matches"},
        }),
    ])
    result = run_script(source(
        'device: Device\noperator: Operator',
        'print((await ctx.device.expect("screen ready")).model_dump_json())',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    verdict = json.loads(result.out)
    assert verdict["passed"] is passed
    assert verdict["condition"] == "screen ready" and verdict["reason"] == "screen matches"
    assert verdict["task"]["success"] is succeeded
    assert json.loads(requests[0].content)["outputSchema"]["required"] == ["passed", "reason"]


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
        'device: Device\noperator: Operator',
        'print((await ctx.device.expect("ready")).model_dump_json())',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    verdict = json.loads(result.out)
    assert verdict["passed"] is False and verdict["task"]["success"] is False
    assert verdict["task"]["output"] is None
    assert verdict["reason"] == "task output does not match the output schema"


def test_task_deadline_interrupts_polling_and_stops_existing_task():
    from mobilerun_functions._adapters.tasks import Tasks

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

        tasks = Tasks(types.SimpleNamespace(tasks=types.SimpleNamespace(
            run=run, get_status=status, stop=stop,
        )), "dev-1")
        with pytest.raises(TimeoutError):
            await tasks.run("read screen", timeout=0.01)

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
        'email: Mailbox = binding(input="mailbox_id")',
        'print(await ctx.email.wait_for_code(after=datetime(2026, 11, 24, tzinfo=UTC)))',
        before="\nfrom datetime import UTC, datetime\n",
    ))
    assert result.code == 0, result.err
    assert result.out == "0007\n"
    assert [request.url.path for request in requests] == [
        "/v1/mailboxes/mailbox-1", "/v1/mailboxes/mailbox-1/otp", "/v1/mailboxes/mailbox-1/otp",
    ]
    assert requests[2].url.params["after"] == "2026-11-24T00:00:00+00:00"
    pause.assert_awaited_once_with(2.0)


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
        "device: Device\noperator: Operator",
        'print((await ctx.device.do("read screen")).success)',
    ), extra_args=["--cloud", "--device-id", "dev-1"])
    assert result.code == 0, result.err
    assert result.out == "True\n"
    assert [request.url.path for request in requests] == [
        "/v1/tasks", "/v1/tasks/task-1/status", "/v1/tasks/task-1/status",
    ]
    pause.assert_awaited_once_with(2.0)
