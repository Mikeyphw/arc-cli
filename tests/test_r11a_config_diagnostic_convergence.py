from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc_cli.cli import main
from arc_cli.config import load_config_result, resolve_config_value
from arc_cli.doctor import collect_doctor_report


def _write_config(root: Path, text: str) -> Path:
    path = root / "arc" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _config_check() -> dict[str, object]:
    return next(item for item in collect_doctor_report()["checks"] if item["id"] == "config")


def test_invalid_supported_environment_is_typed_even_without_config_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_LEVEL", "bogus")
    result = load_config_result()
    assert result.exists is False
    assert result.valid is False
    issue = next(issue for issue in result.issues if issue.key == "environment.ARC_LEVEL")
    assert issue.code == "invalid_environment"
    assert "must be an integer" in issue.message


def test_invalid_environment_keeps_effective_json_diagnostic_and_doctor_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_PROGRESS", "sometimes")

    assert main(["config", "show", "--effective", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.config-inspection/v1"
    assert payload["config"]["valid"] is False
    assert payload["config"]["issues"][0]["key"] == "environment.ARC_PROGRESS"
    assert "effective" not in payload

    check = _config_check()
    assert check["status"] == "fail"
    assert "ARC_PROGRESS" in str(check["detail"])


def test_invalid_backend_environment_fails_closed_instead_of_selecting_empty_preference(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_BACKEND_ZSTD", " , ")
    result = load_config_result()
    assert result.valid is False
    assert any(issue.key == "environment.ARC_BACKEND_ZSTD" for issue in result.issues)
    assert main(["formats", "--json"]) == 2
    assert "ARC_BACKEND_ZSTD" in capsys.readouterr().err


def test_valid_backend_environment_still_resolves_with_environment_provenance(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, "[backends]\nzstd = ['zstd']\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_BACKEND_ZSTD", "pzstd,zstd")
    result = load_config_result()
    assert result.valid is True
    resolved = resolve_config_value("backends.zstd", result.data)
    assert resolved.value == ["pzstd", "zstd"]
    assert resolved.source == "environment"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("[create]\nlevel = 1.5\n", "create.level"),
        ("[create]\nthreads = true\n", "create.threads"),
        ("[completion]\nremote_ttl_seconds = 1.5\n", "completion.remote_ttl_seconds"),
        ("[profiles.backup]\nlevel = true\n", "profiles.backup.level"),
        ("[remotes.box]\ntype = 'ssh'\nport = 22.5\n", "remotes.box.port"),
        ("[remotes.box]\ntype = 'ssh'\ncapability_ttl_seconds = true\n", "remotes.box.capability_ttl_seconds"),
    ],
)
def test_toml_integer_fields_reject_float_and_boolean_coercion(
    tmp_path: Path,
    monkeypatch,
    text: str,
    key: str,
) -> None:
    _write_config(tmp_path, text)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config_result()
    assert result.valid is False
    issue = next(issue for issue in result.issues if issue.key == key)
    assert issue.code == "invalid_type"
    assert "integer" in issue.message


def test_invalid_environment_remains_visible_through_machine_v1(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_THREADS", "-1")
    assert main(["config", "show", "--effective", "--json=v1"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "arc.machine/v1"
    assert envelope["status"] == "failed"
    assert envelope["result"]["schema"] == "arc.config-inspection/v1"
    assert envelope["result"]["config"]["issues"][0]["key"] == "environment.ARC_THREADS"
