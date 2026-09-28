from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc_cli.cli import _apply_defaults, _apply_profile, main, parser
from arc_cli.config import DEFAULT_BACKENDS, CONFIG_INSPECTION_SCHEMA, load_config_result, resolve_config_value
from arc_cli.doctor import collect_doctor_report
from arc_cli.machine import load_schema


def _write_config(root: Path, text: str) -> Path:
    path = root / "arc" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _assert_resolution_shape(value: object) -> None:
    assert isinstance(value, dict)
    assert set(value) == {"key", "value", "source", "layers"}
    assert value["source"] in {"builtin", "config", "environment", "profile", "cli"}
    layers = value["layers"]
    assert isinstance(layers, list) and layers
    selected = 0
    for layer in layers:
        assert isinstance(layer, dict)
        assert {"source", "key", "value", "selected"} <= set(layer)
        assert set(layer) <= {"source", "key", "value", "selected", "detail"}
        assert layer["source"] in {"builtin", "config", "environment", "profile", "cli"}
        selected += int(layer["selected"] is True)
    assert selected == 1


def _assert_config_payload_shape(payload: object, *, valid: bool) -> None:
    schema = load_schema("config-inspection-v1")
    assert schema["$id"] == CONFIG_INSPECTION_SCHEMA
    assert isinstance(payload, dict)
    assert payload["schema"] == CONFIG_INSPECTION_SCHEMA
    assert set(payload) <= set(schema["properties"])
    config = payload["config"]
    assert isinstance(config, dict)
    assert set(config) == {"path", "exists", "valid", "issues"}
    assert config["valid"] is valid
    for issue in config["issues"]:
        assert set(issue) == {"severity", "code", "key", "message"}
        assert issue["severity"] in {"warning", "error"}
    if "resolution" in payload:
        _assert_resolution_shape(payload["resolution"])
    for value in payload.get("effective", {}).values():
        _assert_resolution_shape(value)


def test_gate_valid_effective_payload_matches_bundled_schema_shape(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, "[create]\nlevel=4\n[profiles.backup]\nlevel=7\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "6")
    assert main(["config", "show", "--effective", "--profile", "backup", "--cli", "level=9", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    _assert_config_payload_shape(payload, valid=True)
    resolution = payload["effective"]["create.level"]
    assert resolution["value"] == 9
    assert resolution["source"] == "cli"
    assert [layer["source"] for layer in resolution["layers"]] == [
        "builtin", "config", "environment", "profile", "cli"
    ]


def test_gate_invalid_environment_payload_matches_schema_shape_without_effective_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "bogus")
    assert main(["config", "show", "--effective", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    _assert_config_payload_shape(payload, valid=False)
    assert "effective" not in payload
    assert any(issue["key"] == "environment.ARC_LEVEL" for issue in payload["config"]["issues"])


def test_gate_runtime_scalar_resolution_matches_config_explain_authority(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "payload"
    source.write_text("x", encoding="utf-8")
    _write_config(
        tmp_path,
        "[ui]\nprogress='never'\n[create]\nlevel=4\nthreads=2\n"
        "[profiles.backup]\nprogress='always'\nlevel=7\nthreads=3\n",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "6")
    result = load_config_result()
    assert result.valid

    args = parser().parse_args([
        "create", str(tmp_path / "out.tar"), str(source), "-tar", "--profile", "backup"
    ])
    _apply_profile(args, result.data)
    _apply_defaults(args, result.data)
    for key, attr in (
        ("ui.progress", "progress"),
        ("create.level", "level"),
        ("create.threads", "threads"),
    ):
        expected = resolve_config_value(key, result.data, profile="backup")
        assert getattr(args, attr) == expected.value


def test_gate_explicit_runtime_cli_still_wins_same_resolver(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "payload"
    source.write_text("x", encoding="utf-8")
    _write_config(tmp_path, "[create]\nlevel=4\n[profiles.backup]\nlevel=7\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "6")
    result = load_config_result()
    args = parser().parse_args([
        "create", str(tmp_path / "out.tar"), str(source), "-tar", "--profile", "backup", "--level", "9"
    ])
    _apply_profile(args, result.data)
    _apply_defaults(args, result.data)
    explained = resolve_config_value("create.level", result.data, profile="backup", cli={"level": args.level})
    assert args.level == explained.value == 9
    assert explained.source == "cli"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ARC_PROGRESS", "sometimes"),
        ("ARC_LEVEL", "bogus"),
        ("ARC_THREADS", "-1"),
        *[(f"ARC_BACKEND_{role.upper().replace('-', '_')}", " , ") for role in DEFAULT_BACKENDS],
    ],
)
def test_gate_every_supported_invalid_environment_keeps_doctor_diagnostic(
    tmp_path: Path, monkeypatch, name: str, value: str
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv(name, value)
    report = collect_doctor_report()
    config = next(row for row in report["checks"] if row["id"] == "config")
    backends = next(row for row in report["checks"] if row["id"] == "backends")
    assert config["status"] == "fail"
    assert name in str(config["detail"])
    assert backends["status"] in {"pass", "warn"}
    assert report["summary"]["fail"] >= 1
    assert report["healthy"] is False


def test_gate_unknown_warning_remains_nonfatal_but_visible_to_runtime_and_doctor(
    tmp_path: Path, monkeypatch
) -> None:
    _write_config(tmp_path, "[ui]\nprogress='never'\nmystery=true\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config_result()
    assert result.valid is True
    assert any(issue.code == "unknown_key" and issue.severity == "warning" for issue in result.issues)
    assert resolve_config_value("ui.progress", result.data).value == "never"
    doctor = collect_doctor_report()
    config = next(row for row in doctor["checks"] if row["id"] == "config")
    assert config["status"] == "warn"


def test_gate_r11_family_devtool_surfaces_exist() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    tests = {row["id"] for row in data.get("test", [])}
    for test_id in (
        "arc-r11-config-provenance",
        "arc-r11a-config-diagnostic-convergence",
        "arc-r11b-doctor-environment-isolation",
    ):
        assert test_id in tests
    workflows = data["targets"]["arc"]["workflows"]
    for name in ("r11", "r11a", "r11b"):
        assert name in workflows
