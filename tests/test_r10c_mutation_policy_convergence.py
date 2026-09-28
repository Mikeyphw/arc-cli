from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path

import pytest

from arc_cli.cli import (
    _remote_backup_native_args,
    _remote_create_native_args,
    _remote_extract_native_args,
    main,
    parser,
)
from arc_cli.errors import ConflictError
from arc_cli.mutation_policy import snapshot_existing
from arc_cli.remote import RemoteLocation, parse_remote


def _tar(path: Path, members: dict[str, str]) -> None:
    root = path.parent / (path.stem + "-src")
    root.mkdir(exist_ok=True)
    with tarfile.open(path, "w") as tf:
        for name, text in members.items():
            source = root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(text, encoding="utf-8")
            tf.add(source, arcname=name)


def _options(command: str) -> set[str]:
    root = parser()
    choices = next(action for action in root._actions if action.dest == "command").choices
    return {option for action in choices[command]._actions for option in action.option_strings}


def test_inplace_mutators_expose_backup_without_fake_destination_policy() -> None:
    assert "--destination-policy" in _options("create")
    assert "--destination-policy" in _options("extract")
    assert "--destination-policy" in _options("convert")
    for command in ("add", "update", "remove"):
        assert "--backup-existing" in _options(command)
        assert "--destination-policy" not in _options(command)


def test_snapshot_existing_copies_without_moving_or_clobbering(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"before")
    backup = snapshot_existing(archive, "auto")
    assert backup is not None
    assert archive.read_bytes() == b"before"
    assert backup.read_bytes() == b"before"

    explicit = tmp_path / "saved.tar"
    explicit.write_bytes(b"occupied")
    with pytest.raises(ConflictError):
        snapshot_existing(archive, str(explicit))
    assert explicit.read_bytes() == b"occupied"


def test_extract_explicit_fail_overrides_legacy_overwrite(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar"
    _tar(archive, {"a.txt": "new"})
    out = tmp_path / "out"
    out.mkdir()
    target = out / "a.txt"
    target.write_text("old", encoding="utf-8")

    rc = main([
        "extract", str(archive), "-o", str(out), "--overwrite",
        "--destination-policy", "fail", "--progress", "never", "--quiet",
    ])
    assert rc != 0
    assert target.read_text(encoding="utf-8") == "old"


def test_extract_explicit_replace_overrides_legacy_rename(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar"
    _tar(archive, {"a.txt": "new"})
    out = tmp_path / "out"
    out.mkdir()
    target = out / "a.txt"
    target.write_text("old", encoding="utf-8")

    assert main([
        "extract", str(archive), "-o", str(out), "--rename-existing",
        "--destination-policy", "replace", "--progress", "never", "--quiet",
    ]) == 0
    assert target.read_text(encoding="utf-8") == "new"
    assert not (out / "a.txt.old.1").exists()


def test_extract_skip_identical_proves_bytes_before_skipping(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar"
    _tar(archive, {"a.txt": "same"})
    out = tmp_path / "out"
    out.mkdir()
    target = out / "a.txt"
    target.write_text("same", encoding="utf-8")

    assert main([
        "extract", str(archive), "-o", str(out), "--destination-policy", "skip-identical",
        "--progress", "never", "--quiet",
    ]) == 0
    assert target.read_text(encoding="utf-8") == "same"

    target.write_text("different", encoding="utf-8")
    rc = main([
        "extract", str(archive), "-o", str(out), "--destination-policy", "skip-identical",
        "--progress", "never", "--quiet",
    ])
    assert rc != 0
    assert target.read_text(encoding="utf-8") == "different"


def test_add_and_remove_backup_existing_preserve_pre_mutation_archive(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive.tar"
    _tar(archive, {"a.txt": "a"})
    new_file = tmp_path / "new.txt"
    new_file.write_text("new", encoding="utf-8")
    before_add = tmp_path / "before-add.tar"

    assert main([
        "add", str(archive), str(new_file), "--backup-existing", str(before_add),
        "--progress", "never", "--json",
    ]) == 0
    add_payload = json.loads(capsys.readouterr().out)
    assert add_payload["backup_existing"] == str(before_add)
    with tarfile.open(before_add) as tf:
        assert tf.getnames() == ["a.txt"]

    before_remove = tmp_path / "before-remove.tar"
    assert main([
        "remove", str(archive), "a.txt", "--backup-existing", str(before_remove),
        "--progress", "never", "--json",
    ]) == 0
    remove_payload = json.loads(capsys.readouterr().out)
    assert remove_payload["backup_existing"] == str(before_remove)
    with tarfile.open(before_remove) as tf:
        assert "a.txt" in tf.getnames()
    with tarfile.open(archive) as tf:
        assert "a.txt" not in tf.getnames()


def test_remote_native_forwards_policy_and_backup_authority() -> None:
    create_args = argparse.Namespace(
        level=None, threads=None, add_extension=False, follow_symlinks=False,
        one_file_system=False, preserve_owner=False, preserve_acls=False,
        preserve_xattrs=False, destination_policy="rename", backup_existing="auto",
        overwrite=False, filter_rules=[],
    )
    rendered = _remote_create_native_args(create_args)
    assert rendered.count("--destination-policy") == 1
    assert rendered[rendered.index("--destination-policy") + 1] == "rename"
    assert "--backup-existing" in rendered

    extract_args = argparse.Namespace(
        stdout=False, json=False, output="tablet:/out", destination_policy="skip-identical",
        overwrite=True, skip_existing=False, rename_existing=False, unsafe_paths=False,
        preserve_owner=False, preserve_acls=False, preserve_xattrs=False, filter_rules=[],
    )
    config = {"remotes": {"tablet": {"type": "ssh", "host": "tablet"}}}
    location = parse_remote("tablet:/archive.tar", config, probe_rclone=False)
    assert location is not None
    rendered = _remote_extract_native_args(extract_args, config, location)
    assert "--destination-policy" in rendered
    assert rendered[rendered.index("--destination-policy") + 1] == "skip-identical"
    # Explicit policy is forwarded together with any literal legacy argv; the
    # delegated Arc normalizes explicit policy as authoritative.
    assert "--overwrite" in rendered

    assert _remote_backup_native_args(argparse.Namespace(backup_existing="auto")) == ["--backup-existing"]
    assert _remote_backup_native_args(argparse.Namespace(backup_existing="saved.tar")) == ["--backup-existing", "saved.tar"]


def test_r10c_devtool_ownership_is_first_class() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / ".devtool.toml").read_text(encoding="utf-8"))
    assert data["test_profiles"]["r10c"]["pytest_tests"][0] == "tests/test_r10c_mutation_policy_convergence.py"
    tests = {item["id"] for item in data["test"]}
    assert "arc-r10c-mutation-policy-convergence" in tests
    workflow = data["targets"]["arc"]["workflows"]["r10c"]
    assert any(node["ref"] == "test:arc-r10c-mutation-policy-convergence" for node in workflow)
    assert data["wrapper"]["commands"]["r10c"]["workflow"] == "r10c"
