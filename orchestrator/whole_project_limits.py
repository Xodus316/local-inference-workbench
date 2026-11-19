"""Source bounds shared by whole-project orchestration and its OpenCode workers."""

from .repository import MAX_FILE


LEGACY_SOURCE_BYTES = 128 * 1024
MAX_SOURCE_FILES = 128
MAX_SOURCE_BYTES = MAX_SOURCE_FILES * MAX_FILE
DEFAULT_SOURCE_BYTES = MAX_SOURCE_BYTES
WHOLE_PROJECT_TEST_BYTES = 64 * 1024
WHOLE_PROJECT_COMPLETION_TOKENS = 64 * 1024


def validate_source_bytes(value):
    if type(value) is not int or not 1 <= value <= MAX_SOURCE_BYTES:
        raise ValueError(f"Whole-project source bytes must be an integer from 1 to {MAX_SOURCE_BYTES}")
    return value
