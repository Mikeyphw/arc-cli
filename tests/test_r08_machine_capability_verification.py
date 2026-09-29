from __future__ import annotations

import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from arc_cli import cli
from arc_cli.backends import backend_capability_profile
from arc_cli.capabilities import VerificationLevel
from arc_cli.cli import main
from arc_cli.machine import load_schema, schema_names


def _zip(path: Path, files: dict[str, bytes] | None = None) -> None:
    files = files or {"a.txt": b"alpha"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def _tar(path: Path) -> None:
    source = path.with_suffix(".txt")
    source.write_text("alpha", encoding="utf-8")
    with tarfile.open(path, "w") as tf:
        tf.add(source, arcname="a.txt")


def test_bare_json_retains_legacy_shape_and_v1_uses_machine_envelope(capsys) -> None:
    assert main(["backends", "--json"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert isinstance(legacy, list)
    assert legacy and "role" in legacy[0]
    assert "schema" not in legacy[0]

    assert main(["backends", "--json=v1"]) == 0
    v1 = json.loads(capsys.readouterr().out)
    assert v1["schema"] == "arc.machine/v1"
    assert v1["schema_version"] == 1
    assert v1["kind"] == "result"
    assert v1["operation"] == "backends"
    assert isinstance(v1["result"], list)


def test_machine_v1_preserves_received_argv_and_alias_identity(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["arcbe", "--json=v1"])
    assert main(None) == 0
    payload = json.loads(capsys.readouterr().out)
    identity = payload["invocation"]
    assert identity["program"] == "arcbe"
    assert identity["literal_argv"] == ["--json=v1"]
    assert identity["canonical_command"] == "backends"
    assert identity["literal"].startswith("arcbe ")


def test_machine_v1_parse_errors_are_typed_and_redact_password(capsys) -> None:
    rc = main(["identify", "missing", "--password", "super-secret", "--json=v1"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "error"
    assert payload["error"]["type"] == "UsageError"
    assert payload["error"]["exit_code"] == 2
    encoded = json.dumps(payload)
    assert "super-secret" not in encoded
    assert "***" in encoded


def test_v1_native_plan_is_a_typed_diagnostic_not_separate_stderr(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive)
    assert main(["info", str(archive), "--verify-level", "full", "--show-native=after", "--json=v1"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    diagnostics = payload["diagnostics"]
    assert diagnostics and diagnostics[0]["kind"] == "native_plan"
    assert diagnostics[0]["severity"] == "info"
    assert diagnostics[0]["payload"]["operation"] == "info"


def test_bundled_machine_capability_and_verification_schemas_are_discoverable(capsys) -> None:
    assert set(schema_names()) == {'machine-v1', 'backend-capability-v1', 'verification-evidence-v1', 'logical-fingerprint-v1', 'archive-diff-v1', 'remote-capability-v1', 'batch-input-v1', 'config-inspection-v1', 'format-recommendation-v1', 'benchmark-v1', 'diagnostics-bundle-v1', 'split-manifest-v1'}
    for name in schema_names():
        schema = load_schema(name)
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["type"] == "object"
        assert schema["required"]
    assert main(["schema", "--list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert set(listed["schemas"]) == set(schema_names())
    assert main(["schema", "machine-v1"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Arc machine envelope v1"


def test_typed_backend_profiles_cover_planner_relevant_domains() -> None:
    seven = backend_capability_profile("7z")
    assert seven.solid and seven.multipart and seven.random_access
    assert seven.encryption_read and seven.encryption_write
    assert seven.maximum_verification is VerificationLevel.FULL
    assert seven.remote_suitability == "staged-random-access"

    tar = backend_capability_profile("tar")
    assert tar.stdin and tar.stdout and tar.safe_index
    assert tar.maximum_verification is VerificationLevel.MEMBERS
    assert "remove" in tar.operations if shutil.which("tar") else True

    gzip = backend_capability_profile("gzip")
    assert gzip.remote_suitability == "streamable"
    assert gzip.maximum_verification is VerificationLevel.FULL


def test_backends_json_and_verbose_human_share_typed_profile(capsys) -> None:
    assert main(["backends", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    tar = next(candidate for row in rows if row["role"] == "tar" for candidate in row["candidates"] if candidate["binary"] == "tar")
    assert tar["capability_profile"]["schema_version"] == 1
    assert tar["capability_profile"]["verification"]["maximum"] == "members"

    assert main(["backends", "--verbose"]) == 0
    human = capsys.readouterr().out
    assert "Verify" in human
    assert "Remote" in human


@pytest.mark.skipif(not shutil.which("unzip"), reason="Info-ZIP unzip unavailable")
def test_explicit_full_zip_verification_records_full_proof(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive)
    assert main(["test", str(archive), "--verify-level", "full", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    evidence = payload["verification"]
    assert evidence["requested"] == "full"
    assert evidence["achieved"] == "full"
    assert evidence["status"] == "passed"
    assert evidence["downgraded"] is False
    assert any(check["name"] == "full-content" for check in evidence["checks"])


@pytest.mark.skipif(not shutil.which("tar"), reason="tar unavailable")
def test_explicit_full_tar_verification_fails_closed_without_downgrade(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sample.tar"
    _tar(archive)
    assert main(["test", str(archive), "--verify-level", "full", "--json=v1"]) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "error"
    assert payload["error"]["type"] == "UnsupportedFormat"
    assert "allow-verification-downgrade" in payload["error"]["message"]


@pytest.mark.skipif(not shutil.which("tar"), reason="tar unavailable")
def test_explicit_verification_downgrade_is_visible_evidence(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sample.tar"
    _tar(archive)
    assert main([
        "test", str(archive), "--verify-level", "full", "--allow-verification-downgrade", "--json"
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    evidence = payload["verification"]
    assert evidence["requested"] == "full"
    assert evidence["achieved"] == "members"
    assert evidence["downgraded"] is True
    assert evidence["status"] == "passed"


def test_verification_none_is_explicit_skipped_evidence(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive)
    assert main(["test", str(archive), "--verify-level", "none", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verification"]["requested"] == "none"
    assert payload["verification"]["achieved"] == "none"
    assert payload["verification"]["status"] == "skipped"


def test_convert_none_publishes_unverified_but_cannot_replace_source(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sample.zip"
    _zip(source)
    destination = tmp_path / "sample.tar"
    assert main(["convert", str(source), str(destination), "-F", "tar", "--verify-level", "none", "--json", "--progress", "never"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert destination.exists()
    assert payload["verified"] is None
    assert payload["verification"]["status"] == "skipped"
    assert source.exists()

    other = tmp_path / "other.zip"
    _zip(other)
    out = tmp_path / "other.tar"
    assert main(["convert", str(other), str(out), "-F", "tar", "--verify-level", "none", "--replace-source", "--json"]) != 0
    capsys.readouterr()
    assert other.exists()
    assert not out.exists()


def test_explain_rejects_replace_source_when_verification_is_disabled(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sample.zip"
    _zip(source)
    destination = tmp_path / "sample.tar"
    rc = main([
        "explain", "--json=v1", "convert", str(source), str(destination),
        "-F", "tar", "--verify-level", "none", "--replace-source", "--progress", "never",
    ])
    assert rc != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "error"
    assert payload["error"]["type"] == "UsageError"
    assert "replace-source" in payload["error"]["message"]
    assert "verification" in payload["error"]["message"]
    assert source.exists()
    assert not destination.exists()


def test_explain_projects_typed_verification_policy_without_mutation(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sample.zip"
    _zip(source)
    assert main([
        "explain", "--json", "convert", str(source), "-tgz", "--verify-level", "full",
        "--allow-verification-downgrade", "--progress", "never"
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    decisions = {item["name"]: item["value"] for item in payload["plan"]["decisions"]}
    policy = decisions["verification_policy"]
    assert policy["requested"] == "full"
    assert policy["selected"] in {"members", "full"}
    assert payload["mutation"] is False
    assert not (tmp_path / "sample.tgz").exists()
