from __future__ import annotations

import gzip
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

import pytest

import arc_cli.backends as backends
import arc_cli.cli as cli
from arc_cli import __version__
from arc_cli.cli import main
from arc_cli.machine import load_schema, schema_names
from arc_cli.model import ManifestEntry

ROOT = Path(__file__).resolve().parents[1]


def _tmp_names(root: Path) -> list[str]:
    prefixes = (
        ".arc-split-",
        ".arc-join-",
        ".arc-merge-",
        "arc-merge-work-",
        "arc-remote-",
        "arc-stdin-",
        "arc-manifest-",
    )
    return sorted(
        path.name
        for path in root.rglob("*")
        if any(path.name.startswith(prefix) for prefix in prefixes)
    )


def _zip(path: Path, name: str, data: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, data)


def test_gate_version_and_public_schema_identity() -> None:
    assert __version__ == "0.2.0"
    assert "split-manifest-v1" in schema_names()
    assert load_schema("split-manifest-v1")["$id"] == "arc.split-manifest/v1"


def test_gate_zip_logical_merge_and_safe_gzip_concat(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _zip(left, "one.txt", b"one")
    _zip(right, "two.txt", b"two")
    merged = tmp_path / "merged.zip"
    assert main(["merge", str(left), str(right), "-o", str(merged), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "repack"
    with zipfile.ZipFile(merged) as zf:
        assert set(zf.namelist()) == {"one.txt", "two.txt"}
        assert zf.read("one.txt") == b"one"
        assert zf.read("two.txt") == b"two"

    a = tmp_path / "a.gz"
    b = tmp_path / "b.gz"
    out = tmp_path / "both.gz"
    a.write_bytes(gzip.compress(b"A"))
    b.write_bytes(gzip.compress(b"B"))
    assert main(["merge", str(a), str(b), "-o", str(out), "--json"]) == 0
    concat = json.loads(capsys.readouterr().out)
    assert concat["strategy"] == "concat"
    assert concat["reencoded"] is False
    assert gzip.decompress(out.read_bytes()) == b"AB"
    assert _tmp_names(tmp_path) == []


def test_gate_hostile_merge_member_is_rejected_without_escape(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    hostile = tmp_path / "hostile.zip"
    benign = tmp_path / "benign.zip"
    _zip(hostile, "../escape.txt", b"escape")
    _zip(benign, "ok.txt", b"ok")
    output = tmp_path / "merged.zip"
    assert main(["merge", str(hostile), str(benign), "-o", str(output), "--quiet"]) != 0
    capsys.readouterr()
    assert not output.exists()
    assert not (tmp_path.parent / "escape.txt").exists()
    assert _tmp_names(tmp_path) == []


def test_gate_mixed_format_resolution_fails_closed_then_explicit_format_works(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    gz = tmp_path / "a.gz"
    xz = tmp_path / "b.xz"
    gz.write_bytes(gzip.compress(b"a"))
    import lzma

    xz.write_bytes(lzma.compress(b"b"))
    assert main(["merge", str(gz), str(xz), "--quiet"]) == 2
    assert "mixed inputs" in capsys.readouterr().err
    out = tmp_path / "mixed.gz"
    assert main(["merge", str(gz), str(xz), "-o", str(out), "-F", "gzip", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "repack"
    assert payload["reencoded"] is True
    assert gzip.decompress(out.read_bytes()) == b"ab"
    assert _tmp_names(tmp_path) == []


def test_gate_split_join_strict_manifest_order_and_cleanup(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    source = tmp_path / "payload.bin"
    original = b"0123456789abcdefghijklmnopqrstuvwxyz"
    source.write_bytes(original)
    assert main(["split", str(source), "--size", "8", "--json"]) == 0
    capsys.readouterr()
    manifest_path = tmp_path / "payload.bin.arc-split.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [row["index"] for row in manifest["parts"]] == list(range(1, len(manifest["parts"]) + 1))
    manifest["parts"][0], manifest["parts"][1] = manifest["parts"][1], manifest["parts"][0]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    source.unlink()
    assert main(["join", str(manifest_path), "--quiet"]) != 0
    capsys.readouterr()
    assert not source.exists()
    assert _tmp_names(tmp_path) == []


def test_gate_success_paths_leave_no_composition_temp_state(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    source = tmp_path / "data.bin"
    source.write_bytes(b"abcdefghij" * 5)
    assert main(["split", str(source), "--parts", "4", "--json"]) == 0
    capsys.readouterr()
    source.unlink()
    assert main(["join", str(tmp_path / "data.bin.part001"), "--json"]) == 0
    capsys.readouterr()
    assert _tmp_names(tmp_path) == []


def test_gate_merge_split_join_keyboard_interrupts_leave_no_temp_state(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _zip(left, "left.txt", b"left")
    _zip(right, "right.txt", b"right")
    with monkeypatch.context() as m:
        m.setattr(cli, "_merge_extract_source", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        assert main(["merge", str(left), str(right), "-o", str(tmp_path / "merged.zip"), "--strategy", "repack"]) == 130
    capsys.readouterr()
    assert _tmp_names(tmp_path) == []

    source = tmp_path / "split.bin"
    source.write_bytes(b"0123456789" * 8)
    with monkeypatch.context() as m:
        m.setattr(cli, "_volume_publish_candidate", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        assert main(["split", str(source), "--parts", "3", "--quiet"]) == 130
    capsys.readouterr()
    assert _tmp_names(tmp_path) == []

    source2 = tmp_path / "join.bin"
    source2.write_bytes(b"abcdefghij" * 8)
    assert main(["split", str(source2), "--parts", "3", "--quiet"]) == 0
    source2.unlink()
    with monkeypatch.context() as m:
        m.setattr(cli, "_volume_publish_candidate", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        assert main(["join", str(tmp_path / "join.bin.part001"), "--quiet"]) == 130
    capsys.readouterr()
    assert not source2.exists()
    assert _tmp_names(tmp_path) == []


def test_gate_real_sigint_cleans_transaction_owned_temp_and_preserves_journal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    owned = tmp_path / "owned.tmp"
    script = textwrap.dedent(
        """
        import time
        from pathlib import Path
        from arc_cli.transactions import transaction_scope, tx_cleanup

        owned = Path(__import__('sys').argv[1])
        with transaction_scope('comp-g1-sigint'):
            owned.write_text('temporary', encoding='utf-8')
            tx_cleanup(owned, reason='gate-owned')
            print('READY', flush=True)
            while True:
                time.sleep(1)
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["ARC_STATE_HOME"] = str(state)
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(owned)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY"
    proc.send_signal(signal.SIGINT)
    _out, err = proc.communicate(timeout=10)
    assert proc.returncode != 0, err
    assert not owned.exists()
    journals = list((state / "transactions").glob("*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text(encoding="utf-8"))
    assert journal["status"] == "interrupted"
    assert journal["cleanup_paths"][0]["cleaned"] is True


def test_gate_backend_manifest_materialization_cleans_on_interrupt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    entry = ManifestEntry(tmp_path / "payload", "payload", 1)
    monkeypatch.setattr(backends.os, "fsencode", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        backends._manifest_file([entry], nul=True)
    assert not list(tmp_path.glob("arc-manifest-*"))


def test_gate_machine_explain_and_batch_surfaces(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    source = tmp_path / "batch.bin"
    source.write_bytes(b"composition-gate")
    assert main(["explain", "--json", "split", str(source), "--parts", "2"]) == 0
    explain = json.loads(capsys.readouterr().out)
    assert explain["operation"] == "split"
    assert explain["mutation"] is False

    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "schema": "arc.batch-input/v1",
                "operations": [
                    {"id": "split", "argv": ["split", str(source), "--parts", "2", "--json=v1"]},
                    {
                        "id": "join",
                        "argv": [
                            "join",
                            str(tmp_path / "batch.bin.part001"),
                            "-o",
                            str(tmp_path / "restored.bin"),
                            "--json=v1",
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["batch", str(batch), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert (tmp_path / "restored.bin").read_bytes() == b"composition-gate"


@pytest.mark.skipif(shutil.which("7z") is None, reason="7z backend not installed on this host")
def test_gate_7z_logical_merge_when_backend_available(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    Path("one.txt").write_text("one", encoding="utf-8")
    assert main(["create", "a.7z", "one.txt", "--quiet"]) == 0
    Path("one.txt").unlink()
    Path("two.txt").write_text("two", encoding="utf-8")
    assert main(["create", "b.7z", "two.txt", "--quiet"]) == 0
    Path("two.txt").unlink()
    assert main(["merge", "a.7z", "b.7z", "-o", "merged.7z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy"] == "repack"
    assert main(["extract", "merged.7z", "-o", "out", "--quiet"]) == 0
    assert (tmp_path / "out" / "one.txt").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "out" / "two.txt").read_text(encoding="utf-8") == "two"
