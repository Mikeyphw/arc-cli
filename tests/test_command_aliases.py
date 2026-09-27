from __future__ import annotations

import json
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from arc_cli.cli import _display_invocation, main, parser
from arc_cli.command_docs import EXECUTABLE_ALIASES
from arc_cli.completion import zsh_completion


ROOT = Path(__file__).resolve().parents[1]


def test_every_dispatch_alias_is_installed_as_a_console_script() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["arc"] == "arc_cli.cli:main"
    assert set(EXECUTABLE_ALIASES) <= set(scripts)
    assert all(scripts[name] == "arc_cli.cli:main" for name in EXECUTABLE_ALIASES)


def test_requested_short_and_descriptive_aliases_are_present() -> None:
    expected = {
        "arcmk": "create", "arc-create": "create", "arcpack": "create",
        "arcx": "extract", "arc-extract": "extract", "arcunpack": "extract",
        "arcls": "list", "arc-list": "list",
        "arci": "info", "arc-info": "info",
        "arct": "test", "arc-test": "test", "arccheck": "test",
        "arccv": "convert", "arc-convert": "convert", "arcconvert": "convert",
        "arca": "add", "arc-add": "add", "arcu": "update", "arc-update": "update",
        "arcrm": "remove", "arc-remove": "remove",
        "arcbe": "backends", "arc-backends": "backends",
        "arc-formats": "formats", "arcp": "profiles", "arc-profiles": "profiles",
    }
    assert {name: EXECUTABLE_ALIASES[name] for name in expected} == expected
    assert "arcc" not in EXECUTABLE_ALIASES


def test_alias_dispatch_preserves_actual_program_name_in_display(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parser().parse_args(["info", "archive.zip"])
    args._invoked_program = "arci"
    args._display_argv = ["archive.zip"]
    assert _display_invocation(["info", "archive.zip"], args) == "arci archive.zip"


def test_arci_real_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "hello")
    monkeypatch.setattr(sys, "argv", ["arci", str(archive), "--json"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["format"] == "zip"
    assert payload["resolved_command"] == "info"
    assert payload["invocation"].startswith("arci ")
    assert str(archive) in payload["invocation"]


def test_generated_zsh_completion_is_alias_aware_and_metadata_driven() -> None:
    text = zsh_completion()
    for alias, command in EXECUTABLE_ALIASES.items():
        assert alias in text
        assert f"{alias}) print -r -- {command} ;;" in text
    assert '${words[1]}" == arc' in text
    assert "arc __complete0" in text
