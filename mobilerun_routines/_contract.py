"""The script contract shared by inspect and run."""

from typing import Any

from ._check import Issue
from ._version import SDK_VERSION
from .entry import EntryPoint
from .schema import dataset_key, input_schema, output_schema


def inspect_entry(entry: EntryPoint) -> dict[str, Any]:
    output = output_schema(entry.output_model) if entry.output_model else None
    return {
        "ok": True,
        "entrypoint": entry.name,
        "async": entry.is_async,
        "inputSchema": input_schema(entry.input_model),
        "outputSchema": output,
        "outputKind": entry.output_kind,
        "datasetKey": dataset_key(output) if entry.output_kind == "dataset" else None,
        "sdkVersion": SDK_VERSION,
        "warnings": [
            Issue("return_annotation_missing", "annotate the return type; no output is assumed").as_json()
        ] if entry.return_annotation_missing else [],
    }
