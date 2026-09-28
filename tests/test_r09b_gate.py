from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from arc_cli.machine import load_schema
from arc_cli.remote import RemoteLocation, remote_capabilities, upload_remote


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _ssh_probe(path: Path, log: Path) -> Path:
    return _exe(
        path,
        "import json,os,pathlib,sys\n"
        "with pathlib.Path(os.environ['ARC_R09B_GATE_SSH_LOG']).open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print('tool:arc\\ntool:sh\\ntool:cat\\ntool:mkdir\\ntool:mv\\ntool:rm\\ntool:tar\\nfeature:find-print0\\narc-version:arc 0.9.0')\n"
        "print('arc-backends-json:' + json.dumps([{'role':'zip','candidates':[{'binary':'zip','path':'/usr/bin/zip','capability_profile':{'schema_version':1,'binary':'zip'}}]}]))\n",
    )


def test_gate_cache_lifecycle_reports_live_cache_refresh_and_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "ssh.log"
    _ssh_probe(tmp_path / "ssh", log)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ARC_R09B_GATE_SSH_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))

    clock = [1_000.0]
    monkeypatch.setattr("arc_cli.remote.time.time", lambda: clock[0])
    config = {"completion": {"capability_ttl_seconds": 10}}
    loc = RemoteLocation("ssh", "tablet", "/archive.zip", "tablet:/archive.zip")

    live = remote_capabilities(loc, config, refresh=True)
    assert live["probe"]["source"] == "live"
    assert live["probe"]["age_seconds"] == 0
    assert live["probe"]["fresh"] is True
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    clock[0] = 1_005.0
    cached = remote_capabilities(loc, config)
    assert cached["probe"]["source"] == "cache"
    assert cached["probe"]["age_seconds"] == 5
    assert cached["probe"]["ttl_seconds"] == 10
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    clock[0] = 1_006.0
    refreshed = remote_capabilities(loc, config, refresh=True)
    assert refreshed["probe"]["source"] == "live"
    assert refreshed["probe"]["age_seconds"] == 0
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2

    clock[0] = 1_017.0
    expired = remote_capabilities(loc, config)
    assert expired["probe"]["source"] == "live"
    assert expired["probe"]["age_seconds"] == 0
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3


def test_gate_dry_run_consumes_fresh_cache_then_fails_closed_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "ssh.log"
    _ssh_probe(tmp_path / "ssh", log)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ARC_R09B_GATE_SSH_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))

    clock = [2_000.0]
    monkeypatch.setattr("arc_cli.remote.time.time", lambda: clock[0])
    config = {"completion": {"capability_ttl_seconds": 10}}
    loc = RemoteLocation("ssh", "tablet", "/out.zip", "tablet:/out.zip")
    source = tmp_path / "payload.zip"
    source.write_bytes(b"payload")

    remote_capabilities(loc, config, refresh=True)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    clock[0] = 2_005.0
    fresh = upload_remote(source, loc, config, dry_run=True)
    assert fresh["guaranteed_atomic"] is True
    assert fresh["atomicity"] == "same-filesystem-rename"
    assert fresh["probe"]["source"] == "cache"
    assert fresh["probe"]["age_seconds"] == 5
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    clock[0] = 2_011.0
    stale = upload_remote(source, loc, config, dry_run=True)
    assert stale["guaranteed_atomic"] is False
    assert stale["atomicity"] == "unproven"
    assert "provider-not-probed" in stale["evidence"]
    assert "probe" not in stale
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("move", [True, False, None])
def test_gate_rclone_move_matrix_never_claims_guaranteed_atomicity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, move: bool | None
) -> None:
    features = {"PutStream": True}
    if move is not None:
        features["Move"] = move
    _exe(
        tmp_path / "rclone",
        "import json,sys\n"
        f"features={features!r}\n"
        "if sys.argv[1:3] == ['backend','features']:\n"
        " print(json.dumps({'Features': features})); raise SystemExit(0)\n"
        "raise SystemExit(0)\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    caps = remote_capabilities(RemoteLocation("rclone", "drive", "a.zip", "drive:a.zip"), {}, refresh=True)
    publication = caps["publication"]
    assert publication["strategy"] == "temporary-object+rclone-moveto"
    assert publication["atomicity"] == "provider-dependent"
    assert publication["guaranteed_atomic"] is False
    assert publication["replace_semantics"] == "provider-dependent"


def test_gate_remote_capability_schema_requires_truth_and_provenance_fields() -> None:
    schema = load_schema("remote-capability-v1")
    assert set(schema["required"]) >= {
        "schema", "schema_version", "transport", "locality", "staging", "publication", "probe"
    }
    assert set(schema["properties"]["publication"]["required"]) >= {
        "strategy", "scope", "finalizer", "temporary_object", "atomicity", "guaranteed_atomic", "replace_semantics", "evidence"
    }
    assert set(schema["properties"]["probe"]["required"]) >= {
        "source", "provider", "fetched_at", "age_seconds", "ttl_seconds", "provider_generation", "fresh"
    }


def test_gate_devtool_workflow_wrapper_and_contract_are_first_class() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    assert "tests/test_r09b_gate.py" in data["test_profiles"]["r09b_gate"]["pytest_tests"]
    declared = {row["id"] for row in data["test"]}
    assert "arc-r09b-gate" in declared
    workflow = data["targets"]["arc"]["workflows"]["r09b_gate"]
    refs = {step["ref"] for step in workflow}
    assert "job:r09b-gate-contract" in refs
    assert "job:remote-contract" in refs
    assert "job:machine-contract" in refs
    assert "job:provenance-contract" in refs
    assert "test:arc-r09b-gate" in refs
    assert data["wrapper"]["commands"]["r09b-gate"]["workflow"] == "r09b_gate"

    result = subprocess.run(
        [sys.executable, "scripts/check_r09b_gate_contract.py"], cwd=root, text=True, capture_output=True
    )
    assert result.returncode == 0, f"gate contract failed:\n{result.stdout}\n{result.stderr}"


def test_gate_repository_contract_checkers_are_green() -> None:
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, "scripts/check_machine_contract_r09b.py"],
        [sys.executable, "scripts/check_devtool_contract_r09b.py"],
        [sys.executable, "scripts/check_remote_contract.py"],
        [sys.executable, "scripts/check_provenance_contract.py"],
        [sys.executable, "scripts/generate_command_docs.py", "--check"],
        [sys.executable, "scripts/check_completion_contract.py"],
        [sys.executable, "scripts/check_r09b_gate_contract.py"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=root, text=True, capture_output=True)
        assert result.returncode == 0, f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}"


def test_gate_promise_ledger_is_closed_and_r10_remains_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / "docs" / "ARC-R09B-GATE.md").read_text(encoding="utf-8")
    roadmap = (root / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    for promise in (
        "arc.remote-capability/v1",
        "single SSH probe",
        "provider-dependent",
        "zero-network",
        "provider generation",
        "remote re-read",
        "144 targeted tests",
    ):
        assert promise in gate
    assert "R09B gate: QUALIFIED" in roadmap
    assert "## R10 — Destructive-operation policy and interactive execution UX" in roadmap
