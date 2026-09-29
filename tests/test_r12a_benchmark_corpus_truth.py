from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from arc_cli import advisory


def _portable_tree(root: Path) -> Path:
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("hello" * 100, encoding="utf-8")
    (corpus / "nested").mkdir()
    (corpus / "nested" / "b.bin").write_bytes(bytes(range(64)) * 8)
    return corpus


def _tree(root: Path) -> Path:
    corpus = _portable_tree(root)
    (corpus / "empty").mkdir()
    if hasattr(os, "symlink"):
        os.symlink("a.txt", corpus / "a-link")
    return corpus


def test_corpus_digest_tracks_empty_directories_and_symlink_targets(tmp_path: Path) -> None:
    corpus = _tree(tmp_path)
    one = advisory._corpus_digest(corpus)
    assert one["directories"] == 2
    if (corpus / "a-link").is_symlink():
        assert one["symlinks"] == 1
        (corpus / "a-link").unlink()
        os.symlink("nested/b.bin", corpus / "a-link")
        two = advisory._corpus_digest(corpus)
        assert two["sha256"] != one["sha256"]
    (corpus / "empty").rmdir()
    three = advisory._corpus_digest(corpus)
    assert three["sha256"] != one["sha256"]


def test_benchmark_create_uses_portable_relative_corpus_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = _tree(tmp_path)
    seen: list[tuple[list[str], Path | None]] = []
    original = advisory._run_measured

    def capture(argv: list[str], env, *, cwd=None):
        seen.append((list(argv), Path(cwd) if cwd is not None else None))
        return original(argv, env, cwd=cwd)

    monkeypatch.setattr(advisory, "_run_measured", capture)
    payload = advisory.benchmark({}, corpus=corpus, formats=["tar"], iterations=1)
    if payload["results"][0]["status"] == "unavailable":
        pytest.fail("tar must be available for the R12A benchmark corpus gate")
    create = next((argv, cwd) for argv, cwd in seen if "create" in argv)
    argv, cwd = create
    assert cwd == corpus.parent
    source_arg = argv[argv.index("create") + 2]
    assert source_arg == corpus.name
    assert not Path(source_arg).is_absolute()
    assert payload["results"][0]["status"] == "ok"
    assert payload["results"][0]["iterations"][0]["verified_roundtrip"] is True


def test_real_directory_roundtrip_tar_and_zip(tmp_path: Path) -> None:
    corpus = _portable_tree(tmp_path)
    # TAR and ZIP are baseline Arc container formats, but backend/format
    # metadata fidelity (notably symlinks) is provider-dependent.  The common
    # cross-format gate therefore proves ordinary directory path/content
    # round trips.  Metadata-rich corpus loss is verified separately below and
    # must remain visible as verified_roundtrip=false rather than being masked.
    payload = advisory.benchmark({}, corpus=corpus, formats=["tar", "zip"], iterations=1)
    rows = {row["format"]: row for row in payload["results"]}
    for name in ("tar", "zip"):
        assert rows[name]["status"] == "ok", rows[name]
        assert rows[name]["iterations"][0]["verified_roundtrip"] is True


def test_directory_roundtrip_detects_lost_empty_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = _tree(tmp_path)
    original = advisory._run_measured

    def drop_empty(argv: list[str], env, *, cwd=None):
        result = original(argv, env, cwd=cwd)
        if "extract" in argv and result["returncode"] == 0:
            out = Path(argv[argv.index("-o") + 1])
            candidates = list(out.rglob("empty"))
            for path in candidates:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
        return result

    monkeypatch.setattr(advisory, "_run_measured", drop_empty)
    payload = advisory.benchmark({}, corpus=corpus, formats=["tar"], iterations=1)
    row = payload["results"][0]
    assert row["status"] == "failed"
    assert row["iterations"][0]["verified_roundtrip"] is False


def test_directory_roundtrip_detects_lost_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = _tree(tmp_path)
    if not (corpus / "a-link").is_symlink():
        pytest.skip("symlinks unavailable on this platform")
    original = advisory._run_measured

    def drop_link(argv: list[str], env, *, cwd=None):
        result = original(argv, env, cwd=cwd)
        if "extract" in argv and result["returncode"] == 0:
            out = Path(argv[argv.index("-o") + 1])
            for path in out.rglob("a-link"):
                if path.is_symlink():
                    path.unlink()
        return result

    monkeypatch.setattr(advisory, "_run_measured", drop_link)
    payload = advisory.benchmark({}, corpus=corpus, formats=["tar"], iterations=1)
    row = payload["results"][0]
    assert row["status"] == "failed"
    assert row["iterations"][0]["verified_roundtrip"] is False


def test_recommendation_preserves_symlink_input_kind(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("payload", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.symlink(target.name, link)
    payload = advisory.format_recommendation(link, {})
    assert payload["input"]["kind"] == "symlink"
    streams = [row for row in payload["candidates"] if row["tradeoffs"]["single_stream"]]
    assert streams and all(row["compatible_with_input"] is False for row in streams)


def test_benchmark_rejects_symlink_corpus(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"payload")
    link = tmp_path / "corpus-link"
    os.symlink(target.name, link)
    with pytest.raises(Exception, match="not a symlink"):
        advisory.benchmark({}, corpus=link, formats=["tar"], iterations=1)


def test_empty_directory_corpus_schema_shape(tmp_path: Path) -> None:
    corpus = tmp_path / "empty-corpus"
    corpus.mkdir()
    info = advisory._corpus_digest(corpus)
    assert info["kind"] == "directory"
    assert info["files"] == 0
    assert info["directories"] == 0
    assert info["symlinks"] == 0
    assert len(info["sha256"]) == 64
