from __future__ import annotations

import hashlib
import json
import socket
import zipfile
from pathlib import Path

import pytest

from arc_cli import advisory
from arc_cli.cli import main
from arc_cli.config import ConfigIssue, ConfigLoadResult
from arc_cli.machine import load_schema


def _valid_config(tmp_path: Path) -> ConfigLoadResult:
    return ConfigLoadResult(tmp_path / "config.toml", False, {}, ())


def test_gate_recommendation_is_complete_factual_and_non_prescriptive(tmp_path: Path) -> None:
    source = tmp_path / "tree"
    source.mkdir()
    (source / "payload.txt").write_text("arc-r12-gate\n", encoding="utf-8")
    payload = advisory.format_recommendation(source, {})
    schema = load_schema("format-recommendation-v1")
    assert payload["schema"] == schema["$id"] == "arc.format-recommendation/v1"
    assert payload["selection"] is None
    assert "does not choose" in payload["selection_policy"]
    candidates = payload["candidates"]
    assert [row["format"] for row in candidates] == list(advisory.FORMAT_NAMES)
    required_tradeoffs = set(
        schema["properties"]["candidates"]["items"]["properties"]["tradeoffs"]["required"]
    )
    for row in candidates:
        assert required_tradeoffs <= set(row["tradeoffs"])
        assert isinstance(row["available"], bool)
        assert isinstance(row["compatible_with_input"], bool)
        assert not ({"score", "rank", "winner", "recommended"} & set(row))


def test_gate_benchmark_real_directory_evidence_is_host_specific_and_verified(tmp_path: Path) -> None:
    corpus = tmp_path / "portable"
    corpus.mkdir()
    (corpus / "a.txt").write_text("a" * 8192, encoding="utf-8")
    nested = corpus / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(bytes(range(256)) * 16)
    payload = advisory.benchmark({}, corpus=corpus, formats=["tar"], iterations=1)
    assert payload["schema"] == "arc.benchmark/v1"
    assert payload["host_specific"] is True
    assert len(payload["corpus"]["sha256"]) == 64
    row = payload["results"][0]
    assert row["format"] == "tar"
    assert row["status"] == "ok"
    iteration = row["iterations"][0]
    assert iteration["verified_roundtrip"] is True
    assert iteration["encode"]["returncode"] == 0
    assert iteration["decode"]["returncode"] == 0
    assert iteration["encode"]["throughput_bytes_per_second"] > 0
    assert iteration["decode"]["throughput_bytes_per_second"] > 0


def test_gate_diagnostics_manifest_hashes_every_evidence_member_and_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(advisory, "backend_inventory", lambda *args, **kwargs: [])
    monkeypatch.setattr(advisory, "collect_doctor_report", lambda: {"checks": [], "summary": {"pass": 0, "warn": 0, "fail": 0}})
    monkeypatch.setattr(advisory, "alias_status_rows", lambda: [])

    def forbid_connect(*args, **kwargs):
        raise AssertionError("diagnostics bundle attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbid_connect, raising=True)
    target = tmp_path / "support.zip"
    result = advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target)
    assert result["network_probe_performed"] is False
    assert result["archive_contents_included"] is False
    assert hashlib.sha256(target.read_bytes()).hexdigest() == result["sha256"]

    with zipfile.ZipFile(target) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = set(zf.namelist())
        declared = {item["name"] for item in manifest["files"]}
        assert names == declared | {"manifest.json"}
        assert manifest["redaction"]["network_probe_performed"] is False
        assert manifest["redaction"]["archive_contents_included"] is False
        for item in manifest["files"]:
            raw = zf.read(item["name"])
            assert len(raw) == item["bytes"]
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
            json.loads(raw)


def test_gate_diagnostics_cached_remote_evidence_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "R12-GATE-CACHED-REMOTE-SECRET"
    cache = tmp_path / "cache"
    capability = cache / "capabilities" / "remote-a.json"
    capability.parent.mkdir(parents=True)
    capability.write_text(
        json.dumps({"token": secret, "nested": {"password": secret}, "note": f"prefix-{secret}-suffix"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARC_SUPPORT_TOKEN", secret)
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(cache))
    monkeypatch.setattr(advisory, "backend_inventory", lambda *args, **kwargs: [])
    monkeypatch.setattr(advisory, "collect_doctor_report", lambda: {"checks": [], "summary": {"pass": 0, "warn": 0, "fail": 0}})
    monkeypatch.setattr(advisory, "alias_status_rows", lambda: [])
    target = tmp_path / "support.zip"
    advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target)
    with zipfile.ZipFile(target) as zf:
        remote = zf.read("remote-capabilities.json").decode("utf-8")
    assert secret not in remote
    assert "***" in remote


def test_gate_diagnostics_remains_available_with_invalid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = ConfigLoadResult(
        tmp_path / "config.toml",
        True,
        {},
        (ConfigIssue("error", "invalid_value", "environment.ARC_LEVEL", "invalid level"),),
    )
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(advisory, "backend_inventory", lambda *args, **kwargs: [])
    monkeypatch.setattr(advisory, "collect_doctor_report", lambda: {"checks": [{"id": "config", "status": "fail"}], "summary": {"pass": 0, "warn": 0, "fail": 1}})
    monkeypatch.setattr(advisory, "alias_status_rows", lambda: [])
    target = tmp_path / "invalid-config-support.zip"
    result = advisory.build_diagnostics_bundle(invalid, output=target)
    assert target.is_file()
    assert result["schema"] == "arc.diagnostics-bundle/v1"
    with zipfile.ZipFile(target) as zf:
        config = json.loads(zf.read("config.json"))
    assert config["config"]["valid"] is False
    assert config["config"]["issues"][0]["key"] == "environment.ARC_LEVEL"


def test_gate_machine_v1_wraps_benchmark_and_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(
        "arc_cli.cli.run_benchmark",
        lambda *args, **kwargs: {
            "schema": "arc.benchmark/v1",
            "schema_version": 1,
            "generated_at": "2026-09-28T00:00:00Z",
            "host_specific": True,
            "host": {},
            "corpus": {},
            "iterations": 1,
            "results": [],
        },
    )
    assert main(["benchmark", "--format", "tar", "--json=v1"]) == 0
    bench = json.loads(capsys.readouterr().out)
    assert bench["schema"] == "arc.machine/v1"
    assert bench["operation"] == "benchmark"
    assert bench["result"]["schema"] == "arc.benchmark/v1"

    target = tmp_path / "bundle.zip"
    monkeypatch.setattr(
        "arc_cli.cli.build_diagnostics_bundle",
        lambda *args, **kwargs: {
            "schema": "arc.diagnostics-bundle/v1",
            "schema_version": 1,
            "path": str(target),
            "sha256": "0" * 64,
            "bytes": 0,
            "files": ["manifest.json"],
            "archive_contents_included": False,
            "network_probe_performed": False,
        },
    )
    assert main(["diagnostics", "bundle", str(target), "--json=v1"]) == 0
    diagnostics = json.loads(capsys.readouterr().out)
    assert diagnostics["schema"] == "arc.machine/v1"
    assert diagnostics["operation"] == "diagnostics"
    assert diagnostics["result"]["schema"] == "arc.diagnostics-bundle/v1"


def test_gate_r12_family_and_prior_gates_are_first_class_devtool_surfaces() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    tests = {row["id"] for row in cfg.get("test", [])}
    for test_id in (
        "arc-r09b-gate",
        "arc-r10-gate",
        "arc-r11-gate",
        "arc-r12-advisory-benchmark-diagnostics",
        "arc-r12a-benchmark-corpus-truth",
        "arc-r12-gate",
    ):
        assert test_id in tests
    workflows = cfg["targets"]["arc"]["workflows"]
    for workflow in ("r09b_gate", "r10_gate", "r11_gate", "r12", "r12a", "r12_gate"):
        assert workflow in workflows
