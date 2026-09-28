from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

import arc_cli.execution as execution
from arc_cli.backends import run_backend
from arc_cli.cli import _display_invocation, main, parser
from arc_cli.execution import begin_plan


def _terminal(monkeypatch):
    stream = io.StringIO()
    terminal = Console(file=stream, force_terminal=True, color_system=None, width=120)
    monkeypatch.setattr(execution, "console", terminal)
    return stream


def test_human_command_line_uses_exact_backend_argv_not_arc_invocation(monkeypatch):
    stream = _terminal(monkeypatch)
    begin_plan("create", show_primary_command=True)
    rc = run_backend(["tar", "-cf", "out.tar", "payload"], {}, dry_run=True)
    assert rc == 0
    rendered = stream.getvalue()
    assert "Command" in rendered
    assert "tar -cf out.tar payload" in rendered
    assert "arc create" not in rendered


def test_backend_pipeline_is_rendered_as_the_actual_native_pipeline(monkeypatch):
    stream = _terminal(monkeypatch)
    begin_plan("create", show_primary_command=True)
    rc = run_backend(
        ["tar", "-cf", "-", "payload"],
        {"pipeline": ["zstd", "-q", "-o", "out.tar.zst"], "stdout_file": "out.tar.zst"},
        dry_run=True,
    )
    assert rc == 0
    rendered = stream.getvalue()
    assert "tar -cf - payload | zstd -q -o out.tar.zst" in rendered
    assert "arc create" not in rendered


def test_received_arc_invocation_remains_available_as_audit_identity():
    args = parser().parse_args(["create", "out.zip", "payload", "--password=secret"])
    rendered = _display_invocation(["create", "out.zip", "payload", "--password=secret"], args)
    assert rendered.startswith("arc create")
    assert "secret" not in rendered
    assert "--password=***" in rendered


def test_main_no_longer_prints_the_arc_wrapper_as_the_human_command(monkeypatch, tmp_path: Path, capsys):
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")
    archive = tmp_path / "out.tar"
    # dry-run forces the native command to be printed even under pytest's non-TTY capture.
    assert main(["create", str(archive), str(source), "-tar", "--dry-run", "--progress", "never"]) == 0
    err = capsys.readouterr().err
    assert "Command" in err
    assert "tar" in err
    assert "arc create" not in err


def test_r10a_devtool_ownership_is_first_class():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    assert data["test_profiles"]["r10a"]["pytest_tests"][0] == "tests/test_r10a_backend_command_truth.py"
    tests = {item["id"]: item for item in data["test"]}
    assert "arc-r10a-backend-command-truth" in tests
    workflow = data["targets"]["arc"]["workflows"]["r10a"]
    assert any(node["ref"] == "test:arc-r10a-backend-command-truth" for node in workflow)
    assert data["wrapper"]["commands"]["r10a"]["workflow"] == "r10a"


def test_remote_native_delegation_is_not_mislabeled_as_backend_command(monkeypatch):
    import argparse
    import arc_cli.cli as cli

    # Explicit command display must be forwarded to the delegated Arc even in
    # a non-TTY test process; the local SSH wrapper is transport, not backend.
    args = argparse.Namespace(
        command="create", format=None, format_shortcut=None, backend=None,
        no_fallback=False, json=False, quiet=False, show_command=True,
    )
    native = cli._remote_common_native_args(args)
    assert "--show-command" in native

    stream = _terminal(monkeypatch)
    monkeypatch.setattr(cli, "ssh_command_prefix", lambda location, config: ["ssh", "example"] )
    class Location:
        name = "example"
    cli._run_remote_arc(
        Location(), {}, ["arc", "create", "out.tar", "payload", "--show-command"],
        dry_run=True, description="remote Arc create", show_command=True,
    )
    rendered = stream.getvalue()
    assert "Remote" in rendered
    assert "ssh example" in rendered
    assert "Command  ssh" not in rendered
