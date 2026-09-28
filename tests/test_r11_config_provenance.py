from __future__ import annotations

import json
from pathlib import Path

from arc_cli.cli import main, parser
from arc_cli.completion import completion_candidates
from arc_cli.config import (
    CONFIG_INSPECTION_SCHEMA,
    config_inspection_payload,
    load_config_result,
    resolve_config_value,
)
from arc_cli.doctor import collect_doctor_report
from arc_cli.machine import load_schema, schema_names


def _write_config(root: Path, text: str) -> Path:
    path = root / "arc" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_invalid_toml_is_retained_as_diagnostic_instead_of_empty_success(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[ui\nprogress = 'never'\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config_result()
    assert result.exists is True
    assert result.valid is False
    assert result.data == {}
    assert result.issues[0].code == "toml_decode"
    assert "invalid TOML" in result.issues[0].message


def test_runtime_fails_closed_on_invalid_config_but_config_show_remains_diagnostic(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, "[create]\nlevel = 99\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert main(["formats", "--json"]) == 2
    assert "create.level must be between 0 and 9" in capsys.readouterr().err

    assert main(["config", "show", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == CONFIG_INSPECTION_SCHEMA
    assert payload["config"]["valid"] is False
    assert payload["config"]["issues"][0]["key"] == "create.level"


def test_unknown_configuration_is_visible_as_warning_and_doctor_warns(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[ui]\nprogress = 'never'\nmystery = true\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config_result()
    assert result.valid is True
    assert any(issue.code == "unknown_key" and issue.key == "ui.mystery" for issue in result.issues)
    doctor = collect_doctor_report()
    config_check = next(item for item in doctor["checks"] if item["id"] == "config")
    assert config_check["status"] == "warn"
    assert "ui.mystery" in config_check["detail"]


def test_explain_precedence_records_all_five_layers(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, """
[create]
level = 4
[profiles.backup]
level = 7
""")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "6")
    result = load_config_result()
    resolved = resolve_config_value(
        "create.level",
        result.data,
        profile="backup",
        cli={"level": "9"},
    )
    assert resolved.value == 9
    assert resolved.source == "cli"
    assert [(row.source, row.value, row.selected) for row in resolved.layers] == [
        ("builtin", None, False),
        ("config", 4, False),
        ("environment", 6, False),
        ("profile", 7, False),
        ("cli", 9, True),
    ]


def test_profile_is_selected_after_environment_but_before_cli(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, """
[ui]
progress = "auto"
[profiles.ci]
progress = "never"
""")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_PROGRESS", "always")
    result = load_config_result()
    assert resolve_config_value("ui.progress", result.data, profile="ci").value == "never"
    assert resolve_config_value("ui.progress", result.data, profile="ci", cli={"progress": "always"}).source == "cli"


def test_backend_preference_provenance_includes_environment_override(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[backends]\nzstd = ['zstd']\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_BACKEND_ZSTD", "pzstd,zstd")
    result = load_config_result()
    resolved = resolve_config_value("backends.zstd", result.data)
    assert resolved.value == ["pzstd", "zstd"]
    assert resolved.source == "environment"
    assert [layer.source for layer in resolved.layers] == ["builtin", "config", "environment"]


def test_config_show_effective_json_exposes_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, "[ui]\nprogress = 'never'\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "show", "--effective", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == CONFIG_INSPECTION_SCHEMA
    assert payload["effective"]["ui.progress"]["value"] == "never"
    assert payload["effective"]["ui.progress"]["source"] == "config"
    assert payload["effective"]["ui.native_command_style"]["source"] == "builtin"


def test_config_explain_cli_surface_models_explicit_cli_layer(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, "[create]\nthreads = 2\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "explain", "create.threads", "--cli", "threads=8", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolution"]["value"] == 8
    assert payload["resolution"]["source"] == "cli"
    assert payload["resolution"]["layers"][-1]["detail"] == "--threads"


def test_config_profile_inspection_includes_raw_profile_and_effective_provenance(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, """
[create]
level = 3
[profiles.backup]
level = 8
threads = 4
exclude = [".git/"]
""")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "profile", "backup", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_profile"]["exclude"] == [".git/"]
    assert payload["effective"]["create.level"]["value"] == 8
    assert payload["effective"]["create.level"]["source"] == "profile"
    assert payload["effective"]["create.threads"]["value"] == 4


def test_unknown_explain_key_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "explain", "does.not.exist", "--json"]) == 2
    assert "unknown configuration key" in capsys.readouterr().err


def test_config_json_v1_is_wrapped_in_machine_envelope(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["config", "show", "--effective", "--json=v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.machine/v1"
    assert payload["operation"] == "config"
    assert payload["result"]["schema"] == CONFIG_INSPECTION_SCHEMA


def test_config_schema_is_bundled_and_queryable(capsys) -> None:
    assert "config-inspection-v1" in schema_names()
    schema = load_schema("config-inspection-v1")
    assert schema["$id"] == CONFIG_INSPECTION_SCHEMA
    assert main(["schema", "config-inspection-v1"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"] == CONFIG_INSPECTION_SCHEMA


def test_config_parser_and_completion_expose_inspection_surface(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[profiles.backup]\nlevel = 7\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    args = parser().parse_args(["config", "show", "--effective"])
    assert args.command == "config" and args.config_action == "show" and args.effective
    assert "show" in completion_candidates(["config", ""])
    assert "create.level" in completion_candidates(["config", "explain", "create."])
    assert "backup" in completion_candidates(["config", "profile", "b"])


def test_config_inspection_payload_is_structured_for_machine_consumers(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[ui]\nprogress = 'never'\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config_result()
    payload = config_inspection_payload(result, key="ui.progress")
    assert payload["schema"] == CONFIG_INSPECTION_SCHEMA
    assert payload["resolution"]["key"] == "ui.progress"
    assert payload["resolution"]["layers"][0]["source"] == "builtin"


def test_doctor_finishes_when_invalid_table_would_break_runtime_consumers(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, 'backends = "not-a-table"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    report = collect_doctor_report()
    config_check = next(item for item in report["checks"] if item["id"] == "config")
    assert config_check["status"] == "fail"
    assert any(item["id"] == "backends" for item in report["checks"])


def test_schema_and_help_remain_available_to_diagnose_invalid_config(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, "[create]\nlevel = 99\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["schema", "config-inspection-v1"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"] == CONFIG_INSPECTION_SCHEMA
    assert main(["help", "config", "--plain"]) == 0
    assert "configuration" in capsys.readouterr().out.lower()
