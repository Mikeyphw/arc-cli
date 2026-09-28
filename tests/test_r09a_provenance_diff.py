from __future__ import annotations

import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from arc_cli.cli import main
from arc_cli.machine import load_schema, schema_names
from arc_cli.provenance import compare_fingerprints, normalize_member_name


def _zip(path: Path, files: dict[str, bytes], *, timestamp=(2024, 1, 2, 3, 4, 6), explicit_dirs: bool = False) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if explicit_dirs:
            for parent in sorted({str(Path(name).parent).replace("\\", "/") for name in files if str(Path(name).parent) != "."}):
                info = zipfile.ZipInfo(parent.rstrip("/") + "/", timestamp)
                zf.writestr(info, b"")
        for name, data in files.items():
            info = zipfile.ZipInfo(name, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)


def _tar(path: Path, files: dict[str, bytes], *, mtime: int = 1704164646, explicit_dirs: bool = True) -> None:
    import io

    with tarfile.open(path, "w") as tf:
        if explicit_dirs:
            parents = sorted({str(Path(name).parent).replace("\\", "/") for name in files if str(Path(name).parent) != "."})
            for parent in parents:
                info = tarfile.TarInfo(parent)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = mtime
                tf.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = mtime
            tf.addfile(info, io.BytesIO(data))


def _json_output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


@pytest.mark.skipif(not shutil.which("unzip") or not shutil.which("tar"), reason="zip/tar extraction backends unavailable")
def test_diff_proves_cross_format_logical_equivalence_and_separates_encoding(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.tar"
    files = {"docs/readme.txt": b"hello", "data.bin": b"\x00\x01"}
    _zip(left, files, explicit_dirs=False)
    _tar(right, files, explicit_dirs=True)

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["schema"] == "arc.archive-diff/v1"
    assert payload["equivalence"] == {
        "byte_identical": False,
        "encoding_only": True,
        "format_changed": True,
        "logical": True,
        "metadata": True,
    }
    assert payload["changes"]["counts"] == {
        "added": 0,
        "removed": 0,
        "type_changed": 0,
        "content_changed": 0,
        "metadata_changed": 0,
    }
    assert payload["left"]["digest"] == payload["right"]["digest"]
    assert payload["left"]["archive"]["byte_sha256"] != payload["right"]["archive"]["byte_sha256"]


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_timestamp_only_change_is_metadata_difference_not_logical_content_change(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _zip(left, {"a.txt": b"alpha"}, timestamp=(2023, 1, 1, 0, 0, 0))
    _zip(right, {"a.txt": b"alpha"}, timestamp=(2025, 1, 1, 0, 0, 0))

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is True
    assert payload["equivalence"]["metadata"] is False
    assert payload["equivalence"]["encoding_only"] is False
    assert payload["changes"]["counts"]["metadata_changed"] == 1
    assert payload["changes"]["content_changed"] == []


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_content_and_membership_changes_are_classified(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _zip(left, {"a.txt": b"alpha", "removed.txt": b"old"})
    _zip(right, {"a.txt": b"beta", "added.txt": b"new"})

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is False
    assert [row["path"] for row in payload["changes"]["added"]] == ["added.txt"]
    assert [row["path"] for row in payload["changes"]["removed"]] == ["removed.txt"]
    assert [row["path"] for row in payload["changes"]["content_changed"]] == ["a.txt"]


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_empty_directory_is_preserved_as_logical_content(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    with zipfile.ZipFile(left, "w") as zf:
        zf.writestr("empty/", b"")
        zf.writestr("a.txt", b"alpha")
    _zip(right, {"a.txt": b"alpha"})

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is False
    assert [row["path"] for row in payload["changes"]["removed"]] == ["empty"]


@pytest.mark.skipif(not shutil.which("unzip") or not shutil.which("tar"), reason="zip/tar extraction backends unavailable")
def test_info_fingerprint_digest_is_format_independent_and_archive_hash_is_not(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.tar"
    files = {"nested/a.txt": b"alpha"}
    _zip(left, files)
    _tar(right, files)

    assert main(["info", str(left), "--fingerprint", "--json", "--progress", "never"]) == 0
    linfo = _json_output(capsys)
    assert main(["info", str(right), "--fingerprint", "--json", "--progress", "never"]) == 0
    rinfo = _json_output(capsys)
    assert linfo["fingerprint"]["schema"] == "arc.logical-fingerprint/v1"
    assert linfo["fingerprint"]["digest"] == rinfo["fingerprint"]["digest"]
    assert linfo["fingerprint"]["archive"]["byte_sha256"] != rinfo["fingerprint"]["archive"]["byte_sha256"]
    assert "members" not in linfo["fingerprint"]


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_diff_v1_machine_envelope_contains_stable_diff_result(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    _zip(left, {"a.txt": b"alpha"})
    _zip(right, {"a.txt": b"alpha"})
    assert main(["diff", str(left), str(right), "--json=v1", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["schema"] == "arc.machine/v1"
    assert payload["operation"] == "diff"
    assert payload["result"]["schema"] == "arc.archive-diff/v1"
    assert payload["result"]["equivalence"]["logical"] is True


def test_diff_parse_error_redacts_side_specific_password(capsys) -> None:
    rc = main(["diff", "left.zip", "--left-password", "left-secret", "--json=v1"])
    assert rc == 2
    payload = _json_output(capsys)
    encoded = json.dumps(payload)
    assert "left-secret" not in encoded
    assert "***" in encoded


def test_new_provenance_schemas_are_discoverable(capsys) -> None:
    assert {"logical-fingerprint-v1", "archive-diff-v1"} <= set(schema_names())
    for name in ("logical-fingerprint-v1", "archive-diff-v1"):
        schema = load_schema(name)
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["type"] == "object"
    assert main(["schema", "archive-diff-v1"]) == 0
    assert _json_output(capsys)["title"] == "Arc archive diff v1"


def test_normalized_member_names_are_unicode_nfc_and_separator_stable() -> None:
    assert normalize_member_name("./folder\\caf\u0065\u0301.txt") == "folder/caf\u00e9.txt"


def test_compare_fingerprint_requires_member_evidence() -> None:
    base = {
        "schema": "arc.logical-fingerprint/v1",
        "schema_version": 1,
        "digest": "0" * 64,
        "metadata_digest": "0" * 64,
        "archive": {"format": "zip", "bytes": 1, "byte_sha256": "0" * 64},
    }
    with pytest.raises(ValueError, match="member evidence"):
        compare_fingerprints(base, base)

@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_fingerprint_rejects_normalized_path_collision_before_extraction(tmp_path: Path, capsys, monkeypatch) -> None:
    archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("dir/../a.txt", b"first")
        zf.writestr("a.txt", b"second")

    def must_not_extract(*_args, **_kwargs):
        pytest.fail("fingerprinting extracted an ambiguous member set before collision rejection")

    monkeypatch.setattr("arc_cli.cli._extract", must_not_extract)
    assert main(["info", str(archive), "--fingerprint", "--progress", "never"]) == 7
    captured = capsys.readouterr()
    assert "collide after logical path normalization" in captured.err


@pytest.mark.skipif(not shutil.which("gzip") or not shutil.which("xz"), reason="gzip/xz unavailable")
def test_stream_formats_share_logical_stream_identity(tmp_path: Path, capsys) -> None:
    import gzip
    import lzma

    data = (b"same logical stream\n" * 32)
    left = tmp_path / "left.gz"
    right = tmp_path / "right.xz"
    # Use GzipFile so the stream header timestamp is deterministic.
    with left.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(data)
    with lzma.open(right, "wb") as xz:
        xz.write(data)

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["left"]["digest"] == payload["right"]["digest"]
    assert payload["left"]["member_count"] == payload["right"]["member_count"] == 1
    assert payload["equivalence"]["logical"] is True
    assert payload["equivalence"]["metadata"] is True
    assert payload["equivalence"]["encoding_only"] is True


def test_normalized_member_name_collapses_safe_parent_traversal() -> None:
    assert normalize_member_name("dir/../a.txt") == "a.txt"

@pytest.mark.skipif(not shutil.which("unzip") or not shutil.which("tar"), reason="zip/tar conversion backends unavailable")
def test_convert_prove_equivalent_uses_logical_fingerprint_engine(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.zip"
    destination = tmp_path / "converted.tar"
    _zip(source, {"docs/a.txt": b"alpha", "b.bin": b"\x00\x01"})

    assert main([
        "convert", str(source), str(destination), "-F", "tar",
        "--prove-equivalent", "--json", "--progress", "never",
    ]) == 0
    payload = _json_output(capsys)
    proof = payload["equivalence"]
    assert proof["schema"] == "arc.archive-diff/v1"
    assert proof["equivalence"]["logical"] is True
    assert proof["left"]["digest"] == proof["right"]["digest"]
    assert destination.is_file()


@pytest.mark.skipif(not shutil.which("unzip") or not shutil.which("tar"), reason="zip/tar conversion backends unavailable")
def test_convert_prove_equivalent_blocks_semantic_filter_change_before_local_publish(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.zip"
    destination = tmp_path / "filtered.tar"
    _zip(source, {"keep.txt": b"keep", "drop.txt": b"drop"})

    assert main([
        "convert", str(source), str(destination), "-F", "tar",
        "--include", "keep.txt", "--prove-equivalent", "--progress", "never",
    ]) == 5
    captured = capsys.readouterr()
    assert "not logically equivalent" in captured.err
    assert not destination.exists()


@pytest.mark.skipif(not shutil.which("unzip") or not shutil.which("tar"), reason="zip/tar conversion backends unavailable")
def test_convert_prove_equivalent_dry_run_is_planned_without_fingerprinting_or_output(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.zip"
    destination = tmp_path / "planned.tar"
    _zip(source, {"a.txt": b"alpha"})

    assert main([
        "convert", str(source), str(destination), "-F", "tar",
        "--prove-equivalent", "--dry-run", "--json", "--progress", "never",
    ]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"] == "prove-source-vs-destination"
    assert payload["dry_run"] is True
    assert not destination.exists()
    decisions = {row["name"]: row["value"] for row in payload["plan"]["decisions"]}
    assert decisions["logical_equivalence"] == "prove-source-vs-destination"

@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_fingerprint_hashes_decomposed_unicode_member_via_materialized_spelling(tmp_path: Path, capsys) -> None:
    decomposed = "caf\u0065\u0301.txt"
    archive = tmp_path / "unicode.zip"
    _zip(archive, {decomposed: b"hello"})

    assert main(["info", str(archive), "--fingerprint", "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["fingerprint"]["complete"] is True
    assert payload["fingerprint"]["member_count"] == 1


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_unicode_nfc_and_nfd_archive_names_are_logically_equivalent(tmp_path: Path, capsys) -> None:
    left = tmp_path / "nfc.zip"
    right = tmp_path / "nfd.zip"
    _zip(left, {"caf\u00e9.txt": b"same"})
    _zip(right, {"caf\u0065\u0301.txt": b"same"})

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is True
    assert payload["changes"]["counts"]["content_changed"] == 0
    assert payload["left"]["digest"] == payload["right"]["digest"]


@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_implied_parent_directory_entries_do_not_change_logical_identity(tmp_path: Path, capsys) -> None:
    left = tmp_path / "parents-explicit.zip"
    right = tmp_path / "leaf-only.zip"
    with zipfile.ZipFile(left, "w") as zf:
        zf.writestr("a/", b"")
        zf.writestr("a/b/", b"")
    with zipfile.ZipFile(right, "w") as zf:
        zf.writestr("a/b/", b"")

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is True
    assert payload["left"]["member_count"] == payload["right"]["member_count"] == 1


def test_tar_member_timestamp_normalization_is_timezone_independent(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    archive = tmp_path / "time.tar"
    epoch = 1704164646  # 2024-01-02 03:04:06 UTC
    _tar(archive, {"a.txt": b"a"}, mtime=epoch, explicit_dirs=False)

    old_tz = os.environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", "America/Sao_Paulo")
        time.tzset()
        from arc_cli.safety import tar_members

        [member] = tar_members(archive)
        assert member.mtime == "2024-01-02 03:04:06"
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

@pytest.mark.skipif(not shutil.which("unzip"), reason="unzip unavailable")
def test_unicode_spelling_variants_compare_by_materialized_logical_identity(tmp_path: Path, capsys) -> None:
    left = tmp_path / "left.zip"
    right = tmp_path / "right.zip"
    decomposed = "caf\u0065\u0301.txt"
    composed = "caf\u00e9.txt"
    _zip(left, {decomposed: b"coffee"})
    _zip(right, {composed: b"coffee"})

    assert main(["diff", str(left), str(right), "--json", "--progress", "never"]) == 0
    payload = _json_output(capsys)
    assert payload["equivalence"]["logical"] is True
    assert payload["left"]["digest"] == payload["right"]["digest"]


def test_tar_member_timestamp_is_rendered_in_utc_for_portable_metadata() -> None:
    from arc_cli.safety import _tar_member_from_info

    info = tarfile.TarInfo("a.txt")
    info.size = 0
    info.mtime = 0
    member = _tar_member_from_info(info)
    assert member.mtime == "1970-01-01 00:00:00"
