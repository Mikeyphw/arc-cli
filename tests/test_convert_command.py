from __future__ import annotations

import gzip
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from arc_cli import cli
from arc_cli.cli import main
from arc_cli.formats import detect


def _zip(path: Path, files: dict[str, bytes], *, password: str | None = None) -> None:
    root = path.parent / (path.stem + "-src")
    root.mkdir()
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if password:
        subprocess.run(["zip", "-q", "-P", password, str(path), *files], cwd=root, check=True)
    else:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)


def test_convert_zip_to_tgz_is_verified_and_keeps_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha\n", "b.txt": b"beta\n"})
    assert main(["convert", str(src), "-tgz", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    dest = tmp_path / "sample.tgz"
    assert src.exists()
    assert dest.exists()
    assert detect(dest).canonical == "tar.gzip"
    assert result["destination_format"] == "tar.gzip"
    assert result["members"] == 2
    assert result["verified"] is True
    assert result["original_bytes"] == src.stat().st_size
    assert result["converted_bytes"] == dest.stat().st_size
    assert "size_change_percent" in result and "ratio" in result


def test_convert_selector_is_authoritative_over_misleading_destination_suffix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "misleading.rar"
    assert main(["convert", str(src), str(dest), "-tgz", "--json"]) == 0
    capsys.readouterr()
    assert dest.exists()
    assert detect(dest).canonical == "tar.gzip"


def test_convert_plain_format_is_authoritative_without_rewriting_conflicting_suffix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "kept-name.rar"
    assert main(["convert", str(src), str(dest), "-F", "tar.gzip", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["destination"]) == dest
    assert detect(dest).canonical == "tar.gzip"


def test_convert_infers_target_from_destination_when_no_explicit_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "inferred.tgz"
    assert main(["convert", str(src), str(dest), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["destination_format"] == "tar.gzip"
    assert detect(dest).canonical == "tar.gzip"


def test_convert_refuses_to_guess_target_without_format_or_destination_suffix(
    tmp_path: Path
) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "extensionless"
    assert main(["convert", str(src), str(dest), "--json"]) != 0
    assert not dest.exists()


def test_convert_include_only_narrows_container_to_one_stream_member(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha", "b.txt": b"beta"})
    dest = tmp_path / "one.zst"
    assert main(["convert", str(src), str(dest), "-zst", "--include", "a.txt", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert detect(dest).canonical == "zstd"
    assert result["members"] == 1


def test_convert_multi_member_to_stream_without_selection_fails(tmp_path: Path) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha", "b.txt": b"beta"})
    assert main(["convert", str(src), "-zst", "--json"]) != 0
    assert not (tmp_path / "sample.zst").exists()


def test_convert_batch_preflights_and_derives_each_destination(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, {"a.txt": b"a"})
    _zip(b, {"b.txt": b"b"})
    assert main(["convert", str(a), str(b), "-txz", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {Path(row["destination"]).name for row in rows} == {"a.txz", "b.txz"}
    assert (tmp_path / "a.txz").exists() and (tmp_path / "b.txz").exists()


def test_convert_existing_target_shaped_second_operand_is_destination_not_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "source.zip"
    dest = tmp_path / "dest.zip"
    _zip(src, {"a.txt": b"new"})
    _zip(dest, {"old.txt": b"old"})
    assert main(["convert", str(src), str(dest), "-zip", "--force", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result, dict)
    assert Path(result["destination"]) == dest


def test_convert_replace_source_occurs_only_after_verified_destination(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    assert main(["convert", str(src), "-tgz", "--replace-source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not src.exists()
    assert (tmp_path / "sample.tgz").exists()
    assert result["source_removed"] is True
    assert result["verified"] is True


def test_convert_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    assert main(["convert", str(src), "-tgz", "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True
    assert plan["destination"].endswith("sample.tgz")
    assert not (tmp_path / "sample.tgz").exists()


def test_convert_source_and_destination_passwords_are_independent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if not shutil_which("zip") or not shutil_which("unzip"):
        pytest.skip("Info-ZIP tools unavailable")
    src = tmp_path / "private.zip"
    _zip(src, {"secret.txt": b"secret"}, password="old-pass")
    dest = tmp_path / "rotated.zip"
    assert main([
        "convert", str(src), str(dest), "-zip",
        "--source-password", "old-pass",
        "--password", "new-pass",
        "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is True
    proc = subprocess.run(["unzip", "-p", "-P", "new-pass", str(dest), "secret.txt"], capture_output=True, check=True)
    assert proc.stdout == b"secret"


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def test_convert_failed_verification_never_publishes_local_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import arc_cli.cli as cli

    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "sample.tgz"

    monkeypatch.setattr(cli, "_verify_archive_path", lambda *a, **k: (False, "test", "simulated failure"))
    assert main(["convert", str(src), str(dest), "-tgz", "--json"]) != 0
    assert src.exists()
    assert not dest.exists()
    assert not list(tmp_path.glob(f".{dest.name}.arc-convert-candidate-*"))


def test_convert_failed_verification_does_not_clobber_force_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import arc_cli.cli as cli

    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "existing.tgz"
    original = b"keep-this-exactly"
    dest.write_bytes(original)

    monkeypatch.setattr(cli, "_verify_archive_path", lambda *a, **k: (False, "test", "simulated failure"))
    assert main(["convert", str(src), str(dest), "-tgz", "--force", "--json"]) != 0
    assert dest.read_bytes() == original
    assert src.exists()


def test_convert_plain_format_add_extension_matches_create_semantics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "sample.zip"
    _zip(src, {"a.txt": b"alpha"})

    literal = tmp_path / "literal-name"
    assert main(["convert", str(src), str(literal), "-F", "tar.gz", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert Path(first["destination"]) == literal
    assert literal.exists()
    assert detect(literal).canonical == "tar.gzip"

    extended = tmp_path / "extended-name"
    assert main(["convert", str(src), str(extended), "-F", "tar.gz", "--add-extension", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert Path(second["destination"]) == tmp_path / "extended-name.tar.gz"

    short = tmp_path / "short-name"
    assert main(["convert", str(src), str(short), "-tgz", "--json"]) == 0
    third = json.loads(capsys.readouterr().out)
    assert Path(third["destination"]) == tmp_path / "short-name.tgz"


def test_convert_stream_to_container_uses_sensible_logical_member_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import gzip

    src = tmp_path / "database.sql.gz"
    with gzip.open(src, "wb") as fh:
        fh.write(b"select 1;\n")
    dest = tmp_path / "database.zip"

    assert main(["convert", str(src), str(dest), "-zip", "--json"]) == 0
    capsys.readouterr()
    with zipfile.ZipFile(dest) as zf:
        assert zf.namelist() == ["database.sql"]
        assert zf.read("database.sql") == b"select 1;\n"


def test_convert_batch_collision_is_preflighted_before_any_output(
    tmp_path: Path
) -> None:
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, {"a.txt": b"a"})
    _zip(b, {"b.txt": b"b"})
    existing = tmp_path / "b.tgz"
    existing.write_bytes(b"sentinel")

    assert main(["convert", str(a), str(b), "-tgz", "--json"]) != 0
    assert not (tmp_path / "a.tgz").exists()
    assert existing.read_bytes() == b"sentinel"


def test_convert_remote_replace_source_waits_for_published_reread_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeRemote:
        def __init__(self, raw: str, basename: str) -> None:
            self.raw = raw
            self.basename = basename
            self.kind = "ssh"

    source_remote = FakeRemote("ssh://example/source.gz", "source.gz")
    destination_remote = FakeRemote("ssh://example/out.zst", "out.zst")

    def fake_parse_remote(raw, _config, **_kwargs):
        if str(raw) == source_remote.raw:
            return source_remote
        if str(raw) == destination_remote.raw:
            return destination_remote
        return None

    def fake_download(remote, local, _config, **_kwargs):
        if remote is source_remote:
            events.append("download-source")
            Path(local).write_bytes(gzip.compress(b"payload"))
        else:
            events.append("download-published")
            Path(local).write_bytes(b"published-candidate")

    def fake_convert(_source, destination, *_args, **_kwargs):
        events.append("convert")
        Path(destination).write_bytes(b"local-candidate")

    def fake_verify(path, *_args, **_kwargs):
        if Path(path).name.startswith("verify-"):
            events.append("verify-published")
        else:
            events.append("verify-candidate")
        return True, "fake-test", None

    def fake_upload(_local, remote, _config, **_kwargs):
        assert remote is destination_remote
        events.append("upload")

    monkeypatch.setattr(cli, "parse_remote", fake_parse_remote)
    monkeypatch.setattr(cli, "download_remote", fake_download)
    monkeypatch.setattr(cli, "_atomic_pipeline_convert", fake_convert)
    monkeypatch.setattr(cli, "_verify_archive_path", fake_verify)
    monkeypatch.setattr(cli, "upload_remote", fake_upload)
    monkeypatch.setattr(cli, "_remove_conversion_source", lambda *_args, **_kwargs: events.append("remove-source"))

    args = cli.parser().parse_args([
        "convert", source_remote.raw, destination_remote.raw, "-zst", "--replace-source", "--json"
    ])
    job = {
        "source": source_remote.raw,
        "destination": destination_remote.raw,
        "target_format": cli.parse_format("zstd"),
    }
    result = cli._convert_one(job, args, {}, None, None)
    assert result["verified"] is True
    assert result["source_removed"] is True
    assert events == [
        "download-source",
        "convert",
        "verify-candidate",
        "upload",
        "download-published",
        "verify-published",
        "remove-source",
    ]


def test_convert_remote_dry_run_labels_staging_truthfully(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([
        "convert",
        "ssh://example/tmp/data.gz",
        "rclone://backup/out.zst",
        "-zst",
        "--dry-run",
        "--json",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["strategy"] == "transport-staged stream pipeline"


def test_convert_remote_human_plan_does_not_claim_atomic_transport_publish(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "convert",
        "ssh://example/tmp/data.gz",
        "rclone://backup/out.zst",
        "-zst",
        "--dry-run",
    ]) == 0
    output = capsys.readouterr().out
    assert "transport-staged stream pipeline" in output
    assert "transport-dependent; remote re-read verified" in output
    assert "Atomic  yes" not in output


def test_convert_passwords_are_redacted_from_native_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not shutil_which("zip") or not shutil_which("unzip"):
        pytest.skip("Info-ZIP tools unavailable")
    src = tmp_path / "private.zip"
    _zip(src, {"secret.txt": b"secret"}, password="source-secret")
    dest = tmp_path / "rotated.zip"

    assert main([
        "convert", str(src), str(dest), "-zip",
        "--source-password", "source-secret",
        "--password", "destination-secret",
        "--show-command",
        "--show-native=after",
        "--json",
    ]) == 0
    captured = capsys.readouterr()
    assert "source-secret" not in captured.err
    assert "destination-secret" not in captured.err
    assert "<redacted>" in captured.err or "***" in captured.err


def test_convert_native_plan_is_truthful_for_stream_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import gzip

    src = tmp_path / "data.gz"
    with gzip.open(src, "wb") as fh:
        fh.write(b"hello" * 100)
    dest = tmp_path / "data.zst"
    assert main(["convert", str(src), str(dest), "-zst", "--show-native=after", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    plan = json.loads(captured.err)["native_plan"]
    assert result["strategy"] == "stream pipeline"
    assert plan["operation"] == "convert"
    assert any(stage["kind"] == "backend-pipeline" for stage in plan["stages"])
    rendered = "\n".join(stage["display"] for stage in plan["stages"])
    assert "gzip" in rendered
    assert "zstd" in rendered


def test_convert_native_plan_is_truthful_for_tar_recompression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import tarfile

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("alpha", encoding="utf-8")
    src = tmp_path / "bundle.tar.gz"
    with tarfile.open(src, "w:gz") as tf:
        tf.add(src_dir / "a.txt", arcname="a.txt")
    dest = tmp_path / "bundle.tar.xz"
    assert main(["convert", str(src), str(dest), "-txz", "--show-native=after", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    plan = json.loads(captured.err)["native_plan"]
    assert result["strategy"] == "tar stream recompression"
    rendered = "\n".join(stage["display"] for stage in plan["stages"])
    assert "gzip" in rendered
    assert "xz" in rendered


def test_convert_native_plan_is_truthful_for_isolated_member_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "bundle.zip"
    _zip(src, {"a.txt": b"alpha", "nested/b.txt": b"beta"})
    dest = tmp_path / "bundle.tgz"
    assert main(["convert", str(src), str(dest), "-tgz", "--show-native=after", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    plan = json.loads(captured.err)["native_plan"]
    assert result["strategy"] == "isolated safe member pipeline"
    assert len(plan["stages"]) >= 2
    rendered = "\n".join(stage["display"] for stage in plan["stages"])
    assert "unzip" in rendered or "7z" in rendered
    assert "tar" in rendered


def test_convert_native_plan_is_truthful_for_identity_reencode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not shutil_which("zip") or not shutil_which("unzip"):
        pytest.skip("Info-ZIP tools unavailable")
    src = tmp_path / "source.zip"
    _zip(src, {"a.txt": b"alpha"})
    dest = tmp_path / "copy.zip"
    assert main(["convert", str(src), str(dest), "-zip", "--show-native=after", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    plan = json.loads(captured.err)["native_plan"]
    assert result["strategy"] == "identity re-encode"
    rendered = "\n".join(stage["display"] for stage in plan["stages"])
    assert "unzip" in rendered or "7z" in rendered
    assert "zip" in rendered or "7z" in rendered


def test_convert_remote_shell_expanded_style_batch_is_resolved_without_network_on_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([
        "convert",
        "ssh://host/tmp/a.zip",
        "ssh://host/tmp/b.zip",
        "ssh://host/tmp/c.zip",
        "-tgz",
        "--dry-run",
        "--json",
    ]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["source"] for row in rows] == [
        "ssh://host/tmp/a.zip",
        "ssh://host/tmp/b.zip",
        "ssh://host/tmp/c.zip",
    ]
    assert [row["destination"] for row in rows] == [
        "ssh://host/tmp/a.tgz",
        "ssh://host/tmp/b.tgz",
        "ssh://host/tmp/c.tgz",
    ]
    assert all(row["strategy"].startswith("transport-staged ") for row in rows)
