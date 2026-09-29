from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

import arc_cli.cli as cli
import arc_cli.remote as remote
from arc_cli.cli import main
from arc_cli.remote import RemoteLocation, stage_remote_for_read
from arc_cli.transactions import transaction_scope, tx_cleanup


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("ARC_STATE_HOME", str(state))
    return state


def _only_journal(state: Path) -> dict:
    files = sorted((state / "transactions").glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _zip(path: Path, name: str, content: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, content)


def test_transaction_scope_auto_cleans_registered_temp_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state(monkeypatch, tmp_path)
    owned = tmp_path / "success.tmp"
    with transaction_scope("cleanup-success"):
        owned.write_text("temporary", encoding="utf-8")
        tx_cleanup(owned, reason="test-owned")
    assert not owned.exists()
    data = _only_journal(state)
    assert data["status"] == "completed"
    assert data["cleanup_paths"][0]["cleaned"] is True


def test_transaction_scope_auto_cleans_registered_temp_on_error_preserving_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(monkeypatch, tmp_path)
    owned = tmp_path / "failed.tmp"
    with pytest.raises(RuntimeError, match="boom"):
        with transaction_scope("cleanup-error"):
            owned.write_text("temporary", encoding="utf-8")
            tx_cleanup(owned, reason="test-owned")
            raise RuntimeError("boom")
    assert not owned.exists()
    data = _only_journal(state)
    assert data["status"] == "failed"
    assert data["cleanup_paths"][0]["cleaned"] is True
    assert data["events"][-1]["phase"] == "failed"


def test_transaction_scope_auto_cleans_registered_temp_on_interrupt_preserving_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(monkeypatch, tmp_path)
    owned = tmp_path / "interrupt.tmp"
    with pytest.raises(KeyboardInterrupt):
        with transaction_scope("cleanup-interrupt"):
            owned.write_text("temporary", encoding="utf-8")
            tx_cleanup(owned, reason="test-owned")
            raise KeyboardInterrupt()
    assert not owned.exists()
    data = _only_journal(state)
    assert data["status"] == "interrupted"
    assert data["cleanup_paths"][0]["cleaned"] is True
    assert data["events"][-1]["phase"] == "interrupted"


def test_remote_read_stage_cleans_temp_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(remote, "download_remote", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    location = RemoteLocation(kind="ssh", raw="ssh://box/archive.zip", path="/archive.zip", name="box", alias="box")
    with pytest.raises(KeyboardInterrupt):
        stage_remote_for_read(location, {}, dry_run=False)
    assert not list(tmp_path.glob("arc-remote-*"))


def test_stdin_materialization_cleans_temp_on_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"payload")})())
    monkeypatch.setattr(cli.shutil, "copyfileobj", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        cli._materialize_stdin(Path("-"), "zip")
    assert not list(tmp_path.glob("arc-stdin-*"))


def test_merge_failure_cleans_unpublished_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    output = tmp_path / "merged.zip"
    _zip(left, "left.txt", "left")
    _zip(right, "right.txt", "right")
    monkeypatch.setattr(cli, "_merge_extract_source", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("merge boom")))
    with pytest.raises(RuntimeError, match="merge boom"):
        main(["merge", str(left), str(right), "-o", str(output), "--strategy", "repack", "--progress", "never"])
    assert not output.exists()
    assert not list(tmp_path.glob(".merged.zip.arc-merge-*"))


def test_join_failure_cleans_unpublished_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"0123456789" * 20)
    assert main(["split", str(source), "--parts", "3", "--progress", "never", "--quiet"]) == 0
    source.unlink()
    manifest = tmp_path / "payload.bin.arc-split.json"
    output = tmp_path / "joined.bin"
    monkeypatch.setattr(cli, "_volume_publish_candidate", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("join boom")))
    with pytest.raises(RuntimeError, match="join boom"):
        main(["join", str(manifest), "-o", str(output), "--progress", "never"])
    assert not output.exists()
    assert not list(tmp_path.glob(".joined.bin.arc-join-*"))
