"""Bindings are a declaration contract shared by inspect and run."""

import json

import pytest

PRELUDE = '''
from pydantic import BaseModel, Field
from mobilerun_functions import (
    Context, Credentials, Device, Mailbox, Operator, Proxy, binding, script,
)

class Input(BaseModel):
    mailbox_id: str = Field(default="mailbox-1", json_schema_extra={"format": "mobilerun-mailbox"})
    proxy_id: str = Field(default="proxy-1", json_schema_extra={"format": "mobilerun-proxy"})
'''


def source(fields, body="pass", *, before=""):
    return (
        PRELUDE + before + "\nclass Ctx(Context):\n    " + fields.replace("\n", "\n    ")
        + "\n\n@script\nasync def main(input: Input, ctx: Ctx) -> None:\n    " + body + "\n"
    )


def test_inspect_bindings_requires_and_input_formats(inspect_source):
    code, out, err = inspect_source(source(
        'phone: Device\noperator: Operator = binding()\n'
        'netflix: Credentials = binding(package="com.netflix.mediaclient")\n'
        'gmail: Credentials = binding(package="com.google.android.gm")\n'
        'email: Mailbox = binding(input="mailbox_id")\nproxy: Proxy = binding(input="proxy_id")'
    ))
    assert code == 0, err
    document = json.loads(out)
    assert document["requires"] == {
        "device": True,
        "credentials": ["com.google.android.gm", "com.netflix.mediaclient"],
        "mailbox": True,
        "proxy": True,
        "tasks": True,
    }
    assert document["inputSchema"]["properties"]["mailbox_id"]["format"] == "mobilerun-mailbox"
    assert document["inputSchema"]["properties"]["proxy_id"]["format"] == "mobilerun-proxy"


@pytest.mark.parametrize("signature", ["input: Input", "input: Input, ctx: Context"])
def test_legacy_and_no_context_require_nothing(inspect_source, signature):
    code, out, _ = inspect_source(
        PRELUDE + f"\n@script\ndef main({signature}) -> None:\n    pass\n"
    )
    assert code == 0
    assert json.loads(out)["requires"] == {
        "device": False, "credentials": [], "mailbox": False, "proxy": False, "tasks": False,
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
    ("phone: Device\na: Operator\nb: Operator", "at most one Operator"),
    ('a: Mailbox = binding(input="mailbox_id")\nb: Mailbox = binding(input="mailbox_id")',
     "at most one Mailbox"),
    ('a: Proxy = binding(input="proxy_id")\nb: Proxy = binding(input="proxy_id")',
     "at most one Proxy"),
    ("operator: Operator", "requires a Device"),
    ("vault: Credentials", 'Credentials requires binding(package="...")'),
    ('a: Credentials = binding(package="pkg")\nb: Credentials = binding(package="pkg")',
     "credential packages must be distinct"),
    ('phone: Device = binding(package="pkg")', "accepts only binding()"),
    ("phone: Device\noperator: Operator = binding(input='proxy_id')", "accepts only binding()"),
    ("email: Mailbox", "requires binding"),
    ('proxy: Proxy = binding(input="absent")', "requires binding"),
    ('email: Mailbox = binding(input="proxy_id")', 'format "mobilerun-mailbox"'),
    ('proxy: Proxy = binding(input="mailbox_id")', 'format "mobilerun-proxy"'),
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


@pytest.mark.parametrize("field_type", ["int", "str | None"])
def test_linked_input_must_be_exactly_str(inspect_source, field_type):
    script = source('email: Mailbox = binding(input="mailbox_id")').replace(
        'mailbox_id: str = Field(default="mailbox-1",',
        f'mailbox_id: {field_type} = Field(default=None,',
    )
    code, out, _ = inspect_source(script)
    assert code == 3
    assert 'format "mobilerun-mailbox"' in json.loads(out)["error"]["message"]


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


def test_local_operator_fails_before_main(run_script):
    result = run_script(source("device: Device\noperator: Operator", 'print("MAIN")'))
    assert result.code == 1
    assert result.envelope["error"]["message"] == (
        "NotAvailableError: ctx.tasks needs the framework agent add-on or --cloud"
    )
    assert result.out == ""
