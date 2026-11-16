"""Shared script module name and limits for routine inputs, outputs and errors."""

MODULE_NAME = "mobilerun_user_script"

RESULT_MAX_BYTES = 524288
STATE_MAX_BYTES = 524288
DATASET_MAX_BYTES = 524288
# Cap for `error.message` of every failure and for the reason of a partial result.
ERROR_MESSAGE_MAX_CHARS = 500
# `input` is left out above this size (compact JSON), so it never crowds out the result.
INPUT_ECHO_MAX_BYTES = 65536
MAX_ERROR_DETAILS = 50
