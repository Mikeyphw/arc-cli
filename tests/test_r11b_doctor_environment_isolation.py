from __future__ import annotations

from pathlib import Path

import pytest

from arc_cli.backends import backend_inventory
from arc_cli.config import DEFAULT_BACKENDS, load_config_result
from arc_cli.doctor import collect_doctor_report


def _config_check(report: dict[str, object]) -> dict[str, object]:
    return next(item for item in report["checks"] if item["id"] == "config")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ARC_PROGRESS", "sometimes"),
        ("ARC_LEVEL", "bogus"),
        ("ARC_THREADS", "-1"),
        ("ARC_BACKEND_ZSTD", " , "),
    ],
)
def test_doctor_never_reenters_invalid_supported_environment(
    tmp_path: Path, monkeypatch, name: str, value: str
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv(name, value)
    result = load_config_result()
    assert result.valid is False
    report = collect_doctor_report()
    check = _config_check(report)
    assert check["status"] == "fail"
    assert name in str(check["detail"])
    assert any(item["id"] == "backends" for item in report["checks"])


def test_doctor_invalid_backend_environment_uses_safe_builtin_inventory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_BACKEND_ZSTD", " , ")
    report = collect_doctor_report()
    assert _config_check(report)["status"] == "fail"
    backend_check = next(item for item in report["checks"] if item["id"] == "backends")
    assert backend_check["status"] in {"pass", "warn"}


def test_backend_inventory_explicit_empty_environment_ignores_process_override(monkeypatch) -> None:
    monkeypatch.setenv("ARC_BACKEND_ZSTD", " , ")
    rows = backend_inventory({}, environ={})
    zstd = next(row for row in rows if row["role"] == "zstd")
    assert zstd["preferences"] == DEFAULT_BACKENDS["zstd"]


def test_backend_inventory_default_environment_still_honors_valid_override(monkeypatch) -> None:
    monkeypatch.setenv("ARC_BACKEND_ZSTD", "custom-zstd,zstd")
    rows = backend_inventory({})
    zstd = next(row for row in rows if row["role"] == "zstd")
    assert zstd["preferences"] == ["custom-zstd", "zstd"]
