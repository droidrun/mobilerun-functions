"""`Dataset`, `ctx.dataset.push` and the JSON Lines file behind it."""

from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ._constants import DATASET_MAX_BYTES as DATASET_MAX_BYTES
from ._io import dumps_json
from ._validate import revalidate, validation_details
from .errors import DatasetItemInvalidError, DatasetTooLargeError, NotAvailableError

KEY_MARKER = "x-mobilerun-key"

ItemT = TypeVar("ItemT")


class Dataset(BaseModel, Generic[ItemT]):
    """Return `Dataset[Item]` to publish a list of items the platform can diff.

    An incomplete list is reported with `ctx.partial(reason)`, not with a field here.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]


def key_field(**kwargs: Any) -> Any:
    """`Field(...)` that marks the field identifying a dataset item between runs.

    Exactly one field of an item model carries it, and it must serialise to a string or an
    integer. Other arguments go to `Field` unchanged; a `json_schema_extra` dict is kept next to
    the marker.
    """
    extra = kwargs.pop("json_schema_extra", None) or {}
    return Field(json_schema_extra={**extra, KEY_MARKER: True}, **kwargs)


class DatasetFile:
    """The JSON Lines file or in-memory buffer of one run, created empty at start.

    Only complete lines survive.

    One lock covers the size check, the write and the accounting, so threads cannot exceed the
    limit together or interleave lines. `finalise` cuts the file back to the last complete line.
    """

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.path = Path(path) if path is not None else None
        self._fh: io.BytesIO | io.FileIO
        if self.path is None:
            self._fh = io.BytesIO()
        else:
            self._fh = open(self.path, "wb", buffering=0)  # noqa: SIM115 - closed in `close`
        self._lock = threading.Lock()
        self._items = 0
        self._bytes = 0  # offset of the end of the last complete line

    def append(self, text: str) -> None:
        """Append `text` (one JSON document without newline) as a line; flushed at once."""
        data = text.encode("utf-8") + b"\n"
        with self._lock:
            if self._bytes + len(data) > DATASET_MAX_BYTES:
                raise DatasetTooLargeError(f"the dataset would exceed {DATASET_MAX_BYTES} bytes")
            try:
                view = memoryview(data)
                while view:
                    view = view[self._fh.write(view) :]
            except BaseException:
                self._cut_to_last_line()
                raise
            self._items += 1
            self._bytes += len(data)

    def _cut_to_last_line(self) -> None:
        self._fh.truncate(self._bytes)
        self._fh.seek(self._bytes)

    def finalise(self) -> None:
        """Drop anything after the last complete line (a write that was interrupted)."""
        with self._lock:
            self._cut_to_last_line()

    def stats(self) -> dict[str, int]:
        """Complete lines and their bytes."""
        with self._lock:
            return {"items": self._items, "bytes": self._bytes}

    def buffered_items(self) -> list[Any]:
        with self._lock:
            assert isinstance(self._fh, io.BytesIO)
            return [json.loads(line) for line in self._fh.getvalue().splitlines()]

    def close(self) -> None:
        self._fh.close()


class DatasetSink:
    """`ctx.dataset`: push items to a file or in-memory buffer as the script finds them.

    `item_type` is the `Item` of the entry point's `Dataset[Item]` return annotation, or `None`
    when the entry point does not return a dataset.
    """

    def __init__(self, file: DatasetFile | None = None, item_type: Any = None) -> None:
        self._file = file if file is not None else DatasetFile(None)
        self._adapter: TypeAdapter[Any] | None = (
            None if item_type is None else TypeAdapter(item_type)
        )
        self._item_type = item_type

    def finish(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        """Append returned items after pushed items; return buffered data, or None for a file."""
        if result is not None:
            for item in result["items"]:
                self._file.append(dumps_json(item))
        if self._file.path is None:
            return {"items": self._file.buffered_items()}
        return None

    def push(self, item: BaseModel | dict[str, Any]) -> None:
        """Validate `item` against the dataset's item model and append it as one line.

        Raises `DatasetItemInvalidError` for an invalid item and `DatasetTooLargeError` when the
        line would take the file over `DATASET_MAX_BYTES`; earlier lines stay.
        """
        if self._adapter is None:
            raise NotAvailableError(
                "dataset.push", "is only available when the entry point returns Dataset[Item]"
            )
        try:
            validated = revalidate(self._adapter, item)
        except ValidationError as exc:
            raise DatasetItemInvalidError(
                "the pushed item does not match the dataset item model",
                validation_details(exc, self._item_type),
            ) from None
        self._file.append(self._adapter.dump_json(validated, by_alias=True).decode())
