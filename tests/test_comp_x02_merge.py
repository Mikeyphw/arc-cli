from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from arc_cli.cli import main


def have(*names: str) -> bool:
    return all(shutil.which(name) for name in names)


@pytest.mark.skipif(not have("tar"), reason="tar required")
def test_merge_tar_repacks_logical_members(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("one.txt").write_text("one", encoding="utf-8")
    assert main(["create", "a.tar", "one.txt", "--quiet"]) == 0
    Path("one.txt").unlink()
    Path("two.txt").write_text("two", encoding="utf-8")
    assert main(["create", "b.tar", "two.txt", "--quiet"]) == 0
    Path("two.txt").unlink()

    assert main(["merge", "a.tar", "b.tar", "-o", "merged.tar", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "repack"
    assert payload["reencoded"] is True
    assert payload["format"] == "tar"
    assert main(["extract", "merged.tar", "-o", "out", "--progress", "never", "--quiet"]) == 0
    assert (tmp_path / "out" / "one.txt").read_text() == "one"
    assert (tmp_path / "out" / "two.txt").read_text() == "two"


def test_merge_gzip_auto_concat_is_zero_reencode(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"A\n"))
    Path("b.gz").write_bytes(gzip.compress(b"B\n"))
    assert main(["merge", "a.gz", "b.gz", "-o", "both.gz", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "concat"
    assert payload["reencoded"] is False
    assert gzip.decompress(Path("both.gz").read_bytes()) == b"A\nB\n"


def test_merge_concat_rejects_container_archives(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("x.txt").write_text("x")
    assert main(["create", "a.tar", "x.txt", "--quiet"]) == 0
    assert main(["create", "b.tar", "x.txt", "--quiet"]) == 0
    assert main(["merge", "a.tar", "b.tar", "--strategy", "concat", "--quiet"]) == 3
    assert "same-format single compressed streams" in capsys.readouterr().err


@pytest.mark.skipif(not have("tar"), reason="tar required")
def test_merge_member_conflict_fail_replace_skip_rename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("same.txt").write_text("first")
    assert main(["create", "a.tar", "same.txt", "--quiet"]) == 0
    Path("same.txt").write_text("second")
    assert main(["create", "b.tar", "same.txt", "--quiet"]) == 0
    Path("same.txt").unlink()

    assert main(["merge", "a.tar", "b.tar", "-o", "fail.tar", "--quiet"]) == 8
    assert main(["merge", "a.tar", "b.tar", "-o", "replace.tar", "--member-conflict", "replace", "--quiet"]) == 0
    assert main(["extract", "replace.tar", "-o", "r", "--quiet"]) == 0
    assert (tmp_path / "r" / "same.txt").read_text() == "second"

    assert main(["merge", "a.tar", "b.tar", "-o", "skip.tar", "--member-conflict", "skip", "--quiet"]) == 0
    assert main(["extract", "skip.tar", "-o", "s", "--quiet"]) == 0
    assert (tmp_path / "s" / "same.txt").read_text() == "first"

    assert main(["merge", "a.tar", "b.tar", "-o", "rename.tar", "--member-conflict", "rename", "--quiet"]) == 0
    assert main(["extract", "rename.tar", "-o", "n", "--quiet"]) == 0
    assert (tmp_path / "n" / "same.txt").read_text() == "first"
    assert (tmp_path / "n" / "same.merge2.txt").read_text() == "second"


def test_merge_mixed_formats_require_output_format(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"a"))
    Path("b.xz").write_bytes(__import__("lzma").compress(b"b"))
    assert main(["merge", "a.gz", "b.xz", "--quiet"]) == 2
    assert "mixed inputs" in capsys.readouterr().err


def test_merge_dry_run_and_machine_v1(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"a"))
    Path("b.gz").write_bytes(gzip.compress(b"b"))
    assert main(["merge", "a.gz", "b.gz", "--dry-run", "--json=v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.machine/v1"
    assert payload["operation"] == "merge"
    assert payload["result"]["strategy"] == "concat"
    assert payload["result"]["dry_run"] is True
    assert not Path("a.merged.gz").exists()


def test_explain_merge_uses_real_dry_run_path(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"a"))
    Path("b.gz").write_bytes(gzip.compress(b"b"))
    assert main(["explain", "--json", "merge", "a.gz", "b.gz"]) == 0
    payload = json.loads(capsys.readouterr().out)
    decisions = {item["name"]: item["value"] for item in payload["plan"]["decisions"]}
    assert decisions["merge-strategy"] == "concat"
    assert not Path("a.merged.gz").exists()


def test_merge_delete_inputs_rejects_output_aliasing_input(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"a"))
    Path("b.gz").write_bytes(gzip.compress(b"b"))
    assert main(["merge", "a.gz", "b.gz", "-o", "a.gz", "--destination-policy", "replace", "--delete-inputs", "--quiet"]) == 2
    assert "output is also an input" in capsys.readouterr().err
    assert Path("a.gz").exists()


def test_merge_mixed_stream_formats_repack_to_one_logical_stream(tmp_path: Path, monkeypatch, capsys):
    import lzma

    monkeypatch.chdir(tmp_path)
    Path("a.gz").write_bytes(gzip.compress(b"A"))
    Path("b.xz").write_bytes(lzma.compress(b"B"))
    assert main(["merge", "a.gz", "b.xz", "-o", "mixed.gz", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "repack"
    assert payload["reencoded"] is True
    assert gzip.decompress(Path("mixed.gz").read_bytes()) == b"AB"


def test_merge_container_to_stream_fails_closed_as_ambiguous(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _zip = __import__("zipfile").ZipFile
    with _zip("a.zip", "w") as zf:
        zf.writestr("one.txt", "one")
    Path("b.gz").write_bytes(gzip.compress(b"B"))
    assert main(["merge", "a.zip", "b.gz", "-o", "mixed.gz", "--quiet"]) == 3
    assert "stream merge output can represent only stream inputs" in capsys.readouterr().err
    assert not Path("mixed.gz").exists()
