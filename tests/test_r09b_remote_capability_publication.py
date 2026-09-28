from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from arc_cli.cli import main
from arc_cli.machine import load_schema, schema_names
from arc_cli.remote import RemoteLocation, remote_capabilities, remote_publication_guarantee


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_ssh_remote_capabilities_are_typed_cached_and_single_probe(tmp_path: Path, monkeypatch):
    log = tmp_path / "ssh.log"
    backend = [{"role": "zip", "candidates": [{"binary": "zip", "path": "/usr/bin/zip", "capability_profile": {"schema_version": 1, "binary": "zip"}}]}]
    _exe(
        tmp_path / "ssh",
        "import json,os,pathlib,sys\n"
        "with pathlib.Path(os.environ['SSH_LOG']).open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print('tool:arc\\ntool:sh\\ntool:cat\\ntool:mkdir\\ntool:mv\\ntool:rm\\ntool:tar\\nfeature:find-print0\\narc-version:arc 0.9.0')\n"
        f"print('arc-backends-json:' + {json.dumps(json.dumps(backend))})\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("SSH_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    loc = RemoteLocation("ssh", "tablet", "/archive.zip", "tablet:/archive.zip")

    live = remote_capabilities(loc, {}, refresh=True)
    assert live["schema"] == "arc.remote-capability/v1"
    assert live["probe"]["source"] == "live"
    assert live["probe"]["age_seconds"] == 0
    assert live["locality"]["remote_arc_execution"] is True
    assert live["staging"]["directory_read"] is True
    assert live["publication"]["guaranteed_atomic"] is True
    assert live["publication"]["atomicity"] == "same-filesystem-rename"
    assert live["remote_backend_inventory"][0]["candidates"][0]["binary"] == "zip"

    cached = remote_capabilities(loc, {})
    assert cached["probe"]["source"] == "cache"
    assert cached["probe"]["fresh"] is True
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_ssh_atomicity_fails_closed_without_required_tools(tmp_path: Path, monkeypatch):
    _exe(tmp_path / "ssh", "print('tool:sh\\ntool:cat\\ntool:mkdir\\ntool:rm')\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    caps = remote_capabilities(RemoteLocation("ssh", "tablet", "/x", "tablet:/x"), {}, refresh=True)
    assert caps["publication"]["guaranteed_atomic"] is False
    assert caps["publication"]["atomicity"] == "unproven"
    assert "missing:mv" in caps["publication"]["evidence"]


def test_rclone_move_feature_never_becomes_atomicity_claim(tmp_path: Path, monkeypatch):
    _exe(
        tmp_path / "rclone",
        "import json,sys\n"
        "if sys.argv[1:3] == ['backend','features']:\n"
        " print(json.dumps({'Features': {'Move': True, 'PutStream': True}})); raise SystemExit(0)\n"
        "raise SystemExit(0)\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    caps = remote_capabilities(RemoteLocation("rclone", "drive", "a.zip", "drive:a.zip"), {}, refresh=True)
    pub = caps["publication"]
    assert pub["server_side_move"] is True
    assert pub["guaranteed_atomic"] is False
    assert pub["atomicity"] == "provider-dependent"
    assert "rclone-provider:Move=true" in pub["evidence"]


def test_publication_planning_can_remain_zero_probe(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    ssh = RemoteLocation("ssh", "tablet", "/x", "tablet:/x")
    rclone = RemoteLocation("rclone", "drive", "x", "drive:x")
    monkeypatch.setattr("arc_cli.remote.remote_capabilities", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network probe")))
    ssh_pub = remote_publication_guarantee(ssh, {}, allow_probe=False)
    rc_pub = remote_publication_guarantee(rclone, {}, allow_probe=False)
    assert ssh_pub["guaranteed_atomic"] is False and ssh_pub["atomicity"] == "unproven"
    assert rc_pub["guaranteed_atomic"] is False and rc_pub["atomicity"] == "provider-dependent"
    assert "provider-not-probed" in ssh_pub["evidence"]


def test_backends_remote_refresh_is_forwarded(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr("arc_cli.cli._remote_name_location", lambda *_a, **_k: RemoteLocation("ssh", "tablet", "/", "tablet:/"))
    monkeypatch.setattr(
        "arc_cli.cli.remote_capabilities",
        lambda *_a, **k: seen.append(k) or {"schema": "arc.remote-capability/v1", "schema_version": 1},
    )
    assert main(["backends", "--remote", "tablet", "--refresh", "--json"]) == 0
    assert seen == [{"refresh": True}]
    assert json.loads(capsys.readouterr().out)["schema"] == "arc.remote-capability/v1"


def test_remote_capability_schema_is_public():
    assert "remote-capability-v1" in schema_names()
    schema = load_schema("remote-capability-v1")
    assert schema["properties"]["schema"]["const"] == "arc.remote-capability/v1"


def test_devtool_r09b_contract_is_first_class():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / ".devtool.toml").open("rb") as fh:
        data = tomllib.load(fh)
    assert "tests/test_r09b_remote_capability_publication.py" in data["test_profiles"]["r09b"]["pytest_tests"]
    tests = {row["id"] for row in data["test"]}
    assert "arc-r09b-remote-capability-publication" in tests
    refs = {step["ref"] for step in data["targets"]["arc"]["workflows"]["r09b"]}
    assert "job:remote-contract" in refs
    assert "test:arc-r09b-remote-capability-publication" in refs
    jobs = data["targets"]["arc"]["jobs"]
    assert jobs["machine-contract"]["command"] == ["python3", "scripts/check_machine_contract_r09b.py"]
    assert jobs["devtool-contract"]["command"] == ["python3", "scripts/check_devtool_contract_r09b.py"]
    assert data["wrapper"]["commands"]["r09b"]["workflow"] == "r09b"


def test_relocated_r09b_contract_checkers_are_executable():
    root = Path(__file__).resolve().parents[1]
    for script in (
        "scripts/check_machine_contract_r09b.py",
        "scripts/check_devtool_contract_r09b.py",
        "scripts/check_remote_contract.py",
    ):
        result = subprocess.run([sys.executable, script], cwd=root, text=True, capture_output=True)
        assert result.returncode == 0, f"{script} failed:\n{result.stdout}\n{result.stderr}"
