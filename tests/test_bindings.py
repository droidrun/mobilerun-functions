"""Bindings are a declaration contract shared by inspect and run."""

import json

import pytest

PRELUDE = '''
from pydantic import BaseModel
from mobilerun_functions import (
    Context, Credentials, Device, AI, Mailbox, Proxy, Tasks, binding, script,
)

class Input(BaseModel):
    mailbox_id: str = "business-value"
    proxy_id: str = "business-value"
'''


def source(fields, body="pass", *, before=""):
    return (
        PRELUDE + before + "\nclass Ctx(Context):\n    " + fields.replace("\n", "\n    ")
        + "\n\n@script\nasync def main(input: Input, ctx: Ctx) -> None:\n    " + body + "\n"
    )


def test_inspect_bindings_requires_and_keeps_business_input(inspect_source):
    code, out, err = inspect_source(source(
        'phone: Device\ntasks: Tasks = binding()\n'
        'netflix: Credentials = binding(package="com.netflix.mediaclient")\n'
        'gmail: Credentials = binding(package="com.google.android.gm")\n'
        'email: Mailbox\nproxy: Proxy\n'
        'ai: AI'
    ))
    assert code == 0, err
    document = json.loads(out)
    assert document["requires"] == {
        "device": True,
        "credentials": ["com.google.android.gm", "com.netflix.mediaclient"],
        "mailbox": True,
        "proxy": True,
        "tasks": True,
        "ai": True,
    }
    assert set(document["inputSchema"]["properties"]) == {"mailbox_id", "proxy_id"}
    assert "format" not in document["inputSchema"]["properties"]["mailbox_id"]
    assert "format" not in document["inputSchema"]["properties"]["proxy_id"]


def test_optionless_binding_equals_bare_annotation(inspect_source):
    code, bare, err = inspect_source(source("phone: Device\nemail: Mailbox\nproxy: Proxy"))
    assert code == 0, err
    code, explicit, err = inspect_source(source(
        "phone: Device = binding()\nemail: Mailbox = binding()\nproxy: Proxy = binding()"
    ))
    assert code == 0, err
    assert json.loads(bare)["requires"] == json.loads(explicit)["requires"]


@pytest.mark.parametrize("signature", ["input: Input", "input: Input, ctx: Context"])
def test_legacy_and_no_context_require_nothing(inspect_source, signature):
    code, out, _ = inspect_source(
        PRELUDE + f"\n@script\ndef main({signature}) -> None:\n    pass\n"
    )
    assert code == 0
    assert json.loads(out)["requires"] == {
        "device": False, "credentials": [], "mailbox": False, "proxy": False, "tasks": False,
        "ai": False,
    }


@pytest.mark.parametrize("field", [
    "phone: dict", "phone: Concrete", "phone: Device = Concrete()",
    'phone: Device = "cloud"', 'phone: Device = {}', "phone: list[Device]",
])
def test_inspect_rejects_implementations_and_other_types(inspect_source, field):
    code, out, err = inspect_source(source(field, before="\nclass Concrete:\n    pass\n"))
    assert code == 3
    assert json.loads(out)["error"]["message"] == (
        "phone: the implementation belongs in the local configuration; "
        "declare the interface type with binding(...)"
    )
    assert len(err.splitlines()) == 1


@pytest.mark.parametrize("fields,message", [
    ("phone: Device\nsecond: Device", "at most one Device"),
    ("phone: Device\na: Tasks\nb: Tasks", "at most one Tasks"),
    ("a: Mailbox\nb: Mailbox", "at most one Mailbox"),
    ("phone: Device\na: Proxy\nb: Proxy", "at most one Proxy"),
    ("a: AI\nb: AI", "at most one AI"),
    ("ai: AI = binding(package='pkg')", "accepts only binding()"),
    ("email: Mailbox = binding(package='pkg')", "email: Mailbox accepts only binding()"),
    ("phone: Device\nproxy: Proxy = binding(package='pkg')", "proxy: Proxy accepts only binding()"),
    ("tasks: Tasks", "tasks: requires a Device binding"),
    ("vault: Credentials", 'Credentials requires binding(package="...")'),
    ('a: Credentials = binding(package="pkg")\nb: Credentials = binding(package="pkg")',
     "credential packages must be distinct"),
    ('phone: Device = binding(package="pkg")', "accepts only binding()"),
    ('phone: Device\ntasks: Tasks = "cloud"', "tasks: the implementation belongs"),
    ("proxy: Proxy", "proxy: requires a Device binding"),
    ("email: Mailbox\nproxy: Proxy", "proxy: requires a Device binding"),
])
def test_invalid_declarations_fail_at_inspect_and_run(inspect_source, run_script, fields, message):
    script = source(fields)
    code, out, err = inspect_source(script)
    assert code == 3
    assert json.loads(out)["error"]["code"] == "entry_point_invalid"
    assert message in err
    assert len(err.splitlines()) == 1
    result = run_script(script)
    assert result.code == 3 and result.envelope is None
    assert result.err == err


@pytest.mark.parametrize("field", [
    'email: Mailbox = binding(input="mailbox_id")',
    'phone: Device\nproxy: Proxy = binding(input="proxy_id")',
    'phone: Device = binding(input="x")',
])
def test_input_bound_declarations_are_refused_at_inspect_and_run(inspect_source, run_script, field):
    script = source(field, 'raise AssertionError("main was called")')
    code, out, err = inspect_source(script)
    assert code == 3
    assert "unexpected keyword argument 'input'" in json.loads(out)["error"]["message"]
    result = run_script(script)
    assert result.code == 1
    assert "unexpected keyword argument 'input'" in result.envelope["error"]["message"]
    assert "main was called" not in result.err


def test_business_fields_named_like_resources_stay_in_the_schema(inspect_source):
    code, out, err = inspect_source(source("email: Mailbox"))
    assert code == 0, err
    assert set(json.loads(out)["inputSchema"]["properties"]) == {"mailbox_id", "proxy_id"}


def test_subclass_inherits_declarations(inspect_source):
    script = source("pass", before="\nclass Parent(Context):\n    phone: Device\n")
    script = script.replace("class Ctx(Context)", "class Ctx(Parent)")
    code, out, _ = inspect_source(script)
    assert code == 0
    assert json.loads(out)["requires"]["device"] is True


def test_missing_device_fails_before_main(run_script):
    result = run_script(source("device: Device", 'raise AssertionError("main was called")'))
    assert result.code == 1
    assert result.envelope["error"] == {
        "code": "script_error",
        "message": "NotAvailableError: ctx.device is not available: this run has no device",
        "retryable": False,
    }
    assert "main was called" not in result.err


def test_local_tasks_fail_before_main(run_script):
    result = run_script(source("device: Device\ntasks: Tasks", 'print("MAIN")'))
    assert result.code == 1
    assert result.envelope["error"]["message"] == "NotAvailableError: ctx.tasks needs --cloud"
    assert result.out == ""


def test_sync_empty_context_can_run_its_own_event_loop(run_script):
    script = source(
        "pass", 'print(asyncio.run(asyncio.sleep(0, result="ok")))',
        before="\nimport asyncio\n",
    ).replace("async def main", "def main")
    result = run_script(script)
    assert result.code == 0, result.err
    assert result.out == "ok\n"


@pytest.mark.parametrize("name", [
    "run", "log", "state", "dataset", "partial", "partial_reason", "files",
    "_private", "_device", "__dict__",
])
def test_reserved_context_binding_names_are_rejected(inspect_source, run_script, name):
    script = source(f"{name}: Device")
    code, out, err = inspect_source(script)
    assert code == 3
    assert json.loads(out)["error"]["message"] == (
        f"{name}: reserved Context member; choose another name"
    )
    assert len(err.splitlines()) == 1
    result = run_script(script)
    assert result.code == 3 and result.envelope is None
    assert result.err == err


def test_ai_stub_on_plain_context_says_how_to_declare_it(run_script):
    result = run_script(source("pass", "ctx.ai"))
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.ai is not available: declare `ai: AI` in your Ctx"
    )
