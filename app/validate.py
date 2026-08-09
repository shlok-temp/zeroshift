"""Validate generated documents against the official Zerops JSON Schemas."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
MAX_REPORTED_ERRORS = 20

# Fields where the published schema disagrees with the live deploy API. The
# schema and the docs both type these as integer seconds, but the API unmarshals
# them as Go durations and rejects a bare number:
#   "line 18: cannot unmarshal !!int `60` into time.Duration"
# Verified against the deploy endpoint on 2026-08-09. We emit what the API
# accepts and suppress the resulting schema complaint, rather than emit YAML we
# already know will be rejected.
DURATION_FIELDS = frozenset({
    "failureTimeout",
    "disconnectTimeout",
    "recoveryTimeout",
    "execPeriod",
    "retryPeriod",
})


@lru_cache(maxsize=4)
def load_schema(name: str) -> dict[str, Any] | None:
    path = SCHEMA_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_known_schema_deviation(error: Any) -> bool:
    field = error.path[-1] if error.path else None
    return error.validator == "type" and field in DURATION_FIELDS


def validate_against(document: dict[str, Any], schema_name: str) -> list[str]:
    schema = load_schema(schema_name)
    if schema is None:
        return []
    validator = Draft7Validator(schema)
    messages = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(e.path)):
        if is_known_schema_deviation(error):
            continue
        location = "/".join(str(part) for part in error.path) or "(root)"
        messages.append(f"{location}: {error.message}")
    return messages[:MAX_REPORTED_ERRORS]


def validate_import(document: dict[str, Any]) -> list[str]:
    return validate_against(document, "import-schema.json")


def validate_zerops_yaml(document: dict[str, Any]) -> list[str]:
    return validate_against(document, "zerops-yml-schema.json")


def schemas_available() -> bool:
    return load_schema("import-schema.json") is not None


@lru_cache(maxsize=1)
def valid_build_bases() -> frozenset[str]:
    """Bases Zerops accepts under build.base.

    Read from the schema rather than hardcoded: docker, nginx and static types are
    legal under run.base but cannot be built, and that distinction is invisible
    until a pipeline fails.
    """
    schema = load_schema("zerops-yml-schema.json")
    if not schema:
        return frozenset()
    try:
        node = schema["properties"]["zerops"]["items"]["properties"]["build"]["properties"]["base"]
    except (KeyError, TypeError):
        return frozenset()

    found: set[str] = set()
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if isinstance(current.get("enum"), list):
                found.update(str(value) for value in current["enum"])
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return frozenset(found)


def is_buildable(base: str) -> bool:
    bases = valid_build_bases()
    return base in bases if bases else True
