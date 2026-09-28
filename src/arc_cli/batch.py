from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .command_docs import COMMAND_DOCS
from .errors import UsageError

BATCH_SCHEMA_ID = "arc.batch-input/v1"
BATCH_SCHEMA_VERSION = 1
MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_OPERATIONS = 1024
MAX_ARGV = 4096
_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SECRET_FLAGS = {"--password", "--source-password", "--left-password", "--right-password"}


@dataclass(frozen=True, slots=True)
class BatchOperation:
    id: str
    argv: tuple[str, ...]
    allow_failure: bool = False


@dataclass(frozen=True, slots=True)
class BatchRequest:
    operations: tuple[BatchOperation, ...]
    on_error: str = "stop"


def _unknown_keys(value: dict[str, Any], allowed: set[str], *, where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise UsageError(f"{where} contains unknown field(s): {', '.join(unknown)}")


def validate_batch_payload(payload: Any) -> BatchRequest:
    if not isinstance(payload, dict):
        raise UsageError("batch input must be a JSON object")
    _unknown_keys(payload, {"schema", "on_error", "operations"}, where="batch input")
    if payload.get("schema") != BATCH_SCHEMA_ID:
        raise UsageError(f"batch input schema must be {BATCH_SCHEMA_ID!r}")
    on_error = payload.get("on_error", "stop")
    if on_error not in {"stop", "continue"}:
        raise UsageError("batch input on_error must be 'stop' or 'continue'")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise UsageError("batch input operations must be a non-empty JSON array")
    if len(raw_operations) > MAX_OPERATIONS:
        raise UsageError(f"batch input exceeds the {MAX_OPERATIONS}-operation limit")

    operations: list[BatchOperation] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_operations):
        where = f"batch operation {index}"
        if not isinstance(raw, dict):
            raise UsageError(f"{where} must be a JSON object")
        _unknown_keys(raw, {"id", "argv", "allow_failure"}, where=where)
        op_id = raw.get("id")
        if not isinstance(op_id, str) or not op_id or len(op_id) > 128 or not _ID.fullmatch(op_id):
            raise UsageError(f"{where} id must match [A-Za-z0-9._-]+ and be at most 128 characters")
        if op_id in seen:
            raise UsageError(f"duplicate batch operation id: {op_id}")
        seen.add(op_id)
        argv = raw.get("argv")
        if not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV or not all(isinstance(item, str) for item in argv):
            raise UsageError(f"{where} argv must be a non-empty JSON string array with at most {MAX_ARGV} entries")
        if any("\x00" in item for item in argv):
            raise UsageError(f"{where} argv must not contain NUL bytes")
        if argv[0] not in COMMAND_DOCS:
            raise UsageError(f"{where} argv starts with unknown Arc command: {argv[0]!r}")
        if argv[0] == "batch":
            raise UsageError("batch recursion is not allowed")
        if "extract" == argv[0] and "--stdout" in argv:
            raise UsageError("batch does not allow binary extract --stdout; write to a destination instead")
        allow_failure = raw.get("allow_failure", False)
        if not isinstance(allow_failure, bool):
            raise UsageError(f"{where} allow_failure must be boolean")
        operations.append(BatchOperation(op_id, tuple(argv), allow_failure))
    return BatchRequest(tuple(operations), str(on_error))


def load_batch_input(path: str, *, stdin_text: str | None = None) -> BatchRequest:
    if path == "-":
        if stdin_text is None:
            import sys
            text = sys.stdin.read(MAX_BATCH_BYTES + 1)
        else:
            text = stdin_text
    else:
        p = Path(path).expanduser()
        try:
            if p.stat().st_size > MAX_BATCH_BYTES:
                raise UsageError(f"batch input exceeds {MAX_BATCH_BYTES} bytes")
            text = p.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise UsageError(f"batch input {path!r} is not valid UTF-8: {exc}") from exc
        except OSError as exc:
            raise UsageError(f"cannot read batch input {path!r}: {exc}") from exc
    if len(text.encode("utf-8")) > MAX_BATCH_BYTES:
        raise UsageError(f"batch input exceeds {MAX_BATCH_BYTES} bytes")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"invalid batch JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc
    return validate_batch_payload(payload)


def redact_argv(argv: tuple[str, ...] | list[str]) -> list[str]:
    out: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            out.append("***")
            redact_next = False
            continue
        if token in _SECRET_FLAGS:
            out.append(token)
            redact_next = True
            continue
        replaced = False
        for flag in _SECRET_FLAGS:
            prefix = flag + "="
            if token.startswith(prefix):
                out.append(prefix + "***")
                replaced = True
                break
        if not replaced:
            out.append(token)
    return out



def _secret_values(argv: tuple[str, ...] | list[str]) -> set[str]:
    secrets: set[str] = set()
    for index, token in enumerate(argv):
        if token in _SECRET_FLAGS and index + 1 < len(argv):
            secrets.add(str(argv[index + 1]))
            continue
        for flag in _SECRET_FLAGS:
            prefix = flag + "="
            if token.startswith(prefix):
                secrets.add(token[len(prefix):])
    return {value for value in secrets if value}


def redact_text(text: str, argv: tuple[str, ...] | list[str]) -> str:
    value = text
    for secret in sorted(_secret_values(argv), key=len, reverse=True):
        value = value.replace(secret, "***")
    return value

def execute_batch(request: BatchRequest, runner: Callable[[list[str]], tuple[int, str, str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    failed = 0
    stopped = False
    for operation in request.operations:
        code, stdout, stderr = runner(list(operation.argv))
        stdout = redact_text(stdout, operation.argv)
        stderr = redact_text(stderr, operation.argv)
        interrupted = int(code) == 130
        allowed = code != 0 and operation.allow_failure and not interrupted
        if interrupted:
            status = "interrupted"
        elif code == 0:
            status = "ok"
        elif allowed:
            status = "allowed_failure"
        else:
            status = "failed"
            failed += 1
        parsed: Any = None
        stripped = stdout.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
        results.append({
            "id": operation.id,
            "argv": redact_argv(operation.argv),
            "status": status,
            "exit_code": int(code),
            "stdout": stdout,
            "stderr": stderr,
            "result": parsed,
        })
        if interrupted:
            stopped = True
            break
        if code != 0 and not operation.allow_failure and request.on_error == "stop":
            stopped = True
            break
    batch_interrupted = any(item["status"] == "interrupted" for item in results)
    return {
        "schema": "arc.batch-result/v1",
        "schema_version": 1,
        "status": "interrupted" if batch_interrupted else ("failed" if failed else "ok"),
        "on_error": request.on_error,
        "requested": len(request.operations),
        "executed": len(results),
        "failed": failed,
        "stopped": stopped,
        "operations": results,
    }
