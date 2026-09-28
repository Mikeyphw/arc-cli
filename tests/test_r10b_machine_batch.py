from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from arc_cli.batch import BATCH_SCHEMA_ID, execute_batch, load_batch_input, validate_batch_payload
from arc_cli.cli import _preflight_batch_request, main
from arc_cli.errors import UsageError
from arc_cli.machine import load_schema, schema_names


def request(*operations, on_error="stop"):
    return {"schema": BATCH_SCHEMA_ID, "on_error": on_error, "operations": list(operations)}


def op(id, argv, **extra):
    return {"id": id, "argv": argv, **extra}


def test_batch_input_schema_is_bundled_and_queryable():
    assert "batch-input-v1" in schema_names()
    schema = load_schema("batch-input-v1")
    assert schema["$id"] == BATCH_SCHEMA_ID
    assert schema["properties"]["operations"]["maxItems"] == 1024


def test_batch_contract_requires_argv_arrays_unique_ids_and_known_commands():
    with pytest.raises(UsageError, match="non-empty JSON string array"):
        validate_batch_payload(request(op("one", "formats --json")))
    with pytest.raises(UsageError, match="duplicate batch operation id"):
        validate_batch_payload(request(op("one", ["formats"]), op("one", ["profiles"])))
    with pytest.raises(UsageError, match="unknown Arc command"):
        validate_batch_payload(request(op("one", ["not-an-arc-command"])))
    with pytest.raises(UsageError, match="recursion"):
        validate_batch_payload(request(op("one", ["batch", "jobs.json"])))
    with pytest.raises(UsageError, match="binary extract --stdout"):
        validate_batch_payload(request(op("one", ["extract", "a.zip", "--stdout"])))


def test_execute_batch_stop_and_continue_semantics():
    calls = []
    def runner(argv):
        calls.append(argv)
        code = 7 if argv[0] == "test" else 0
        return code, "", ""

    stop = execute_batch(validate_batch_payload(request(
        op("a", ["formats"]), op("b", ["test", "bad.zip"]), op("c", ["profiles"]),
    )), runner)
    assert stop["executed"] == 2 and stop["failed"] == 1 and stop["stopped"] is True

    calls.clear()
    cont = execute_batch(validate_batch_payload(request(
        op("a", ["test", "bad.zip"]), op("b", ["formats"]), on_error="continue",
    )), runner)
    assert cont["executed"] == 2 and cont["failed"] == 1 and cont["stopped"] is False


def test_allow_failure_does_not_fail_the_batch():
    payload = execute_batch(
        validate_batch_payload(request(op("probe", ["test", "missing.zip"], allow_failure=True))),
        lambda argv: (3, "", "expected failure"),
    )
    assert payload["status"] == "ok"
    assert payload["operations"][0]["status"] == "allowed_failure"


def test_batch_results_redact_passwords_in_argv_stdout_and_stderr():
    secret = "very-secret"
    payload = execute_batch(
        validate_batch_payload(request(op("x", ["test", "a.zip", f"--password={secret}"]))),
        lambda argv: (3, f"stdout {secret}", f"stderr {secret}"),
    )
    item = payload["operations"][0]
    combined = json.dumps(item)
    assert secret not in combined
    assert "--password=***" in combined


def test_cli_batch_validate_only_from_stdin(monkeypatch, capsys):
    raw = json.dumps(request(op("formats", ["formats", "--json"])))
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert main(["batch", "-", "--validate-only", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "on_error": "stop",
        "operations": 1,
        "schema": "arc.batch-validation/v1",
        "schema_version": 1,
        "status": "valid",
    }


def test_cli_batch_executes_heterogeneous_machine_operations(tmp_path: Path, capsys):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(request(
        op("formats", ["formats", "--json"]),
        op("schema", ["schema", "batch-input-v1"]),
    )), encoding="utf-8")
    assert main(["batch", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.batch-result/v1"
    assert payload["executed"] == 2
    assert [item["id"] for item in payload["operations"]] == ["formats", "schema"]
    assert payload["operations"][0]["result"] is not None
    assert payload["operations"][1]["result"]["$id"] == BATCH_SCHEMA_ID


def test_cli_batch_json_v1_wraps_batch_result(tmp_path: Path, capsys):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(request(op("formats", ["formats", "--json"]))), encoding="utf-8")
    assert main(["batch", str(path), "--json=v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.machine/v1"
    assert payload["operation"] == "batch"
    assert payload["result"]["schema"] == "arc.batch-result/v1"


def test_r10b_batch_contract_checker_passes():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "scripts/check_batch_contract.py"], cwd=root, check=True)


def test_batch_rejects_nul_argv_and_invalid_utf8_input(tmp_path: Path):
    with pytest.raises(UsageError, match="NUL bytes"):
        validate_batch_payload(request(op("bad", ["formats", "bad\x00arg"])))

    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(UsageError, match="not valid UTF-8"):
        load_batch_input(str(path))


def test_batch_preflights_all_nested_argv_before_execution():
    batch = validate_batch_payload(request(
        op("valid-first", ["formats", "--json"]),
        op("invalid-second", ["create"]),
    ))
    with pytest.raises(UsageError, match="invalid-second.*invalid Arc argv"):
        _preflight_batch_request(batch)


def test_batch_preflight_rejects_interactive_password_prompt():
    batch = validate_batch_payload(request(
        op("prompt", ["test", "archive.7z", "--password"]),
    ))
    with pytest.raises(UsageError, match="interactive password prompt"):
        _preflight_batch_request(batch)


def test_interrupt_always_stops_even_continue_and_allow_failure():
    calls = []
    batch = validate_batch_payload(request(
        op("interrupt", ["formats"], allow_failure=True),
        op("must-not-run", ["profiles"]),
        on_error="continue",
    ))
    def runner(argv):
        calls.append(argv)
        return (130, "", "interrupted") if len(calls) == 1 else (0, "", "")
    payload = execute_batch(batch, runner)
    assert payload["status"] == "interrupted"
    assert payload["stopped"] is True
    assert payload["executed"] == 1
    assert payload["operations"][0]["status"] == "interrupted"
    assert calls == [["formats"]]


def test_cli_validate_only_rejects_late_invalid_argv_without_running_batch(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(request(
        op("first", ["formats", "--json"]),
        op("late-bad", ["create"]),
    )), encoding="utf-8")
    called = False
    def should_not_execute(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("execute_batch must not run during failed preflight")
    monkeypatch.setattr("arc_cli.cli.execute_batch", should_not_execute)
    assert main(["batch", str(path), "--validate-only", "--json"]) != 0
    assert called is False
    assert "late-bad" in capsys.readouterr().err


def test_r10b_generated_completion_contract_passes():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "scripts/check_completion_contract.py"], cwd=root, check=True)
