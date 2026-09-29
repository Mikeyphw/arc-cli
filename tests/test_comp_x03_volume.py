from __future__ import annotations

import hashlib
import json
from pathlib import Path

from arc_cli.cli import main
from arc_cli.machine import load_schema, schema_names
from arc_cli.volume import parse_size


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_split_size_manifest_and_join_round_trip(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "payload.bin"
    original = b"0123456789abcdefghijklmnopqrstuvwxyz"
    source.write_bytes(original)

    assert main(["split", str(source), "--size", "10", "--json"]) == 0
    split = json.loads(capsys.readouterr().out)
    assert split["part_sizes"] == [10, 10, 10, 6]
    assert split["verification"]["status"] == "verified"
    manifest_path = tmp_path / "payload.bin.arc-split.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema"] == "arc.split-manifest/v1"
    assert manifest["byte_length"] == len(original)
    assert manifest["checksum"] == {"algorithm": "sha256", "value": _sha256(original)}
    assert [row["name"] for row in manifest["parts"]] == [
        "payload.bin.part001",
        "payload.bin.part002",
        "payload.bin.part003",
        "payload.bin.part004",
    ]

    source.unlink()
    assert main(["join", str(tmp_path / "payload.bin.part003"), "--json"]) == 0
    joined = json.loads(capsys.readouterr().out)
    assert joined["verification"]["status"] == "verified"
    assert source.read_bytes() == original


def test_split_parts_distribution_exact_boundary_and_zero(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "eight.bin"
    source.write_bytes(b"abcdefgh")
    assert main(["split", str(source), "--parts", "3", "--output-prefix", str(tmp_path / "three"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["part_sizes"] == [3, 3, 2]

    boundary = tmp_path / "boundary.bin"
    boundary.write_bytes(b"abcdefgh")
    assert main(["split", str(boundary), "--size", "4", "--output-prefix", str(tmp_path / "boundary-parts"), "--json"]) == 0
    exact = json.loads(capsys.readouterr().out)
    assert exact["part_sizes"] == [4, 4]

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert main(["split", str(empty), "--size", "1MiB", "--json"]) == 0
    zero = json.loads(capsys.readouterr().out)
    assert zero["part_sizes"] == [0]
    assert (tmp_path / "empty.bin.part001").read_bytes() == b""


def test_join_rejects_missing_or_corrupt_parts_before_publication(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "data.bin"
    source.write_bytes(b"abcdefghij")
    assert main(["split", str(source), "--size", "4", "--json"]) == 0
    capsys.readouterr()
    source.unlink()

    missing = tmp_path / "data.bin.part002"
    missing.unlink()
    assert main(["join", str(tmp_path / "data.bin.part001")]) != 0
    capsys.readouterr()
    assert not source.exists()

    # Restore a fresh split, then corrupt a part while preserving its size.
    source.write_bytes(b"abcdefghij")
    assert main(["split", str(source), "--size", "4", "--destination-policy", "replace", "--json"]) == 0
    capsys.readouterr()
    source.unlink()
    corrupt = tmp_path / "data.bin.part002"
    corrupt.write_bytes(b"XXXX")
    assert main(["join", str(tmp_path / "data.bin.part001")]) != 0
    capsys.readouterr()
    assert not source.exists()


def test_missing_manifest_requires_explicit_weaker_mode(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "raw.bin"
    original = b"abcdefghi"
    source.write_bytes(original)
    assert main(["split", str(source), "--size", "3", "--no-manifest", "--json"]) == 0
    capsys.readouterr()
    source.unlink()

    part = tmp_path / "raw.bin.part002"
    assert main(["join", str(part)]) != 0
    capsys.readouterr()
    assert main(["join", str(part), "--allow-missing-manifest", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verification"]["status"] == "unanchored"
    assert source.read_bytes() == original


def test_destructive_cleanup_requires_and_follows_verification(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "delete.bin"
    original = b"delete-me-after-proof"
    source.write_bytes(original)
    assert main(["split", str(source), "--size", "7", "--delete-source", "--no-verify"]) != 0
    capsys.readouterr()
    assert source.exists()

    assert main(["split", str(source), "--size", "7", "--delete-source", "--json"]) == 0
    capsys.readouterr()
    assert not source.exists()
    parts = sorted(tmp_path.glob("delete.bin.part*"))
    assert parts

    assert main(["join", str(parts[0]), "--delete-parts", "--no-verify"]) != 0
    capsys.readouterr()
    assert all(path.exists() for path in parts)
    assert main(["join", str(parts[0]), "--delete-parts", "--json"]) == 0
    capsys.readouterr()
    assert source.read_bytes() == original
    assert not any(path.exists() for path in parts)
    assert (tmp_path / "delete.bin.arc-split.json").exists()


def test_split_join_dry_run_explain_machine_and_schema(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "plan.bin"
    source.write_bytes(b"123456789")

    assert main(["split", str(source), "--parts", "2", "--dry-run", "--json=v1"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert machine["operation"] == "split"
    assert machine["result"]["dry_run"] is True
    assert not (tmp_path / "plan.bin.part001").exists()

    assert main(["explain", "--json", "split", str(source), "--parts", "2", "--output-prefix", str(tmp_path / "explain")]) == 0
    explain = json.loads(capsys.readouterr().out)
    assert explain["operation"] == "split"
    assert any(row["name"] == "split-mode" for row in explain["plan"]["decisions"])

    assert "split-manifest-v1" in schema_names()
    schema = load_schema("split-manifest-v1")
    assert schema["$id"] == "arc.split-manifest/v1"


def test_size_parser_binary_and_decimal_units():
    assert parse_size("1KiB") == 1024
    assert parse_size("2M") == 2_000_000
    assert parse_size("3MiB") == 3 * 1024 * 1024


def test_split_reuses_remote_read_staging(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    staged = tmp_path / "staged-download.bin"
    staged.write_bytes(b"remote-bytes")

    def fake_stage(location, config, *, progress=False):
        assert location.basename == "blob.bin"
        return staged, None

    monkeypatch.setattr("arc_cli.cli.stage_remote_for_read", fake_stage)
    assert main([
        "split",
        "rclone://demo/path/blob.bin",
        "--size",
        "6",
        "--output-dir",
        str(tmp_path / "parts"),
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["part_count"] == 2
    assert (tmp_path / "parts" / "blob.bin.arc-split.json").is_file()


def test_generic_batch_can_run_split_then_join(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "batch.bin"
    restored = tmp_path / "restored.bin"
    original = b"batch-volume-protocol"
    source.write_bytes(original)
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({
        "schema": "arc.batch-input/v1",
        "operations": [
            {"id": "split", "argv": ["split", str(source), "--size", "5", "--json"]},
            {"id": "join", "argv": ["join", str(tmp_path / "batch.bin.part001"), "-o", str(restored), "--json"]},
        ],
    }))
    assert main(["batch", str(batch), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert [row["status"] for row in payload["operations"]] == ["ok", "ok"]
    assert restored.read_bytes() == original
