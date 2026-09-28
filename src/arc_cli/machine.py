from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from importlib import resources
from typing import Any

MACHINE_SCHEMA_VERSION = 1
MACHINE_SCHEMA_ID = "arc.machine/v1"


@dataclass(frozen=True, slots=True)
class MachineError:
    type: str
    message: str
    exit_code: int
    category: str = "arc"

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "message": self.message,
            "exit_code": self.exit_code,
            "category": self.category,
        }


def _redacted_argv(args) -> list[str]:
    argv = [str(value) for value in (getattr(args, "_display_argv", []) or [])]
    secret_names = ("password", "source_password", "left_password", "right_password")
    secrets = {
        str(getattr(args, name, None))
        for name in secret_names
        if getattr(args, name, None) and getattr(args, name, None) != "__PROMPT__"
    }
    out: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            out.append("***")
            redact_next = False
            continue
        if token in {"--password", "--source-password", "--left-password", "--right-password"}:
            out.append(token)
            redact_next = True
            continue
        matched_inline = False
        for option in ("--password", "--source-password", "--left-password", "--right-password"):
            if token.startswith(option + "="):
                out.append(option + "=***")
                matched_inline = True
                break
        if matched_inline:
            continue
        redacted = token
        for secret in secrets:
            if redacted == secret:
                redacted = "***"
            elif secret in redacted:
                redacted = redacted.replace(secret, "***")
        out.append(redacted)
    return out


def invocation_identity(args) -> dict[str, object]:
    program = str(getattr(args, "_invoked_program", "arc"))
    argv = _redacted_argv(args)
    command = str(getattr(args, "command", ""))
    return {
        "program": program,
        "literal_argv": argv,
        "literal": shlex.join([program, *argv]),
        "canonical_command": command,
    }


def redact_text(text: str, args) -> str:
    value = str(text)
    argv = [str(item) for item in (getattr(args, "_display_argv", []) or [])]
    secret_names = ("password", "source_password", "left_password", "right_password")
    secrets = {
        str(getattr(args, name, None))
        for name in secret_names
        if getattr(args, name, None) and getattr(args, name, None) != "__PROMPT__"
    }
    secret_options = {"--password", "--source-password", "--left-password", "--right-password"}
    for index, token in enumerate(argv):
        if token in secret_options and index + 1 < len(argv):
            secrets.add(argv[index + 1])
        elif any(token.startswith(option + "=") for option in secret_options):
            secrets.add(token.split("=", 1)[1])
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "***")
    return value


def diagnostic(kind: str, payload: Any, *, severity: str = "info") -> dict[str, object]:
    return {"kind": kind, "severity": severity, "payload": payload}


def envelope(
    args,
    *,
    result: Any = None,
    error: MachineError | None = None,
    diagnostics: list[dict[str, object]] | None = None,
    status: str | None = None,
) -> dict[str, object]:
    return {
        "schema": MACHINE_SCHEMA_ID,
        "schema_version": MACHINE_SCHEMA_VERSION,
        "kind": "error" if error is not None else "result",
        "status": status or ("error" if error is not None else "ok"),
        "operation": str(getattr(args, "command", "")),
        "invocation": invocation_identity(args),
        "result": result if error is None else None,
        "error": ({**error.to_dict(), "message": redact_text(error.message, args)} if error is not None else None),
        "diagnostics": list(diagnostics or []),
    }


def dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


SCHEMA_FILES = {
    "machine-v1": "machine-v1.schema.json",
    "backend-capability-v1": "backend-capability-v1.schema.json",
    "verification-evidence-v1": "verification-evidence-v1.schema.json",
    "logical-fingerprint-v1": "logical-fingerprint-v1.schema.json",
    "archive-diff-v1": "archive-diff-v1.schema.json",
    "remote-capability-v1": "remote-capability-v1.schema.json",
    "batch-input-v1": "batch-input-v1.schema.json",
}


def schema_names() -> tuple[str, ...]:
    return tuple(SCHEMA_FILES)


def load_schema(name: str) -> dict[str, object]:
    try:
        filename = SCHEMA_FILES[name]
    except KeyError as exc:
        raise KeyError(name) from exc
    path = resources.files("arc_cli").joinpath("schemas", filename)
    return json.loads(path.read_text(encoding="utf-8"))
