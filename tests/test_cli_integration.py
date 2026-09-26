from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from arc_cli.cli import main


def have(*names: str) -> bool:
    return all(shutil.which(name) for name in names)


@pytest.mark.skipif(not have("tar", "gzip"), reason="tar+gzip required")
def test_tar_gzip_round_trip_with_filter(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.txt").write_text("hello")
    (tmp_path / "src" / "drop.pyc").write_text("drop")
    assert main(["create", "a.tar.gz", "src", "--exclude", "*.pyc", "--progress", "never"]) == 0
    assert main(["extract", "a.tar.gz", "-o", "out", "--progress", "never"]) == 0
    assert (tmp_path / "out" / "src" / "keep.txt").read_text() == "hello"
    assert not (tmp_path / "out" / "src" / "drop.pyc").exists()


@pytest.mark.skipif(not have("zip", "unzip"), reason="zip+unzip required")
def test_extensionless_explicit_zip(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    assert main(["create", "archive", "--format", "zip", "f.txt", "--progress", "never"]) == 0
    assert (tmp_path / "archive").is_file()
    assert main(["extract", "archive", "--format", "zip", "-o", "out", "--progress", "never"]) == 0
    assert (tmp_path / "out" / "f.txt").read_text() == "x"


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip required")
def test_malicious_zip_path_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with zipfile.ZipFile("evil.zip", "w") as zf:
        zf.writestr("../../escape.txt", "x")
    assert main(["extract", "evil.zip", "-o", "out", "--progress", "never"]) == 7
    assert not (tmp_path.parent / "escape.txt").exists()
