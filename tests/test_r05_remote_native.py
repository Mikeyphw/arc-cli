from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path

import pytest

from arc_cli.cli import main
from arc_cli.completion import completion_candidates
from arc_cli.remote import RemoteLocation, remote_capabilities, same_remote


def _config(tmp_path: Path, monkeypatch, body: str | None = None) -> Path:
    path = tmp_path / "config" / "arc" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        body
        or (
            '[remotes.tablet]\n'
            'type="ssh"\n'
            'host="tablet.example"\n'
            'user="u0_test"\n'
            'port=8022\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    return path


def _caps(version: str = "arc 0.1.0") -> dict:
    return {
        "type": "ssh",
        "tools": ["arc", "python3", "tar"],
        "features": ["python-scandir", "stat"],
        "arc_version": version,
        "capabilities": ["remote-arc"],
    }


def _capture_remote(monkeypatch, *, returncode: int = 0):
    seen: list[list[str]] = []

    class Result:
        def __init__(self, rc: int):
            self.returncode = rc

    def fake_run(argv, **_kwargs):
        seen.append(list(argv))
        return Result(returncode)

    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    monkeypatch.setattr("arc_cli.cli.subprocess.run", fake_run)
    return seen


def _remote_words(seen: list[list[str]]) -> list[str]:
    assert seen
    return shlex.split(seen[-1][-1])


def test_same_remote_compares_resolved_ssh_endpoint(tmp_path: Path, monkeypatch):
    _config(
        tmp_path,
        monkeypatch,
        '[remotes.one]\ntype="ssh"\nhost="host"\nuser="u"\nport=8022\n'
        '[remotes.two]\ntype="ssh"\nhost="host"\nuser="u"\nport=8022\n',
    )
    from arc_cli.config import load_config

    cfg = load_config()
    left = RemoteLocation("ssh", "one", "/a", "one:/a")
    right = RemoteLocation("ssh", "two", "/b", "two:/b")
    assert same_remote(left, right, cfg)


def test_remote_create_uses_same_host_inputs_without_staging(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert (
        main(
            [
                "create",
                "tablet:/backups/a.zip",
                "tablet:/src/one.txt",
                "tablet:/src/tree",
                "--execution",
                "remote",
                "--level",
                "7",
                "--threads",
                "2",
                "--exclude",
                "*.tmp",
                "--overwrite",
                "--quiet",
            ]
        )
        == 0
    )
    words = _remote_words(seen)
    assert words[:3] == ["arc", "create", "/backups/a.zip"]
    assert "/src/one.txt" in words and "/src/tree" in words
    assert "tablet:/src/one.txt" not in words
    assert ["--level", "7"] == words[words.index("--level") : words.index("--level") + 2]
    assert ["--threads", "2"] == words[words.index("--threads") : words.index("--threads") + 2]
    assert ["--exclude", "*.tmp"] == words[words.index("--exclude") : words.index("--exclude") + 2]
    assert "--overwrite" in words


def test_remote_create_rejects_local_or_other_remote_inputs(tmp_path: Path, monkeypatch):
    _config(
        tmp_path,
        monkeypatch,
        '[remotes.tablet]\ntype="ssh"\nhost="tablet"\n'
        '[remotes.other]\ntype="ssh"\nhost="other"\n',
    )
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    assert main(["create", "tablet:/a.zip", str(tmp_path / "local"), "--execution", "remote", "--quiet"]) == 3
    assert main(["create", "tablet:/a.zip", "other:/src", "--execution", "remote", "--quiet"]) == 3


def test_remote_add_and_update_forward_backend_passthrough(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert main(["add", "tablet:/a.7z", "tablet:/new", "--execution", "remote", "--quiet", "--", "-bb1"]) == 0
    assert _remote_words(seen)[-2:] == ["--", "-bb1"]
    assert main(["update", "tablet:/a.7z", "tablet:/new", "--execution", "remote", "--quiet"]) == 0
    assert _remote_words(seen)[:3] == ["arc", "update", "/a.7z"]


def test_remote_remove_requires_members_and_preserves_remote_exit_code(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    assert main(["remove", "tablet:/a.zip", "--execution", "remote", "--quiet"]) == 2
    seen = _capture_remote(monkeypatch, returncode=9)
    assert main(["remove", "tablet:/a.zip", "old.txt", "--execution", "remote", "--quiet"]) == 9
    assert _remote_words(seen)[-1] == "old.txt"


def test_remote_extract_to_same_remote_output(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert (
        main(
            [
                "extract",
                "tablet:/a.zip",
                "docs/read me.txt",
                "--execution",
                "remote",
                "--output",
                "tablet:/restore dir",
                "--overwrite",
                "--quiet",
            ]
        )
        == 0
    )
    words = _remote_words(seen)
    assert words[:3] == ["arc", "extract", "/a.zip"]
    assert words[words.index("--output") + 1] == "/restore dir"
    assert "docs/read me.txt" in words
    assert "--overwrite" in words


def test_remote_extract_stdout_allowed_but_local_output_rejected(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert main(["extract", "tablet:/a.zip", "one.txt", "--stdout", "--execution", "remote", "--quiet"]) == 0
    assert "--stdout" in _remote_words(seen)
    assert main(["extract", "tablet:/a.zip", "one.txt", "-o", str(tmp_path / "out"), "--execution", "remote", "--quiet"]) == 3


def test_remote_list_supports_inline_filters_but_rejects_rule_files(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert main(["list", "tablet:/a.zip", "--exclude", "*.tmp", "--include", "keep.tmp", "--execution", "remote", "--quiet"]) == 0
    words = _remote_words(seen)
    assert words[words.index("--exclude") + 1] == "*.tmp"
    assert words[words.index("--include") + 1] == "keep.tmp"
    rules = tmp_path / "rules.txt"
    rules.write_text("*.tmp\n", encoding="utf-8")
    assert main(["list", "tablet:/a.zip", "--exclude-from", str(rules), "--execution", "remote", "--quiet"]) == 3


def test_remote_execution_dry_run_is_zero_network_for_create(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("probe")))
    monkeypatch.setattr("arc_cli.cli.subprocess.run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("execute")))
    assert main(["create", "tablet:/a.zip", "tablet:/src", "--execution", "remote", "--dry-run", "--quiet"]) == 0


def test_remote_execution_rejects_incompatible_arc_version(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps("arc 0.2.0"))
    monkeypatch.setattr("arc_cli.cli.subprocess.run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not execute")))
    assert main(["test", "tablet:/a.zip", "--execution", "remote", "--quiet"]) == 3


def test_remote_execution_allows_unknown_remote_arc_version_for_compatibility(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: {"type": "ssh", "tools": ["arc"], "arc_version": None})
    assert main(["test", "tablet:/a.zip", "--execution", "remote", "--quiet"]) == 0
    assert seen


def test_remote_identify_executes_all_same_host_paths_in_one_command(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert main(["identify", "tablet:/a.zip", "tablet:/b.7z", "--execution", "remote", "--json"]) == 0
    words = _remote_words(seen)
    assert words == ["arc", "identify", "/a.zip", "/b.7z", "--json"]


def test_remote_identify_rejects_mixed_hosts(tmp_path: Path, monkeypatch):
    _config(
        tmp_path,
        monkeypatch,
        '[remotes.tablet]\ntype="ssh"\nhost="tablet"\n'
        '[remotes.other]\ntype="ssh"\nhost="other"\n',
    )
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    assert main(["identify", "tablet:/a.zip", "other:/b.zip", "--execution", "remote", "--json"]) == 3


def test_rclone_cannot_use_remote_execution(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch, '[remotes.cloud]\ntype="rclone"\nremote="gdrive"\n')
    assert main(["test", "cloud:a.zip", "--execution", "remote", "--quiet"]) == 3


def test_output_completion_uses_remote_directory_provider(monkeypatch, tmp_path: Path):
    _config(tmp_path, monkeypatch)
    seen = []

    def fake_complete(prefix, config, **kwargs):
        seen.append((prefix, kwargs))
        return ["tablet:/restore/", "tablet:/results/"]

    monkeypatch.setattr("arc_cli.completion.complete_remote", fake_complete)
    candidates = completion_candidates(["extract", "tablet:/a.zip", "--output", "tablet:/re"])
    assert candidates == ["tablet:/restore/", "tablet:/results/"]
    assert seen and seen[0][1]["dirs_only"] is True


def test_remote_capability_probe_records_arc_version(tmp_path: Path, monkeypatch):
    ssh = tmp_path / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "print('tool:arc\\nfeature:stat\\narc-version:arc 0.1.0')\n",
        encoding="utf-8",
    )
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    caps = remote_capabilities(RemoteLocation("ssh", "tablet", "/", "tablet:/"), {}, refresh=True)
    assert caps["arc_version"] == "arc 0.1.0"
    assert "remote-arc" in caps["capabilities"]


def test_remote_native_quoting_preserves_hostile_paths(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    member = "odd ' quote;$(touch nope).txt"
    assert main(["remove", "tablet:/a weird.zip", member, "--execution", "remote", "--quiet"]) == 0
    words = _remote_words(seen)
    assert words[2] == "/a weird.zip"
    assert words[-1] == member


def test_remote_credentials_remain_rejected(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    assert main(["test", "tablet:/a.zip", "--password", "secret", "--execution", "remote", "--quiet"]) == 3


def test_remote_extract_stdout_rejects_json(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: _caps())
    assert main(["extract", "tablet:/a.zip", "one.txt", "--stdout", "--json", "--execution", "remote"]) == 2


def test_remote_invalid_locality_fails_before_network_probe(tmp_path: Path, monkeypatch):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("probe should not run")))
    assert main(["create", "tablet:/a.zip", str(tmp_path / "local"), "--execution", "remote", "--quiet"]) == 3


def test_remote_show_command_prints_ssh_command(tmp_path: Path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    seen = _capture_remote(monkeypatch)
    assert main(["test", "tablet:/a.zip", "--execution", "remote", "--show-command", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "ssh" in err and "arc test /a.zip" in err
    assert seen


def test_attached_remote_output_completion(monkeypatch, tmp_path: Path):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "arc_cli.completion.complete_remote",
        lambda *_a, **_k: ["tablet:/restore/"],
    )
    assert completion_candidates(["extract", "tablet:/a.zip", "--output=tablet:/re"]) == [
        "--output=tablet:/restore/"
    ]


def test_devtool_r05_profile_and_job_are_tests_only():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / ".devtool.toml").open("rb") as fh:
        data = tomllib.load(fh)
    profile = data["test_profiles"]["r05"]
    assert profile["pytest_tests"] == ["tests/test_r05_remote_native.py"]
    assert profile["pytest_include_defaults"] is False
    job = data["targets"]["arc"]["jobs"]["r05-tests"]
    assert job["runner"] == "command"
    assert job["command"] == ["python3", "scripts/run_r05_tests.py"]
    assert "r05" in data["wrapper"]["commands"]["test"]["options"]["profile"]["choices"]
    assert "r05" in data["wrapper"]["commands"]["validate"]["options"]["profile"]["choices"]
