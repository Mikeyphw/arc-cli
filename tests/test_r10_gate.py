from __future__ import annotations

import contextlib
import io
import json
import os
import tarfile
from pathlib import Path

from arc_cli.cli import main
from arc_cli.execution import ExecutionStage


def _tar_file(path: Path, members: dict[str, str]) -> None:
    root = path.parent / f"{path.stem}-src"
    root.mkdir(exist_ok=True)
    with tarfile.open(path, "w") as tf:
        for name, text in members.items():
            source = root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(text, encoding="utf-8")
            tf.add(source, arcname=name)


def test_gate_extract_rename_preserves_old_and_publishes_new(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar"
    _tar_file(archive, {"a.txt": "new"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.txt").write_text("old", encoding="utf-8")

    assert main([
        "extract", str(archive), "-o", str(out),
        "--destination-policy", "rename", "--progress", "never", "--quiet",
    ]) == 0
    assert (out / "a.txt").read_text(encoding="utf-8") == "new"
    assert (out / "a.txt.old.1").read_text(encoding="utf-8") == "old"


def test_gate_skip_identical_proves_symlink_target_before_mutation(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "target").write_text("payload", encoding="utf-8")
    (source / "link").symlink_to("target")
    archive = tmp_path / "links.tar"
    with tarfile.open(archive, "w") as tf:
        tf.add(source / "link", arcname="link", recursive=False)

    out = tmp_path / "out"
    out.mkdir()
    (out / "link").symlink_to("target")
    assert main([
        "extract", str(archive), "-o", str(out),
        "--destination-policy", "skip-identical", "--progress", "never", "--quiet",
    ]) == 0
    assert os.readlink(out / "link") == "target"

    (out / "link").unlink()
    (out / "link").symlink_to("other")
    assert main([
        "extract", str(archive), "-o", str(out),
        "--destination-policy", "skip-identical", "--progress", "never", "--quiet",
    ]) != 0
    assert os.readlink(out / "link") == "other"


def test_gate_update_backup_is_exact_pre_mutation_archive(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("one", encoding="utf-8")
    archive = tmp_path / "archive.tar"
    assert main(["create", str(archive), str(source), "-tar", "--progress", "never", "--quiet"]) == 0
    original = archive.read_bytes()

    source.write_text("two", encoding="utf-8")
    backup = tmp_path / "before-update.tar"
    assert main([
        "update", str(archive), str(source), "--backup-existing", str(backup),
        "--progress", "never", "--quiet",
    ]) == 0
    assert backup.read_bytes() == original
    assert archive.read_bytes() != original


def test_gate_occupied_update_backup_fails_before_archive_mutation(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("one", encoding="utf-8")
    archive = tmp_path / "archive.tar"
    assert main(["create", str(archive), str(source), "-tar", "--progress", "never", "--quiet"]) == 0
    original = archive.read_bytes()
    source.write_text("two", encoding="utf-8")
    backup = tmp_path / "occupied.tar"
    backup.write_bytes(b"do-not-clobber")

    assert main([
        "update", str(archive), str(source), "--backup-existing", str(backup),
        "--progress", "never", "--quiet",
    ]) != 0
    assert archive.read_bytes() == original
    assert backup.read_bytes() == b"do-not-clobber"


def test_gate_batch_json_is_one_clean_record_even_for_human_inner_command(tmp_path: Path) -> None:
    request = tmp_path / "batch.json"
    request.write_text(json.dumps({
        "schema": "arc.batch-input/v1",
        "operations": [{"id": "aliases", "argv": ["aliases"]}],
    }), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert main(["batch", str(request), "--json"]) == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "arc.batch-result/v1"
    assert payload["operations"][0]["status"] == "ok"
    assert "Arc executable aliases" in payload["operations"][0]["stdout"]
    assert stderr.getvalue() == ""


def test_gate_backend_command_redacts_stage_secrets() -> None:
    stage = ExecutionStage(
        kind="backend",
        argv=["7z", "a", "-psecret-value", "out.7z", "payload"],
        redact=["secret-value"],
    )
    rendered = stage.display()
    assert "secret-value" not in rendered
    assert "<redacted>" in rendered


def test_gate_r10_family_devtool_surfaces_exist() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    tests = {row["id"] for row in data.get("test", [])}
    for test_id in (
        "arc-r10-mutation-policy-progress",
        "arc-r10a-backend-command-truth",
        "arc-r10b-machine-batch",
        "arc-r10c-mutation-policy-convergence",
    ):
        assert test_id in tests
    workflows = data["targets"]["arc"]["workflows"]
    for name in ("r10", "r10a", "r10b", "r10c"):
        assert name in workflows
