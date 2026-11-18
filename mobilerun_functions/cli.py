"""Command line: `python -m mobilerun_functions new|inspect|run`."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn, get_args

from ._check import Issue, issues_for
from ._contract import inspect_entry
from ._template import STARTER_SCRIPT
from .context import DevicePlatform, DeviceTarget, RunInfo, RunMode
from .errors import EntryPointError
from .loader import ScriptImportError, load_script
from .runner import (
    EXIT_NO_ENTRY_POINT,
    EXIT_OK,
    EXIT_RUNNER_FAULT,
    RunnerFault,
    RunOptions,
    run,
)
from .schema import SchemaError

MODES = get_args(RunMode)
DEVICE_TARGETS = get_args(DeviceTarget)
DEVICE_PLATFORMS = get_args(DevicePlatform)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse exits 2 by default, which `run` uses for invalid input.
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(EXIT_RUNNER_FAULT)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m mobilerun_functions")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    new_p = sub.add_parser("new", help="write a starter script")
    new_p.add_argument("path")

    inspect_p = sub.add_parser("inspect", help="print entry point and JSON Schemas")
    inspect_p.add_argument("path")

    run_p = sub.add_parser("run", help="validate input, call the entry point, write the result")
    run_p.add_argument("path")
    run_p.add_argument("--input-file", required=True)
    run_p.add_argument("--result-file", required=True)
    run_p.add_argument("--state-file")
    run_p.add_argument("--dataset-file")
    run_p.add_argument("--run-id", default="local")
    run_p.add_argument("--attempt", type=int, default=1)
    run_p.add_argument("--mode", choices=MODES, default="live")
    run_p.add_argument("--device-id", help="cloud device UUID, or ADB serial with local target")
    run_p.add_argument(
        "--device-target",
        choices=DEVICE_TARGETS,
        default="cloud",
        help="where the device lives (default: cloud)",
    )
    run_p.add_argument(
        "--device-platform",
        choices=DEVICE_PLATFORMS,
        help="local device platform (default: android); ios uses its Portal",
    )
    run_p.add_argument("--device-url", help="Portal HTTP URL of a local device")
    run_p.add_argument("--deadline", type=datetime.fromisoformat)
    return parser


def _new(path: str) -> int:
    try:
        # "x" never overwrites and creates no directories.
        with open(path, "x", encoding="utf-8") as file:
            file.write(STARTER_SCRIPT)
    except FileExistsError:
        sys.stderr.write(f"{path} already exists\n")
        return EXIT_RUNNER_FAULT
    except OSError as exc:
        sys.stderr.write(f"cannot write {path}: {exc}\n")
        return EXIT_RUNNER_FAULT
    sys.stdout.write(path + "\n")
    return EXIT_OK


def _inspect(path: str) -> int:
    try:
        source = Path(path).read_bytes()
    except OSError as exc:
        return _reject(issues_for(exc, b"", path))
    try:
        # The script runs at import: what it prints must not end up in the JSON on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            entry = load_script(path)
            document = inspect_entry(entry)
    except (OSError, EntryPointError, ScriptImportError, SchemaError) as exc:
        return _reject(issues_for(exc, source, path))
    sys.stdout.write(json.dumps(document) + "\n")
    return EXIT_OK


def _reject(errors: list[Issue], *, json_output: bool = True) -> int:
    """Report issues on stderr and, for inspect, a JSON failure document on stdout.

    `error` is the first of `errors`, the shape callers read before `errors` existed.
    """
    document = {
        "ok": False,
        "error": errors[0].as_json(),
        "errors": [issue.as_json() for issue in errors],
        "warnings": [],
    }
    if json_output:
        sys.stdout.write(json.dumps(document) + "\n")
    for issue in errors:
        sys.stderr.write(issue.describe() + "\n")
    return EXIT_NO_ENTRY_POINT


def _invalid_device_flags(args: argparse.Namespace) -> bool:
    return args.device_target != "local" and bool(args.device_platform or args.device_url)


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if _invalid_device_flags(args):
        parser.error("--device-platform and --device-url need --device-target local")
    info = RunInfo(
        run_id=args.run_id,
        attempt=args.attempt,
        deadline=args.deadline,
        mode=args.mode,
        device_id=args.device_id,
        device_target=args.device_target,
        device_platform=args.device_platform,
        device_url=args.device_url,
    )
    opts = RunOptions(
        path=args.path,
        input_file=args.input_file,
        result_file=args.result_file,
        run_info=info,
        state_file=args.state_file,
        dataset_file=args.dataset_file,
    )
    try:
        return run(opts)
    except (EntryPointError, SchemaError) as exc:
        return _reject(issues_for(exc, Path(args.path).read_bytes(), args.path), json_output=False)
    except RunnerFault as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_RUNNER_FAULT


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "new":
        return _new(args.path)
    if args.command == "inspect":
        return _inspect(args.path)
    return _run(args, parser)
