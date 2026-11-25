# mobilerun-functions

Python package for writing Mobilerun routine scripts. A script has one typed entry point that
receives a validated input model and a `Context` with what the platform provides, and returns
an output model. The package also ships the `inspect` and `run` commands the platform uses to
read a script's schema and to execute it.

Requires Python 3.11 to 3.13 and `pydantic>=2.11` (instances are re-validated by field name,
which needs `by_name` validation from pydantic 2.11).

## Install

```sh
pip install mobilerun-functions
```

For device control add the extra: `pip install "mobilerun-functions[device]"`. For `ctx.ai.openai()` add `[ai]` (`openai>=3,<4`).

## Hello

Create a working script and inspect its contract:

```sh
python -m mobilerun_functions new hello.py
python -m mobilerun_functions inspect hello.py
printf '{"name":"Ada"}\n' > in.json
python -m mobilerun_functions run hello.py --input-file in.json --result-file out.json
cat out.json
# {"ok":true,"outcome":"succeeded","result":{"message":"Hello, Ada!"},"input":{"name":"Ada","greeting":"Hello"}}
```

A script is one Python file with this shape (the starter also has a `greeting` input):

```python
from pydantic import BaseModel
from mobilerun_functions import Context, script


class Input(BaseModel):
    name: str = "world"  # defaults are allowed


class Output(BaseModel):
    message: str


@script
def main(input: Input, ctx: Context) -> Output:
    ctx.log.info("greeting")  # a log line on stderr; `print(...)` works too
    return Output(message=f"Hello, {input.name}!")
```

- **Import.** `script` and `Context` come from `mobilerun_functions`, nothing else
  (`from mobilerun import script` does not exist).
- **Entry point.** Mark exactly one function with the bare decorator `@script`. It may be `def`
  or `async def`, takes `input` (a pydantic model) and, optionally, `ctx: Context`.
- **Input.** The `Input` model is the input schema; fields with defaults are optional. `run`
  fills the defaults in and reports the input the function received as `input` in the envelope.
- **Return type.** One of `Output` (a pydantic model), `Output | None`, `None`, or
  `Dataset[Item]` (see Datasets). Anything else is rejected with `entry_point_invalid`.
  A required `Output` returning `None` fails with `output_invalid`; annotate `Output | None`
  when `None` is intentional. Missing return annotations assume no output and produce an
  `inspect` warning.
- **Logging.** `ctx.log.info(...)` writes a line to stderr. `print(...)` works too: `run` leaves
  it on stdout (the result goes to the result file), and `inspect` moves it to stderr so stdout
  holds only its JSON document.
- **Time limit.** The platform sets it; the script does not.
- **Starter.** `python -m mobilerun_functions new hello.py` writes a working script in this shape.

When a script is not set up this way, `inspect` reports what is wrong with a line number and a
repair hint (see below).

Helper modules next to the script are not importable: use one file per script.

## Test a script

The decorator returns the original function, so call it directly in a test:

```python
from hello import Input, main
from mobilerun_functions import Context, RunInfo, State


def test_greeting() -> None:
    ctx = Context(RunInfo(), state=State(previous={"count": 1}, previous_schema=1))
    output = main(Input(name="Ada"), ctx)
    assert output.message == "Hello, Ada!"
    ctx.state.save({"count": 2})
    assert ctx.state.saved is not None
    assert ctx.state.saved.data == {"count": 2}
```

Direct calls keep the function's signature for type checkers. Use `await main(...)` for async
scripts. Direct calls do not run the CLI's input/output validation or size checks.

## State between runs

Pass `--state-file PATH` to enable saving. A missing file or JSON `null` means no previous
state. The file can contain the envelope's `state` object as-is:
`{"meta":{"schema":1},"data":{"count":1}}`. The platform's
`{"version":7,"meta":{"schema":1},"data":{"count":1}}` format is also accepted.
`run` reads this file; it writes the new snapshot into the result envelope, not back to the file.
Saving without the flag raises `NotAvailableError` and tells you to pass it.

Save this as `counter.py`:

```python
from pydantic import BaseModel
from mobilerun_functions import Context, script


class Input(BaseModel):
    pass


class Saved(BaseModel):
    count: int = 0


@script
def main(input: Input, ctx: Context) -> Saved:
    previous = Saved.model_validate(ctx.state.previous) if ctx.state.previous is not None else Saved()
    current = Saved(count=previous.count + 1)
    ctx.state.save(current, schema=1)
    return current
```

In a fresh directory (with no `state.json`), run it twice:

```sh
printf '{}\n' > in.json
for attempt in 1 2; do
  python -m mobilerun_functions run counter.py --input-file in.json --result-file out.json \
    --state-file state.json --attempt "$attempt"
  python -c 'import json; d=json.load(open("out.json")); print(d["result"]); json.dump(d["state"],open("state.json","w"))'
done
# {'count': 1}
# {'count': 2}
```

`state.previous` is a JSON value; narrow it with `Saved.model_validate(...)`, as above.
`state.previous_schema` is the schema number used by the previous save, or `None` without one.
Use it to migrate changed shapes. The last `save` wins; state is emitted only after success.

## Datasets

Return `Dataset[Item]` for a list the platform can diff between runs. Mark the field that
identifies an item with `key_field()` (a `Field(...)` that accepts the usual arguments); exactly
one field must carry it, and it must serialise to a string or an integer (`str`, `int`, `UUID`,
...; not `float`, `bool` or a list). The package does not check that keys are unique.
`Dataset` has only `items`: report an incomplete list with `ctx.partial(reason)`.

A long run should not lose what it found, so `run --dataset-file PATH` creates (and truncates)
a JSON Lines file and `ctx.dataset.push(item)` appends one validated item to it at once.
`push` is available when the entry point returns `Dataset[Item]` (or `Dataset[Item] | None`).
Without `--dataset-file`, validated items are buffered in memory with the same byte accounting
(one JSON line per item). For any other return type, `push` raises `NotAvailableError`. An
item is an `Item` or data valid for `Item`; a bad one raises `DatasetItemInvalidError`
(`output_invalid`). A line that would take the file over 524288 bytes raises
`DatasetTooLargeError` (`dataset_too_large`, exit 1); the lines before stay, and a script may
catch the error and call `ctx.partial`.

With `--dataset-file`, items the entry point returns are appended after the pushed ones and
`result` is `null`; every envelope carries `"dataset": {"items": N, "bytes": B}` for the lines
this run wrote. Without it, `result` holds `{"items": [...]}` with pushed items first,
then returned items. Returning `None` for `Dataset[Item]` publishes the pushed items,
including an empty dataset when nothing was pushed. This also applies to `Dataset[Item] | None`.
Static checking accepts models and dictionaries for `push`; the precise `Item` shape is checked
at runtime. For direct tests, pass `DatasetSink(item_type=Item)` from `mobilerun_functions.dataset`
to `Context(..., dataset=...)`.

Instead of pushing, return the items together:

```python
@script
def main(input: Input, ctx: Context) -> Dataset[Product]:
    return Dataset(items=[Product(sku="a", name="Apple", unit_price=1.0),
                          Product(sku="b", name="Banana", unit_price=2.0)])
```

### Dataset example

```python
from datetime import date

from pydantic import BaseModel, Field
from mobilerun_functions import Context, Dataset, ScriptError, key_field, script


class Input(BaseModel):
    query: str
    since: date  # a `format: date` input; the script receives a `date`
    max_pages: int = 3


class Product(BaseModel):
    sku: str = key_field()  # the field that identifies an item between runs
    name: str
    unit_price: float = Field(serialization_alias="unitPrice")


@script
async def main(input: Input, ctx: Context) -> Dataset[Product] | None:
    if input.max_pages < 1:
        raise ScriptError("bad_pages", "max_pages must be at least 1")
    ctx.log.info(f"searching {input.query}")
    for page in range(1, input.max_pages + 1):
        try:
            rows = await fetch_page(input.query, page)  # your code
        except TimeoutError:
            ctx.partial(f"page {page} timed out")
            break
        for row in rows:
            ctx.dataset.push(Product(sku=row["sku"], name=row["name"], unit_price=row["price"]))
    # nothing to return: every item was pushed
```

`def` works the same way as `async def`, and `ctx` may be left out of the signature. Return a
`Dataset[Item]` for a list the platform can diff, call `ctx.partial("reason")` to mark a
partial result, and use `ctx.state` to carry data to the next run.

## Context reference

### Bindings contract (0.2.0)

Declare what the function needs using interface types in a `Context` subclass. The runner
provides implementations before `main`; the function file stays the same locally and in the
cloud. `Device`, `Credentials`, `Mailbox`, `Proxy`, `Tasks`, and `AI` are structural Python
Protocols. The temporary adapters will be replaced by the framework factory.

```python
from pydantic import BaseModel, Field
from mobilerun_functions import Context, Device, Credentials, AI, Mailbox, Proxy, Tasks, binding, script

class Input(BaseModel):
    mailbox_id: str = Field(json_schema_extra={"format": "mobilerun-mailbox"})
    proxy_id: str = Field(json_schema_extra={"format": "mobilerun-proxy"})

class Ctx(Context):
    device: Device
    account: Credentials = binding(package="com.example.app")
    email: Mailbox = binding(input="mailbox_id")
    proxy: Proxy = binding(input="proxy_id")
    tasks: Tasks
    ai: AI

@script
async def main(input: Input, ctx: Ctx) -> None:
    await ctx.tasks.run("Focus the password field", device=ctx.device, max_steps=5)
    await ctx.account.type_field(ctx.device, "com.example.app", "password")
```

Install `mobilerun-functions[device]` for cloud resources; add `[local]` for local device
backends. `Device` and `Tasks` need no default, or `binding()`. Credentials require a
package and several credentials bindings may declare distinct packages. Mailbox and proxy
bindings each link to a `str` Input field with the corresponding `format` shown above; the
input value is the id of an existing resource. At most one of each other interface is allowed.
`Tasks` requires the `Device` binding (there is one device in the context). A concrete
implementation, other field type, or concrete instance in `Ctx` fails `inspect` with
“the implementation belongs in the local configuration”.

`inspect` always emits `requires`, including for legacy scripts and scripts without `ctx`:

```json
{"device": true, "credentials": ["com.example.app"], "mailbox": true, "proxy": true, "tasks": true, "ai": true}
```

Packages are sorted and unique. Legacy scripts and scripts without `ctx` get all false and
an empty credentials list. The platform can use this declaration for admission.

The runner captures `MOBILERUN_FUNCTIONS_ENV=cloud` before importing the function. This
platform signal selects cloud bindings even when local device flags are present. Outside the
platform, local device flags select the existing backends and credentials come from
`--credentials-file PATH`. `--cloud` forces every binding to the cloud with your own API key.
The base URL comes from `MOBILERUN_BASE_URL`, then `MOBILERUN_API_BASE_URL`; `--cloud`
and local mailbox/proxy access default it to `https://api.mobilerun.ai/v1`. Keys come from
`MOBILERUN_API_KEY`, then `MOBILERUN_CLOUD_API_KEY`. These connection choices cannot be changed by the function's module.
A binding that cannot be resolved fails the run before `main` with `NotAvailableError` in the
usual failure envelope.

```sh
# Local device and file credentials
python -m mobilerun_functions run function.py --input-file in.json --result-file out.json \
  --device-target local --device-id emulator-5554 --credentials-file credentials.yaml

# Mailbox-only function, with MOBILERUN_API_KEY already set in the environment
python -m mobilerun_functions run mailbox_only.py --input-file in.json --result-file out.json

# Same file, cloud resources with your own key already set in the environment
python -m mobilerun_functions run function.py --input-file in.json --result-file out.json \
  --cloud --device-id YOUR_EXISTING_DEVICE_ID
```

The credentials file uses the framework's YAML format, package → name → fields. A flat
`name: value` entry is an unbound credential with the field `password`. Use `chmod 600`:
files readable or writable by group or others are refused.

```yaml
com.example.app:
  work:
    username: example
    password: example-only
another_account: example-only
```

`credentials.list(package=None)` returns only package, name, and field names.
`credentials.type_field(device, package, field, name=None)` types directly into a focused
field and returns no secret value. It refreshes state and refuses to type unless a non-empty
focused element is reported with `isEditable: true`. `package=None` searches the declared
credentials;
requesting another package cannot expose it. Driver failures while typing credentials are
reported without the value. Never put credential values in input JSON or log them.

Mailbox access always uses the cloud SDK, also for a local device, and needs
`MOBILERUN_API_KEY` (or its legacy name); the base URL defaults as above. `address` is the
resolved address;
`await mailbox.wait_for_code(sender=None, timeout=60, after=None)` polls for an OTP while
preserving leading zeros. Proxy resolution requires a cloud device (`--cloud` or
`--device-target cloud`) and connects the supplied existing proxy to the run's `--device-id` through the device proxy endpoint and **leaves it connected** after the run.
No resource is bought, created, or deleted during resolution.

Declaring `ai: AI` (`binding()` only, at most one) gives an OpenAI-compatible client for the
platform AI: `ctx.ai.base_url` (the API base URL plus `/ai`, for example `https://api.mobilerun.ai/v1/ai`), `ctx.ai.api_key` (a bearer for that base URL
only) and `ctx.ai.openai()`, a new `openai.AsyncOpenAI` for it (needs `mobilerun-functions[ai]`,
else `NotAvailableError("ai", "install mobilerun-functions[ai]")`). `ai` is the one binding
that is cloud in both modes: it always calls the Mobilerun gateway, with your own key locally
(`MOBILERUN_API_KEY`, base URL defaulting as above) and with the platform's run credentials in
the cloud, where `api_key` is only a placeholder. Resolution makes no network call. Scoped
API keys are refused by the gateway, and usage is billed to the run's organization, not to
the function author. A missing key fails the run before `main`.

```python
client = ctx.ai.openai()
reply = await client.chat.completions.create(
    model="openai/gpt-4o", messages=[{"role": "user", "content": "Hello"}],
)
```

The async Device interface provides `screenshot` (bytes), `tap`, `swipe`, `drag`, `input_text`,
`press_button`, `press_key_code`, `start_app`, `list_apps`, `wait`, and `state`.
`state.refresh()` returns `UIState` and `state.current` is a property returning the latest snapshot (raises
`NotAvailableError` before the first refresh). UIState contains elements, formatted_text, focused_text, phone_state,
screen_width, and screen_height. **The wording of `formatted_text` may change in a later version.**

Declaring `tasks: Tasks` provides `await ctx.tasks.run(task, device=ctx.device, output=Model,
max_steps=..., timeout=...)`. It runs a natural-language task on the context's device, spends
normal task usage, and buys nothing. `device` must be the context's `Device`; any other object
raises `NotAvailableError("tasks", "device is not the run's device")` before a call is made.
`timeout` is in seconds (default 1800). The result is an `ExecutionResult[T]` (success, answer,
typed output, steps, task_id, trajectory_path, device); an output that does not match `Model`
sets `success` to false.

```python
class Login(BaseModel):
    logged_in: bool

result = await ctx.tasks.run("Check whether the user is logged in", device=ctx.device, output=Login)
if not result.success or result.output is None:
    ctx.log.warning("Could not determine the login state")
```

In the platform the task goes through the run's own task routes, which hand the run's device
lease to the task and take it back when the task ends. Refusals surface as
`NotAvailableError("tasks", code)` with the code `device_lease_not_held`, `tasks_not_declared`,
`run_not_active` or `device_not_in_run`. With `--cloud` the public tasks API is used with your
own key. Local execution without `--cloud` fails before `main` with
`NotAvailableError("tasks", "needs --cloud")`. See `examples/bindings.py` for a full declaration.

No ctx method buys, creates, or deletes resources. Tasks consume usage like any task.

### Legacy Context


| Member | Behaviour |
| --- | --- |
| `run.run_id` / `run.attempt` | Run identifier (`"local"`) and attempt number (`1`). |
| `run.mode` | `live` (default), `dry_run`, or `verification`; the script decides how to handle it. |
| `run.deadline` | Optional datetime, informational; the platform enforces the deadline. |
| `run.device_id` | Optional device identifier. |
| `run.device_target` | `cloud` (default) or `local`. |
| `run.device_platform` | Optional local device platform: `android` or `ios`. |
| `run.device_url` | Optional local Portal HTTP URL. |
| `log.info` / `log.warning` / `log.error` | One line on stderr per call. |
| `log.status` | Replaces the file named by `MOBILERUN_FUNCTIONS_STATUS_FILE`; no-op if unset, best effort. |
| `state.previous` / `state.previous_schema` | Prior saved JSON data and its schema number, or `None`. |
| `state.save(data, schema=1)` | Copies a JSON value or model; requires `--state-file` in CLI runs. |
| `dataset.push(item)` | Validates and emits an item for a dataset entry point; buffers without a file. |
| `partial(reason)` | Marks a successful run incomplete; the last reason wins. |
| `device()` | Deprecated: connects once and returns the run's `mobilerun_core.Device`; see below. |
| `credentials` / `files` / `ai` | Legacy stubs raise `NotAvailableError`; declare `credentials` or `ai` in your `Ctx` to get the binding. |

### Control the device

Existing scripts annotated `ctx: Context` keep `ctx.device()` unchanged in 0.2.0.
The first call per context emits one `DeprecationWarning` naming the new `Ctx` form.
A run started with a device gives the script `ctx.device()`. It is synchronous and returns the
`mobilerun-core` device of the run, connected once and reused on every call:

```python
@script
def main(input: Input, ctx: Context) -> None:
    device = ctx.device()
    path = device.screenshot()  # path of a temporary image file
    ui = device.ui()  # UI tree; `ui.nodes` lists the elements
    device.tap(540, 1200)
    device.type("hello")
    device.start_app("com.android.settings")
```

The device is the unchanged `mobilerun_core.Device`; see that package for all its methods. It is
an optional dependency: install `mobilerun-functions[device]`. A run without a device, without
`mobilerun-core`, or without the platform's environment raises `NotAvailableError`; scripts
never build a client or read the environment. The platform handles the credentials and sets the
API base URL in `MOBILERUN_BASE_URL`; the older `MOBILERUN_API_BASE_URL` is still read when the
new name is unset or empty.

### Run against a local device

To test a script against your own emulator or phone, install the extra
(`pip install "mobilerun-functions[local]"`) and pass the device to `run`:

- `--device-target local|cloud`: where the device lives (default `cloud`, which is the platform's
  behaviour and needs no flag).
- `--device-platform android|ios`: the platform of a device reached by URL.
- `--device-url URL`: the Portal HTTP URL of the device.

```sh
# Android over ADB: the device id is the serial from `adb devices`
python -m mobilerun_functions run script.py --input-file in.json --result-file out.json \
  --device-id emulator-5554 --device-target local

# iOS through its Portal URL
python -m mobilerun_functions run script.py --input-file in.json --result-file out.json \
  --device-target local --device-platform ios --device-url http://192.168.1.20:6643
```

`--device-platform` defaults to `android`; with `--device-url` it uses the Android Portal over HTTP,
without it ADB. `ios` always uses the iOS Portal. A local run never opens a cloud device.

Portal tokens and URLs are read by `mobilerun-core` from its own environment variables
(`MOBILERUN_ANDROID_PORTAL_TOKEN`, `MOBILERUN_IOS_PORTAL_TOKEN`, …); this package never reads or
passes them. The Android Portal needs a token; the iOS one may run without it. A missing serial,
URL or required token raises `NotAvailableError`.

## Commands reference

```sh
# write a starter script (fails if the file exists; the directory must exist)
python -m mobilerun_functions new hello.py

# print the entry point and the input/output JSON Schema (exit 3 if there is no valid entry point)
python -m mobilerun_functions inspect script.py

# validate input, call the entry point, write one result envelope
python -m mobilerun_functions run script.py --input-file in.json --result-file out.json \
    [--state-file state.json] [--dataset-file dataset.jsonl]
```

### `new`

`new PATH` writes a short working script (an `Input` and an `Output` model and a `@script` entry
point) to `PATH` and prints the path. It never overwrites: if `PATH` exists, or its directory is
missing, it prints one line on stderr and exits 64. The starter passes `inspect` as written.

### `inspect`

On success it prints one JSON object on stdout and exits 0:
`{"ok": true, "entrypoint", "async", "inputSchema", "outputSchema", "outputKind", "datasetKey",
"sdkVersion", "requires", "warnings"}`. `outputKind` is `value`, `dataset` or `none`. `datasetKey` is the
serialization name of the item field marked with `key_field()` for a `Dataset[Item]` output, else
`null`. `warnings` is a list of issues (see below). A missing return annotation adds
`return_annotation_missing`: no output is assumed. Annotate the function to make its intent clear.
For datasets, item fields live at `outputSchema.properties.items.items.properties`.
The script runs when it is imported; what it prints goes to stderr, so stdout holds exactly the
JSON document (output written straight to file descriptor 1 is not intercepted).

**What `inspect` reports.** A script that imports and has a valid entry point is accepted; a
script that does not compile is a `syntax_error`. When the import or the entry point fails,
`inspect` also reads the source as Python syntax (it does not run any code) to explain the usual
newcomer mistakes more precisely, with line numbers and every finding at once. Each issue has a
code:

<!-- inspect-codes:start -->
- `syntax_error`: the file does not compile; the issue has the line.
- `import_wrong_module`: `script` (or `Context` from a `mobilerun*` module) is imported from a
  module other than `mobilerun_functions`, for example `from mobilerun import script`.
- `import_missing`: a function is decorated with `@script` but `script` is not imported from
  `mobilerun_functions`.
- `no_entry_point`: no function is marked with `@script`.
- `entry_point_invalid`: two functions are marked with `@script`, or the signature does not fit;
  unresolved annotations such as a missing `Context` import get the repair hint.
- `import_failed`: the script cannot be read or raises while it is imported. A `NameError` for
  `script` or `Context`, or a missing `mobilerun`, `mobilerun_sandbox` or `mobilerun_sdk` module,
  gets the same repair hint as the static checks.
- `schema_unsupported`: a model uses something outside the supported JSON Schema subset, or a
  `Dataset` item model does not have exactly one key field that serialises to a string or an
  integer.
<!-- inspect-codes:end -->

**Failure.** `inspect` exits 3 and prints one JSON document on stdout:

```json
{"ok": false,
 "error": {"code": "import_missing", "message": "...", "line": 9,
           "repair": {"action": "fix_script", "hint": "Add `from mobilerun_functions import ..."}},
 "errors": ["... every issue, in line order, the first one equal to `error` ..."],
 "warnings": []}
```

An issue is `{"code", "message", "line"?, "repair"?}`. `line` is set when the problem has a
place in the script; `repair` is `{"action": "fix_script", "hint"}`, where the hint names the
correct import and a one-line example. `error` is always the first entry of `errors`, so callers
that only read `error.code` and `error.message` keep working. stderr gets one line per error:
`line N: message — hint`.

The schemas are limited to a subset the platform can validate. `pattern`, `patternProperties`,
`prefixItems`, `discriminator` and recursive models are rejected, and so is any `format` outside this list:

`date-time`, `date`, `uuid`, `uri`, `email`, `mobilerun-mailbox`, `mobilerun-proxy`

That covers `datetime`, `date`, `UUID`, `AnyUrl`/`HttpUrl` and, for `email`, a `str` field with
`Field(json_schema_extra={"format": "email"})`. `run` still validates the input with the
Pydantic model, so a `datetime` field receives a parsed value.

### `run`

`run PATH --input-file PATH --result-file PATH` validates the same contract as `inspect`
before reading input. Contract violations exit 3, write nothing to stdout and
print one line per issue on stderr in the same format as `inspect`, and write no result file.

An exception raised while importing the script instead makes `run` write an error envelope
and exit 1 (`script_error` for an unexpected exception). `inspect` reports the same unexpected
exception as `import_failed` and exits 3.

| Flag | Meaning / default |
| --- | --- |
| `--input-file` | Required input JSON file. |
| `--result-file` | Required envelope output path (written atomically). |
| `--state-file` | Prior state file, may not exist on the first run. |
| `--dataset-file` | Incremental JSON Lines output (truncated at start). |
| `--run-id` | Run identifier; `local`. |
| `--attempt` | Attempt number; `1`. |
| `--mode` | `live`, `dry_run`, `verification`; `live`. |
| `--deadline` | ISO datetime, informational; absent by default. |
| `--credentials-file` | Private local credentials YAML file. |
| `--cloud` | Resolve bindings in the cloud with your API key. |
| `--device-id` | Cloud UUID or local ADB serial. |
| `--device-target` | `cloud` (default) or `local`. |
| `--device-platform` | Local `android` (default) or `ios`; only with local target. |
| `--device-url` | Local Portal HTTP URL; only with local target. |

Every failed run with exit 1 or 2 writes one summary line `error CODE: MESSAGE` to stderr,
including field paths and error types for validation failures. The result file remains the
machine-readable source of the full envelope.

## Platform contract

Exit codes: `0` success; `1` the run failed (error envelope); `2` invalid input; `3` no valid
entry point (no result file); `64` runner fault such as bad flags or an unreadable file (no
result file). The envelope is
`{"ok": true, "outcome": "succeeded" | "partial", "result": ...}` (a partial one also has
`partialReason`; with `ctx.state.save` it has `state`) or
`{"ok": false, "error": {"code", "message", "retryable", "details"?}}`.
Validation `error.details` lists field paths and error types
only (`{"path": "items.0.name", "message": "string_type"}`), never input values.

Both envelopes also carry `"input"`: the input the entry point received, after validation and
defaults (`model_dump(mode="json", by_alias=True)` of the `Input` model, so a field left out of
the input file shows its default, and keys are the names `inputSchema` lists). It is there
whenever the input validated: also for a failed or cancelled run, never for `input_invalid` and
never when there is no result file. It can contain the values the platform passed in.
`input` is omitted when it is larger than 64 KiB or not representable as JSON; the run itself is
not affected.

Result keys are the serialization names of the output model (`serialization_alias`, or `alias`),
exactly the property names `outputSchema` lists.

**Error codes.** A script raises `ScriptError(code, message, retryable=False)`. The code must
match `^[a-z][a-z0-9_]{0,63}$` and must not be one of the codes the runner uses itself;
otherwise the envelope code is `script_error` (message and `retryable` are kept). The check
happens when the envelope is built and writes a warning to stderr for an invalid or reserved code.

<!-- reserved-codes:start -->
Reserved codes: `script_error` (an unexpected exception), `input_invalid` (exit 2; field paths
and error types only), `output_invalid` (the return value or a pushed item does not match its
model), `result_too_large`, `state_too_large`, `dataset_too_large`, `cancelled` (SIGTERM).
<!-- reserved-codes:end -->

`script_error` messages contain `str(exc)` and may include input values. The platform scrubs
them; this package does not.

**Limits.**

- `error.message` and `partialReason` are cut to 500 characters (`ERROR_MESSAGE_MAX_CHARS`).
- `result` for value outputs: 524288 bytes of compact JSON (`RESULT_MAX_BYTES`).
- `state`: 524288 bytes (`STATE_MAX_BYTES`).
- dataset (buffer or file): 524288 bytes (`DATASET_MAX_BYTES`).

### Cancellation

On SIGTERM, `run` stops the entry point (sync or async) and the outcome is `cancelled`, even if
the script swallows the interruption or fails while cleaning up. It writes
`{"ok": false, "error": {"code": "cancelled", "message": "terminated by signal", "retryable":
false}}` (plus the `dataset` counts when a dataset file is in use) and exits 1. Lines already
pushed stay on disk; the file only ever holds complete lines, also after an interrupted write.
`push` is safe to call from several threads. The deadline is informational; the platform
enforces it. Resource bindings apply their own OTP and task timeouts; the runner itself has no deadline retry logic.

## Development

Develop with `uv sync --frozen && uv run ruff check && uv run pytest`.
