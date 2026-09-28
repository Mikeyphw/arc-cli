from argparse import Namespace
from pathlib import Path

import pytest

from arc_cli.errors import ConflictError, UsageError
from arc_cli.mutation_policy import DestinationPolicy, backup_existing, decide_destination, policy_from_args, validate_backup_policy
from arc_cli.progress import SEMANTIC_PHASES, SemanticProgress, progress_enabled


def test_destination_policy_legacy_aliases_map_to_replace():
    assert policy_from_args(Namespace(destination_policy=None, overwrite=True, force=False)) is DestinationPolicy.REPLACE
    assert policy_from_args(Namespace(destination_policy=None, overwrite=False, force=True)) is DestinationPolicy.REPLACE


def test_explicit_policy_is_authoritative_over_legacy_alias():
    assert policy_from_args(Namespace(destination_policy="rename", overwrite=True, force=False)) is DestinationPolicy.RENAME


def test_fail_replace_rename_and_skip_identical(tmp_path: Path):
    dest = tmp_path / "out.arc"
    dest.write_bytes(b"same")
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"same")
    with pytest.raises(ConflictError):
        decide_destination(dest, DestinationPolicy.FAIL, candidate=candidate)
    assert decide_destination(dest, DestinationPolicy.REPLACE, candidate=candidate).action == "replace"
    rename = decide_destination(dest, DestinationPolicy.RENAME, candidate=candidate)
    assert rename.action == "rename" and rename.backup.name == "out.arc.old.1"
    assert decide_destination(dest, DestinationPolicy.SKIP_IDENTICAL, candidate=candidate).action == "skip-identical"
    candidate.write_bytes(b"different")
    with pytest.raises(ConflictError):
        decide_destination(dest, DestinationPolicy.SKIP_IDENTICAL, candidate=candidate)


def test_backup_existing_is_recoverable_and_never_clobbers(tmp_path: Path):
    dest = tmp_path / "out.arc"
    dest.write_bytes(b"old")
    backup = backup_existing(dest, "auto")
    assert backup is not None and backup.read_bytes() == b"old" and not dest.exists()
    dest.write_bytes(b"new-old")
    explicit = tmp_path / "saved.arc"
    assert backup_existing(dest, str(explicit)) == explicit
    assert explicit.read_bytes() == b"new-old"


def test_backup_requires_replace_policy():
    with pytest.raises(UsageError):
        validate_backup_policy(DestinationPolicy.RENAME, "auto")


def test_semantic_progress_has_stable_phases_and_stays_silent_when_disabled():
    assert SEMANTIC_PHASES == ("scan", "encode", "verify", "publish")
    with SemanticProgress(False, "create", batch_index=2, batch_total=3) as progress:
        for phase in SEMANTIC_PHASES:
            progress.phase(phase, input_bytes=100, output_bytes=50)
        progress.complete(input_bytes=100, output_bytes=50)


def test_json_and_quiet_disable_progress_even_when_auto():
    assert not progress_enabled("auto", json_mode=True)
    assert not progress_enabled("auto", quiet=True)
