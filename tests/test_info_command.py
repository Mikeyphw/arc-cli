from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from arc_cli import cli
from arc_cli.cli import main, parser


def _zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_info_is_summary_not_implicit_integrity_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive, {"a.txt": b"alpha"})
    assert main(["info", str(archive), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "zip"
    assert data["members"]["total"] == 1
    assert data["verified"] is None
    assert data["safe_paths"] is True


def test_info_verify_reports_actual_integrity_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive, {"a.txt": b"alpha"})
    assert main(["info", str(archive), "--verify", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verified"] is True
    assert data["verify_backend"]


def test_info_detects_filename_content_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "wrong.rar"
    _zip(archive, {"a.txt": b"alpha"})
    assert main(["info", str(archive), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "zip"
    assert data["extension_format"] == "rar"
    assert data["format_mismatch"] is True
    assert data["format_confidence"] == "high"


def test_info_multiple_archives_returns_json_array(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, {"a": b"a"})
    _zip(b, {"b": b"b"})
    assert main(["info", str(a), str(b), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [Path(row["path"]).name for row in data] == ["a.zip", "b.zip"]


def test_info_gzip_reports_original_size_and_embedded_filename(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "document.txt"
    source.write_bytes(b"abc" * 100)
    stream = tmp_path / "renamed.gz"
    with source.open("rb") as src, gzip.GzipFile(filename=source.name, mode="wb", fileobj=stream.open("wb")) as out:
        out.write(src.read())
    assert main(["info", str(stream), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["original_bytes"] == 300
    assert data["stream_filename"] == "document.txt"


def test_info_technical_exposes_backend_metadata_without_claiming_verification(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive, {"a.txt": b"alpha"})
    assert main(["info", str(archive), "--technical", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data["technical"], dict)
    assert "methods" in data["technical"]
    assert data["verified"] is None


def test_info_parser_accepts_native_diagnostic_flags() -> None:
    args = parser().parse_args(["info", "a.zip", "--show-command", "--show-native=both", "--native-style", "exact"])
    assert args.show_command is True
    assert args.show_native == "both"
    assert args.native_style == "exact"


def test_info_encrypted_zip_without_password_still_summarizes_and_verify_fails_noninteractively(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import shutil
    import subprocess
    if not shutil.which("zip") or not shutil.which("unzip"):
        pytest.skip("Info-ZIP tools unavailable")
    root = tmp_path / "src"
    root.mkdir()
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    archive = tmp_path / "private.zip"
    subprocess.run(["zip", "-q", "-P", "pass", str(archive), "secret.txt"], cwd=root, check=True)
    assert main(["info", str(archive), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["encrypted"] is True
    assert data["verified"] is None
    assert main(["info", str(archive), "--verify", "--json"]) != 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["verified"] is False
    assert "password" in verified["verify_error"].lower()


def test_info_members_report_largest_and_timestamp_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "times.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        early = zipfile.ZipInfo("small.txt", date_time=(2020, 1, 2, 3, 4, 6))
        late = zipfile.ZipInfo("large.bin", date_time=(2024, 5, 6, 7, 8, 10))
        zf.writestr(early, b"x")
        zf.writestr(late, b"y" * 100)
    assert main(["info", str(archive), "--members", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["oldest"] == "2020-01-02 03:04:06"
    assert data["newest"] == "2024-05-06 07:08:10"
    assert data["members"]["largest"] == {"name": "large.bin", "bytes": 100}
    assert "volumes" in data


def test_info_verify_native_plan_describes_executed_probe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive, {"a.txt": b"alpha"})
    assert main(["info", str(archive), "--verify", "--show-native=after", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["verified"] is True
    diagnostic = json.loads(captured.err)
    assert diagnostic["native_plan"]["operation"] == "info"
    assert diagnostic["native_plan"]["stages"]


def test_info_human_output_surfaces_volume_and_remote_staging_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "sample.zip"
    _zip(archive, {"a.txt": b"a"})

    original = cli._archive_info_local

    def fake_info(path, display, args, config, password):
        result, failed = original(path, display, args, config, password)
        result["volumes"] = 3
        return result, failed

    monkeypatch.setattr(cli, "_archive_info_local", fake_info)
    monkeypatch.setattr(cli, "parse_remote", lambda *args, **kwargs: None)
    assert main(["info", str(archive)]) == 0
    assert "Volumes" in capsys.readouterr().out


def test_info_remote_result_marks_local_inspection_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "staged.zip"
    _zip(archive, {"a.txt": b"a"})

    class FakeRemote:
        raw = "ssh://example/staged.zip"
        kind = "ssh"

    fake = FakeRemote()
    monkeypatch.setattr(cli, "parse_remote", lambda *args, **kwargs: fake)
    monkeypatch.setattr(cli, "stage_remote_for_read", lambda *args, **kwargs: (archive, None))
    assert main(["info", fake.raw]) == 0
    output = capsys.readouterr().out
    assert "Transport" in output
    assert "ssh (staged locally for inspection)" in output
