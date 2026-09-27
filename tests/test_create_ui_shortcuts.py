from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from rich.console import Console

import arc_cli.cli as cli_module
from arc_cli.cli import (
    _compression_metrics,
    _display_invocation,
    _format_percent,
    _remote_create_target,
    _resolve_create_target,
    _scan_status_text,
    _show_invocation,
    main,
    parser,
)
from arc_cli.completion import completion_candidates
from arc_cli.formats import CREATE_SUFFIX_SHORTCUTS, SUFFIXES, parse_format
from arc_cli.progress import _progress_columns




def test_every_recognized_archive_suffix_has_a_create_selector():
    recognized = {suffix for suffix, _fmt in SUFFIXES}
    selected = {suffix for _flag, _fmt, suffix in CREATE_SUFFIX_SHORTCUTS}
    assert selected == recognized


def test_create_help_describes_selector_append_semantics_truthfully(capsys):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["create", "--help"])
    assert exc.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "has no recognized archive suffix" in text
    assert "when ARCHIVE is extensionless" not in text


def test_remote_create_target_uses_selector_suffix_without_name_inference():
    from arc_cli.remote import RemoteLocation

    args = parser().parse_args(["create", "drive:Backups/archive", "src", "-tarzst"])
    loc = RemoteLocation("rclone", "drive", "Backups/archive", "drive:Backups/archive")
    fmt, resolved = _remote_create_target(args, loc)
    assert fmt.canonical == "tar.zstd"
    assert resolved.path == "Backups/archive.tar.zst"
    assert resolved.raw == "drive:Backups/archive.tar.zst"

@pytest.mark.parametrize("flag, format_name, suffix", CREATE_SUFFIX_SHORTCUTS)
def test_every_suffix_shortcut_maps_to_its_exact_suffix(flag: str, format_name: str, suffix: str):
    args = parser().parse_args(["create", "backup", "src", flag])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == parse_format(format_name).canonical
    assert path == Path("backup" + suffix)


@pytest.mark.parametrize(
    "argv",
    [
        ["create", "-zip", "backup", "src"],
        ["create", "backup", "-zip", "src"],
        ["create", "backup", "src", "-zip"],
    ],
)
def test_suffix_shortcut_can_be_placed_naturally_around_create_positionals(argv: list[str]):
    args = parser().parse_args(argv)
    fmt, path = _resolve_create_target(args, args.archive)
    assert args.inputs == ["src"]
    assert fmt.canonical == "zip"
    assert path == Path("backup.zip")


def test_suffix_shortcut_preserves_stdout_sentinel():
    args = parser().parse_args(["create", "-", "src", "-zip"])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == "zip"
    assert path == Path("-")


def test_stdout_sentinel_is_not_rewritten_by_add_extension_either():
    args = parser().parse_args(["create", "-", "src", "--format", "zip", "--add-extension"])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == "zip"
    assert path == Path("-")


def test_suffix_shortcut_is_authoritative_and_appends_exact_suffix():
    args = parser().parse_args(["create", "backup", "src", "-tarzst"])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == "tar.zstd"
    assert path == Path("backup.tar.zst")

    alias = parser().parse_args(["create", "backup", "src", "-tgz"])
    fmt, path = _resolve_create_target(alias, alias.archive)
    assert fmt.canonical == "tar.gzip"
    assert path == Path("backup.tgz")


def test_suffix_shortcut_does_not_infer_format_from_existing_name():
    args = parser().parse_args(["create", "backup.zip", "src", "-tarzst"])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == "tar.zstd"
    # An explicitly supplied recognized suffix is respected as a filename;
    # the selected format remains authoritative and mismatch warning handles it.
    assert path == Path("backup.zip")


def test_format_and_suffix_shortcut_are_mutually_exclusive_semantically():
    with pytest.raises(SystemExit):
        parser().parse_args(["create", "backup", "src", "--format", "zip", "-tarzst"])


def test_explicit_format_without_add_extension_keeps_existing_contract():
    args = parser().parse_args(["create", "backup", "src", "--format", "zip"])
    fmt, path = _resolve_create_target(args, args.archive)
    assert fmt.canonical == "zip"
    assert path == Path("backup")


def test_create_completion_exposes_suffix_shortcuts_only_on_create():
    create = completion_candidates(["create", "backup", "-"])
    add = completion_candidates(["add", "backup.zip", "-"])
    assert "-zip" in create
    assert "-tarzst" in create
    assert "-tarzst" not in add


def test_create_completion_hides_mutually_exclusive_format_selectors():
    after_shortcut = completion_candidates(["create", "backup", "-zip", "-"])
    assert "-zip" not in after_shortcut
    assert "-tarzst" not in after_shortcut
    assert "-F" not in after_shortcut
    assert "--format" not in after_shortcut

    after_format = completion_candidates(["create", "backup", "--format", "zip", "-"])
    assert "-zip" not in after_format
    assert "-tarzst" not in after_format
    assert "-F" not in after_format
    assert "--format" not in after_format


def test_invocation_stays_one_logical_copyable_line_on_narrow_terminal(monkeypatch):
    stream = io.StringIO()
    narrow = Console(file=stream, force_terminal=True, color_system=None, width=24)
    monkeypatch.setattr(cli_module, "console", narrow)
    argv = ["create", "archive", "payload", "-zip", "--overwrite", "--progress=always"]
    args = parser().parse_args(argv)
    _show_invocation(argv, args)
    rendered = stream.getvalue()
    assert rendered.count("\n") == 1
    assert "arc create archive payload -zip --overwrite --progress=always" in rendered


def test_scan_status_compacts_on_narrow_terminals():
    ultra_narrow = _scan_status_text(128, 12, 3456, 30)
    narrow = _scan_status_text(128, 12, 3456, 40)
    medium = _scan_status_text(128, 12, 3456, 60)
    wide = _scan_status_text(128, 12, 3456, 100)
    assert "sel" in ultra_narrow and "selected" not in ultra_narrow
    assert "seen" in narrow and "bytes" not in narrow
    assert "selected" in medium and "visited" not in medium
    assert "visited" in wide


def test_invocation_display_redacts_password_value():
    args = parser().parse_args(["create", "a.zip", "src", "--password=secret"])
    rendered = _display_invocation(["create", "a.zip", "src", "--password=secret"], args)
    assert "secret" not in rendered
    assert "--password=***" in rendered


def test_percent_formatting_does_not_round_real_values_to_zero_or_one_hundred():
    assert _format_percent(0.0288) == "0.03%"
    assert _format_percent(99.9712) == "99.97%"
    assert _format_percent(25.0) == "25.0%"
    assert _format_percent(100.2) == "100.2%"


def test_compression_metrics_report_savings_and_ratio():
    metrics = _compression_metrics(1000, 250)
    assert metrics["compressed_percent"] == pytest.approx(25.0)
    assert metrics["saved_percent"] == pytest.approx(75.0)
    assert metrics["ratio"] == pytest.approx(4.0)
    assert _compression_metrics(0, 10) == {
        "compressed_percent": None,
        "saved_percent": None,
        "ratio": None,
    }


def test_narrow_progress_uses_lower_density_than_wide_progress():
    narrow = _progress_columns("bytes", 70)
    wide = _progress_columns("bytes", 160)
    narrow_names = [type(x).__name__ for x in narrow]
    wide_names = [type(x).__name__ for x in wide]
    assert "DownloadColumn" not in narrow_names
    assert "TransferSpeedColumn" not in narrow_names
    assert "DownloadColumn" in wide_names
    assert "TransferSpeedColumn" in wide_names
    assert len(narrow) < len(wide)

    ultra_narrow_names = [type(x).__name__ for x in _progress_columns("bytes", 34)]
    assert "TimeElapsedColumn" not in ultra_narrow_names


@pytest.mark.skipif(not shutil.which("zip"), reason="zip backend required")
def test_create_shortcut_writes_extension_and_reports_size_stats(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("payload.txt").write_text("hello" * 1000)
    assert main(["create", "archive", "payload.txt", "-zip", "--progress", "never"]) == 0
    assert Path("archive.zip").is_file()
    rendered = capsys.readouterr().out
    assert "Original" in rendered
    assert "Compressed" in rendered
    assert "Compression" in rendered
    assert "Saved" in rendered
    assert "Ratio" in rendered


@pytest.mark.skipif(not shutil.which("zip"), reason="zip backend required")
def test_create_json_includes_compression_metrics(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("payload.txt").write_text("hello" * 1000)
    assert main(["create", "archive", "payload.txt", "-zip", "--json", "--progress", "never"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["archive"] == "archive.zip"
    assert payload["original_bytes"] == 5000
    assert payload["compressed_bytes"] == Path("archive.zip").stat().st_size
    assert isinstance(payload["compression_percent_of_original"], float)
    assert isinstance(payload["compression_saved_percent"], float)
    assert isinstance(payload["compression_ratio"], float)


@pytest.mark.skipif(not shutil.which("zip"), reason="zip backend required")
def test_actual_create_selector_overrides_mismatched_filename_suffix(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("payload.txt").write_text("hello" * 1000)
    assert main(["create", "misleading.tar", "payload.txt", "-zip", "--progress", "never"]) == 0
    data = Path("misleading.tar").read_bytes()
    assert data.startswith(b"PK")

