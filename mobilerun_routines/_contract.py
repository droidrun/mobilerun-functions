"""The script contract shared by inspect and run."""

from __future__ import annotations

from typing import Any

from ._check import RETURN_ANNOTATION_MISSING, Issue
from ._version import SDK_VERSION
from .entry import EntryPoint
from .schema import dataset_key, input_schema, output_schema


def inspect_entry(entry: EntryPoint) -> dict[str, Any]:
    """Inspect the contract, raising SchemaError on unsupported schemas or dataset keys.

    Run calls this for the checks alone; inspect publishes the returned document.
    """
    warnings = []
    if entry.return_annotation_missing:
        warnings.append(
            Issue(
                RETURN_ANNOTATION_MISSING, "annotate the return type; no output is assumed"
            ).as_json()
        )
    output = output_schema(entry.output_model) if entry.output_model else None
    return {
        "ok": True,
        "entrypoint": entry.name,
        "async": entry.is_async,
        "inputSchema": input_schema(entry.input_model),
        "outputSchema": output,
        "outputKind": entry.output_kind,
        "datasetKey": dataset_key(output)
        if entry.output_kind == "dataset" and output is not None
        else None,
        "sdkVersion": SDK_VERSION,
        "warnings": warnings,
    }
