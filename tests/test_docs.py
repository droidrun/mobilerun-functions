"""The README agrees with the code."""

import json
import re
from pathlib import Path

from mobilerun_functions import (
    RESERVED_ERROR_CODES,
)
from mobilerun_functions._check import INSPECT_ERROR_CODES

ROOT = Path(__file__).parent.parent
README = (ROOT / "README.md").read_text()


def _marked(name: str) -> str:
    match = re.search(rf"<!-- {name}:start -->(.*?)<!-- {name}:end -->", README, re.S)
    assert match, f"README has no {name} block"
    return match.group(1)


def test_readme_lists_exactly_the_reserved_codes():
    assert set(re.findall(r"`([a-z_]+)`", _marked("reserved-codes"))) == set(RESERVED_ERROR_CODES)


def test_readme_lists_exactly_the_inspect_codes():
    block = _marked("inspect-codes")
    assert set(re.findall(r"^- `([a-z_]+)`", block, re.M)) == set(INSPECT_ERROR_CODES)


def test_the_script_format_example_is_a_valid_script(tmp_path, capsys):
    from mobilerun_functions import cli

    section = README.split("## Hello")[1].split("## Test a script")[0]
    example = re.search(r"```python\n(.*?)```", section, re.S)
    assert example
    path = tmp_path / "format.py"
    path.write_text(example.group(1))
    assert cli.main(["inspect", str(path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["warnings"] == [] and doc["outputKind"] == "value"


def test_the_readme_sample_is_a_valid_script(tmp_path, capsys):
    from mobilerun_functions import cli

    section = README.split("### Dataset example")[1].split("## Context reference")[0]
    sample = re.search(r"```python\n(.*?)```", section, re.S)
    assert sample
    path = tmp_path / "sample.py"
    path.write_text(sample.group(1))
    assert cli.main(["inspect", str(path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["datasetKey"] == "sku" and doc["async"] is True
    assert doc["inputSchema"]["properties"]["since"]["format"] == "date"
    assert set(doc["outputSchema"]["properties"]["items"]["items"]["properties"]) == {
        "sku",
        "name",
        "unitPrice",
    }


def test_readme_section_order():
    headings = re.findall(r"^## (.+)$", README, re.M)
    assert headings == [
        "Install",
        "Hello",
        "Test a script",
        "State between runs",
        "Datasets",
        "Context reference",
        "Commands reference",
        "Platform contract",
        "Development",
    ]


def test_readme_state_example_runs_twice(run_script):
    section = README.split("## State between runs")[1].split("## Datasets")[0]
    source = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    first = run_script(source, state=None)
    second = run_script(source, state=first.envelope["state"])
    assert first.envelope["result"] == {"count": 1}
    assert second.envelope["result"] == {"count": 2}
