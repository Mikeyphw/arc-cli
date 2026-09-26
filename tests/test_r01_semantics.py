from __future__ import annotations

import bz2
import gzip
import json
import lzma
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from arc_cli.cli import main
from arc_cli.formats import detect


def have(*names: str) -> bool:
    return all(shutil.which(name) for name in names)


def make_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


@pytest.mark.skipif(not have("unzip"), reason="unzip required")
def test_member_filters_apply_to_list_extract_and_test(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    make_zip(Path("a.zip"), {"keep.txt": "keep", "drop.log": "drop"})

    assert main(["list", "a.zip", "--exclude", "*.log", "--json", "--progress", "never"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in listed] == ["keep.txt"]

    assert main(["extract", "a.zip", "-o", "out", "--exclude", "*.log", "--progress", "never"]) == 0
    capsys.readouterr()
    assert (tmp_path / "out" / "keep.txt").read_text() == "keep"
    assert not (tmp_path / "out" / "drop.log").exists()

    assert main(["test", "a.zip", "--exclude", "*.log", "--show-command", "--dry-run", "--progress", "never"]) == 0
    captured = capsys.readouterr()
    rendered = captured.err + captured.out
    assert "keep.txt" in rendered
    assert "drop.log" not in rendered


@pytest.mark.skipif(not have("tar"), reason="tar required")
def test_dry_run_rename_existing_is_zero_write(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.txt").write_text("new")
    subprocess.run(["tar", "-cf", "a.tar", "src/file.txt"], check=True)
    target = tmp_path / "out" / "src" / "file.txt"
    target.parent.mkdir(parents=True)
    target.write_text("old")

    assert main(["extract", "a.tar", "-o", "out", "--rename-existing", "--dry-run", "--progress", "never"]) == 0
    assert target.read_text() == "old"
    assert not list(target.parent.glob("file.txt.old.*"))

    missing = tmp_path / "would-be-created"
    assert main(["extract", "a.tar", "-o", str(missing), "--dry-run", "--progress", "never"]) == 0
    assert not missing.exists()


@pytest.mark.skipif(not have("gzip"), reason="gzip required")
def test_stream_conflict_policies_are_normalized(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("payload").write_text("new")
    with Path("payload").open("rb") as src, gzip.open("payload.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    Path("payload").write_text("old")

    assert main(["extract", "payload.gz", "-o", ".", "--progress", "never"]) == 8
    assert Path("payload").read_text() == "old"

    assert main(["extract", "payload.gz", "-o", ".", "--skip-existing", "--progress", "never"]) == 0
    assert Path("payload").read_text() == "old"

    assert main(["extract", "payload.gz", "-o", ".", "--rename-existing", "--progress", "never"]) == 0
    assert Path("payload").read_text() == "new"
    assert Path("payload.old.1").read_text() == "old"

    Path("payload").write_text("stale")
    assert main(["extract", "payload.gz", "-o", ".", "--overwrite", "--progress", "never"]) == 0
    assert Path("payload").read_text() == "new"


@pytest.mark.skipif(not have("tar"), reason="tar required")
def test_list_passthrough_and_show_command_use_backend(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("f.txt").write_text("x")
    subprocess.run(["tar", "-cf", "a.tar", "f.txt"], check=True)

    assert main(["list", "a.tar", "--show-command", "--dry-run", "--", "--numeric-owner"]) == 0
    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert "tar" in rendered
    assert "--numeric-owner" in rendered

    assert main(["list", "a.tar", "--", "--numeric-owner"]) == 0
    captured = capsys.readouterr()
    assert "f.txt" in captured.out


@pytest.mark.skipif(not have("zip", "unzip"), reason="zip+unzip required")
def test_add_conflicts_but_update_replaces(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("f.txt").write_text("one")
    assert main(["create", "a.zip", "f.txt", "--progress", "never"]) == 0
    Path("f.txt").write_text("two")

    assert main(["add", "a.zip", "f.txt", "--progress", "never"]) == 8
    assert main(["update", "a.zip", "f.txt", "--progress", "never"]) == 0
    assert main(["extract", "a.zip", "-o", "out", "--progress", "never"]) == 0
    assert (tmp_path / "out" / "f.txt").read_text() == "two"


@pytest.mark.parametrize("kind", ["gzip", "bzip2", "xz", "zstd"])
def test_zero_filled_stream_is_not_promoted_to_tar(tmp_path: Path, kind: str):
    raw = b"\0" * 4096
    if kind == "gzip":
        path = tmp_path / "data.gz"
        with gzip.open(path, "wb") as fh:
            fh.write(raw)
    elif kind == "bzip2":
        path = tmp_path / "data.bz2"
        path.write_bytes(bz2.compress(raw))
    elif kind == "xz":
        path = tmp_path / "data.xz"
        path.write_bytes(lzma.compress(raw))
    else:
        if not shutil.which("zstd"):
            pytest.skip("zstd required")
        path = tmp_path / "data.zst"
        src = tmp_path / "raw"
        src.write_bytes(raw)
        subprocess.run(["zstd", "-q", "-f", "-o", str(path), str(src)], check=True)
    assert detect(path).canonical == kind


@pytest.mark.skipif(not have("gzip"), reason="gzip required")
def test_empty_targz_filename_hint_preserves_tar_container(tmp_path: Path):
    path = tmp_path / "empty.tar.gz"
    with tarfile.open(path, "w:gz"):
        pass
    assert detect(path).canonical == "tar.gzip"


@pytest.mark.skipif(not have("zip", "unzip"), reason="zip+unzip required")
def test_wrong_password_is_password_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("secret.txt").write_text("classified")
    subprocess.run(["zip", "-q", "-P", "right", "secret.zip", "secret.txt"], check=True)
    assert main(["extract", "secret.zip", "-o", "out", "--password", "wrong", "--progress", "never"]) == 6

@pytest.mark.skipif(not have("unzip"), reason="unzip required")
def test_empty_filter_selection_never_falls_back_to_all_members(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_zip(Path("a.zip"), {"one.txt": "1", "two.txt": "2"})
    assert main(["extract", "a.zip", "-o", "out", "--exclude", "*", "--progress", "never"]) == 0
    assert not (tmp_path / "out").exists()
    assert main(["test", "a.zip", "--exclude", "*", "--progress", "never"]) == 0

@pytest.mark.parametrize("kind", ["gzip", "bzip2", "xz", "zstd"])
def test_all_stream_formats_honor_skip_existing(tmp_path: Path, monkeypatch, kind: str):
    monkeypatch.chdir(tmp_path)
    raw = b"new-stream-data"
    plain = tmp_path / "payload"
    plain.write_bytes(raw)
    if kind == "gzip":
        archive = tmp_path / "payload.gz"
        with plain.open("rb") as src, gzip.open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst)
    elif kind == "bzip2":
        archive = tmp_path / "payload.bz2"
        archive.write_bytes(bz2.compress(raw))
    elif kind == "xz":
        archive = tmp_path / "payload.xz"
        archive.write_bytes(lzma.compress(raw))
    else:
        if not shutil.which("zstd"):
            pytest.skip("zstd required")
        archive = tmp_path / "payload.zst"
        subprocess.run(["zstd", "-q", "-f", "-o", str(archive), str(plain)], check=True)
    plain.write_bytes(b"existing")
    assert main(["extract", str(archive), "-o", str(tmp_path), "--skip-existing", "--progress", "never"]) == 0
    assert plain.read_bytes() == b"existing"


@pytest.mark.skipif(not have("tar"), reason="tar required")
def test_create_dry_run_does_not_create_manifest_or_archive(tmp_path: Path, monkeypatch):
    import tempfile as tempfile_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile_module, "tempdir", str(tmp_path))
    Path("input.txt").write_text("x")
    before = {p.name for p in tmp_path.iterdir()}
    assert main(["create", "a.tar", "input.txt", "--dry-run", "--progress", "never"]) == 0
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after
    assert not Path("a.tar").exists()
    assert not list(tmp_path.glob("arc-manifest-*"))


@pytest.mark.skipif(not have("gzip"), reason="gzip required")
def test_mislabeled_non_tar_gzip_content_wins_over_tar_suffix(tmp_path: Path):
    path = tmp_path / "not-really.tar.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(b"plain compressed payload, not a tar archive")
    assert detect(path).canonical == "gzip"
