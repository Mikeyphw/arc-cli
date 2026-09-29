from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from arc_cli.cli import main, _resume_entry_valid
from arc_cli.transactions import BatchManifest, TransactionJournal, transaction_scope, tx_cleanup


def _zip(path: Path, name: str, content: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, content)


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ARC_STATE_HOME", str(root))
    return root


def test_explain_convert_is_rich_and_zero_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    state = _state(monkeypatch, tmp_path)
    source = tmp_path / "sample.zip"
    _zip(source, "a.txt", "hello")
    assert main(["explain", "--json", "convert", str(source), "-tgz", "--progress", "never"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "convert"
    assert payload["mutation"] is False
    decisions = {item["name"]: item["value"] for item in payload["plan"]["decisions"]}
    assert decisions["strategy"] == "isolated safe member pipeline"
    assert decisions["publication"] == "local-same-filesystem-atomic-replace"
    assert decisions["verification"] == "verify-unpublished-candidate-before-publish"
    assert not (tmp_path / "sample.tgz").exists()
    assert not state.exists()


def test_explain_create_uses_real_backend_plan_without_journal(tmp_path: Path, monkeypatch, capsys) -> None:
    state = _state(monkeypatch, tmp_path)
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")
    target = tmp_path / "a.tar"
    assert main(["explain", "--json", "create", str(target), str(source), "--progress", "never"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"]["stages"]
    assert {d["name"] for d in payload["plan"]["decisions"]} >= {"format", "backend", "publication"}
    assert not target.exists()
    assert not state.exists()


def test_create_records_durable_publication_journal(tmp_path: Path, monkeypatch, capsys) -> None:
    state = _state(monkeypatch, tmp_path)
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")
    target = tmp_path / "a.tar"
    assert main(["create", str(target), str(source), "--json", "--progress", "never"]) == 0
    capsys.readouterr()
    journals = sorted((state / "transactions").glob("*.json"))
    assert len(journals) == 1
    data = json.loads(journals[0].read_text())
    assert data["status"] == "completed"
    assert data["operation"] == "create"
    phases = [event["phase"] for event in data["events"]]
    assert "staging" in phases and "publish" in phases and "complete" in phases
    assert all(item["cleaned"] for item in data["cleanup_paths"])


def test_interruption_is_durable(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with transaction_scope("test-interrupt"):
            raise KeyboardInterrupt()
    journals = list((tmp_path / "state" / "transactions").glob("*.json"))
    data = json.loads(journals[0].read_text())
    assert data["status"] == "interrupted"
    assert data["events"][-1]["phase"] == "interrupted"


def test_recover_cleanup_removes_only_registered_temp_path(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    owned = tmp_path / "owned.tmp"
    unrelated = tmp_path / "keep.txt"
    owned.write_text("owned")
    unrelated.write_text("keep")
    with pytest.raises(RuntimeError):
        with transaction_scope("failed-op") as journal:
            tx_cleanup(owned, reason="test-owned")
            txid = journal.id
            raise RuntimeError("boom")
    # Transaction-owned temporary state is now removed automatically on the
    # failing exit; explicit recovery remains idempotent for old/partial state.
    assert not owned.exists()
    assert unrelated.read_text() == "keep"
    assert main(["recover", txid, "--cleanup", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "cleaned"
    assert unrelated.read_text() == "keep"


def test_batch_resume_reuses_unchanged_verified_items(tmp_path: Path, monkeypatch, capsys) -> None:
    state = _state(monkeypatch, tmp_path)
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, "a.txt", "aaa")
    _zip(b, "b.txt", "bbb")
    argv = ["convert", str(a), str(b), "-tgz", "--batch", "--json", "--progress", "never"]
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    batch_id = first[0]["batch_id"]
    mtimes = [(tmp_path / "a.tgz").stat().st_mtime_ns, (tmp_path / "b.tgz").stat().st_mtime_ns]
    assert main([*argv[:-3], "--resume", "--json", "--progress", "never"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert [item["resumed"] for item in second] == [True, True]
    assert all(item["reused_evidence"] for item in second)
    assert [(tmp_path / "a.tgz").stat().st_mtime_ns, (tmp_path / "b.tgz").stat().st_mtime_ns] == mtimes
    assert (state / "batches" / f"{batch_id}.json").is_file()


def test_batch_resume_invalidates_only_changed_source(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, "a.txt", "aaa")
    _zip(b, "b.txt", "bbb")
    base = ["convert", str(a), str(b), "-tgz", "--batch", "--json", "--progress", "never"]
    assert main(base) == 0
    capsys.readouterr()
    _zip(a, "a.txt", "changed")
    resume = ["convert", str(a), str(b), "-tgz", "--batch", "--resume", "--json", "--progress", "never"]
    assert main(resume) == 0
    result = json.loads(capsys.readouterr().out)
    assert result[0]["resumed"] is False
    assert result[1]["resumed"] is True


def test_batch_resume_refuses_tampered_destination_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    _zip(a, "a.txt", "aaa")
    _zip(b, "b.txt", "bbb")
    base = ["convert", str(a), str(b), "-tgz", "--batch", "--json", "--progress", "never"]
    assert main(base) == 0
    capsys.readouterr()
    (tmp_path / "b.tgz").write_bytes(b"tampered")
    resume = ["convert", str(a), str(b), "-tgz", "--batch", "--resume", "--json", "--progress", "never"]
    assert main(resume) != 0
    capsys.readouterr()
    assert (tmp_path / "b.tgz").read_bytes() == b"tampered"

    forced = ["convert", str(a), str(b), "-tgz", "--batch", "--resume", "--force", "--json", "--progress", "never"]
    assert main(forced) == 0
    result = json.loads(capsys.readouterr().out)
    assert result[0]["resumed"] is True
    assert result[1]["resumed"] is False
    assert (tmp_path / "b.tgz").read_bytes() != b"tampered"


def test_resume_requires_batch(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    source = tmp_path / "a.zip"
    _zip(source, "a.txt", "aaa")
    assert main(["convert", str(source), "-tgz", "--resume", "--json", "--progress", "never"]) != 0


def test_custom_batch_id_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    source = tmp_path / "a.zip"
    other = tmp_path / "b.zip"
    _zip(source, "a.txt", "aaa")
    _zip(other, "b.txt", "bbb")
    with pytest.raises(ValueError):
        BatchManifest.open("../escape", policy={})


def test_remote_resume_evidence_is_not_reused_without_remote_identity(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    job = {"source": "ssh://box/archive.zip", "destination": str(tmp_path / "out.tgz")}
    dest = tmp_path / "out.tgz"
    dest.write_text("dest")
    from arc_cli.transactions import file_fingerprint
    entry = {
        "status": "completed",
        "source_fingerprint": {"kind": "remote-unproven", "path": "ssh://box/archive.zip", "reusable": False},
        "destination_fingerprint": file_fingerprint(dest),
    }
    assert _resume_entry_valid(entry, job, {}) is False


def test_recover_lists_failed_transactions(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    journal = TransactionJournal.create("boom")
    journal.fail("broken")
    assert main(["recover", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == journal.id and item["status"] == "failed" for item in payload["transactions"])



def test_custom_batch_id_policy_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    BatchManifest.open("stable-id", policy={"level": 1}, reset=True)
    with pytest.raises(ValueError, match="different conversion policy"):
        BatchManifest.open("stable-id", policy={"level": 9}, reset=False)


def test_recover_rejects_unsafe_transaction_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    assert main(["recover", "../outside", "--json"]) != 0
    assert "unsafe transaction id" in capsys.readouterr().err


def test_cleanup_is_idempotent_and_does_not_delete_recreated_path(tmp_path: Path, monkeypatch) -> None:
    _state(monkeypatch, tmp_path)
    owned = tmp_path / "owned.tmp"
    owned.write_text("temporary")
    journal = TransactionJournal.create("cleanup-idempotence")
    journal.register_cleanup(owned, reason="test-owned")
    journal.fail("boom")
    first = journal.cleanup()
    assert first and first[0]["removed"] is True
    assert not owned.exists()
    owned.write_text("new legitimate content")
    second = journal.cleanup()
    assert second == []
    assert owned.read_text() == "new legitimate content"


def test_cleaned_transactions_are_not_listed_as_recoverable(tmp_path: Path, monkeypatch, capsys) -> None:
    _state(monkeypatch, tmp_path)
    journal = TransactionJournal.create("cleanup-listing")
    temp = tmp_path / "candidate.tmp"
    temp.write_text("temporary")
    journal.register_cleanup(temp)
    journal.fail("boom")
    journal.cleanup()
    assert main(["recover", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(item["id"] != journal.id for item in payload["transactions"])
    assert main(["recover", "--all", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == journal.id and item["status"] == "cleaned" for item in payload["transactions"])
