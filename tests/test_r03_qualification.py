from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arc_cli.backends import InfoZipBackend, SevenZipBackend, backend_capabilities, backend_inventory, resolve_backend
from arc_cli.cli import main, parser
from arc_cli.errors import BackendUnavailable, UnsupportedFormat
from arc_cli.model import ArchiveFormat, BackendInfo, ManifestEntry
from arc_cli.qualification import PASS, SKIP_CAPABILITY, _call_arc, _mutation_case, run_qualification


def _which_map(monkeypatch, mapping: dict[str, str | None]) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: mapping.get(name))




def test_bsdtar_does_not_advertise_tar_remove():
    assert "remove" not in backend_capabilities("bsdtar")


def test_tar_remove_capability_is_probed_from_concrete_executable(tmp_path: Path):
    fake = tmp_path / "tar"
    fake.write_text("#!/bin/sh\nprintf '%s\n' 'usage: tar --delete -f ARCHIVE MEMBER'\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    assert "remove" in backend_capabilities(str(fake))


def test_tar_remove_resolver_skips_bsdtar_and_uses_delete_capable_tar(tmp_path: Path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    bsdtar = bindir / "bsdtar"
    bsdtar.write_text("#!/bin/sh\necho 'bsdtar help'\n", encoding="utf-8")
    bsdtar.chmod(bsdtar.stat().st_mode | stat.S_IXUSR)
    tar = bindir / "tar"
    tar.write_text("#!/bin/sh\necho 'usage: tar --delete -f ARCHIVE MEMBER'\n", encoding="utf-8")
    tar.chmod(tar.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))
    backend = resolve_backend(
        ArchiveFormat("tar"),
        "remove",
        {"backends": {"tar": ["bsdtar", "tar"]}},
    )
    assert backend.info.binary == "tar"
    assert "remove" in backend.info.capabilities


def test_mutation_qualification_skips_when_one_required_operation_is_unavailable(tmp_path: Path, monkeypatch):
    import arc_cli.qualification as qualification

    class DummyBackend:
        info = BackendInfo("tar", "bsdtar", "/fake/bsdtar", {"add", "update"})

    def fake_resolve(_fmt, operation, _config, *args, **kwargs):
        if operation == "remove":
            raise BackendUnavailable("no installed backend can remove tar")
        return DummyBackend()

    monkeypatch.setattr(qualification, "resolve_backend", fake_resolve)
    case = _mutation_case("tar", ".tar", tmp_path)
    assert case.status == SKIP_CAPABILITY
    assert "remove tar" in case.detail


def test_qualification_internal_arc_calls_do_not_leak_expected_stderr(tmp_path: Path, capsys):
    rc = _call_arc(["identify", "missing.bin"], tmp_path)
    assert rc != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_resolver_skips_installed_backend_missing_required_capability(monkeypatch):
    _which_map(monkeypatch, {"zip": "/fake/zip", "7z": "/fake/7z"})
    config = {"backends": {"zip_create": ["zip", "7z"]}}
    backend = resolve_backend(
        ArchiveFormat("zip"), "create", config, required_capabilities={"threads"}
    )
    assert isinstance(backend, SevenZipBackend)
    assert backend.info.binary == "7z"
    assert "threads" in backend.info.capabilities


def test_no_fallback_stops_at_first_configured_candidate(monkeypatch):
    _which_map(monkeypatch, {"zip": "/fake/zip", "7z": "/fake/7z"})
    config = {"backends": {"zip_create": ["zip", "7z"]}}
    with pytest.raises(BackendUnavailable):
        resolve_backend(
            ArchiveFormat("zip"), "create", config,
            required_capabilities={"threads"}, no_fallback=True,
        )


def test_forced_backend_is_strict_about_capabilities(monkeypatch):
    _which_map(monkeypatch, {"zip": "/fake/zip"})
    with pytest.raises(UnsupportedFormat):
        resolve_backend(
            ArchiveFormat("zip"), "create", {}, forced="zip",
            required_capabilities={"threads"},
        )


def test_missing_preferred_backend_falls_back_unless_disabled(monkeypatch):
    _which_map(monkeypatch, {"zip": None, "7z": "/fake/7z"})
    config = {"backends": {"zip_create": ["zip", "7z"]}}
    assert resolve_backend(ArchiveFormat("zip"), "create", config).info.binary == "7z"
    with pytest.raises(BackendUnavailable):
        resolve_backend(ArchiveFormat("zip"), "create", config, no_fallback=True)


def test_no_fallback_is_normalized_cli_option_and_completed():
    args = parser().parse_args(["create", "a.zip", "src", "--no-fallback"])
    assert args.no_fallback is True
    from arc_cli.completion import completion_candidates
    assert "--no-fallback" in completion_candidates(["create", "a.zip", "--no-"])




def test_sevenzip_directory_entries_use_direct_argv_not_listfile(tmp_path: Path):
    directory = tmp_path / "empty"
    directory.mkdir()
    file_path = tmp_path / "x.txt"
    file_path.write_text("x", encoding="utf-8")
    entries = [
        ManifestEntry(directory, "empty", 0, True),
        ManifestEntry(file_path, "x.txt", 1, False),
    ]
    backend = SevenZipBackend(BackendInfo("7z", "7z", "/usr/bin/7z"))
    cmd, meta = backend.command(
        "create", tmp_path / "a.7z", fmt=ArchiveFormat("7z"), entries=entries,
        extra=[], level=None, threads=None, follow_symlinks=False,
        password=None, dry_run=True,
    )
    assert not any(str(arg).startswith("@") for arg in cmd)
    assert "--" in cmd
    assert str(directory) in cmd
    assert str(file_path) in cmd
    assert meta["cleanup"] == []


def test_sevenzip_normalized_7z_archives_are_mutation_safe(tmp_path: Path):
    entry = ManifestEntry(tmp_path / "x.txt", "x.txt", 1)
    backend = SevenZipBackend(BackendInfo("7z", "7z", "/usr/bin/7z"))
    for operation in ("create", "add", "update"):
        cmd, _meta = backend.command(
            operation, tmp_path / "a.7z", fmt=ArchiveFormat("7z"), entries=[entry],
            extra=[], level=None, threads=None, follow_symlinks=False,
            password=None, dry_run=True,
        )
        assert "-ms=off" in cmd

def test_backend_inventory_exposes_each_candidate_capabilities(monkeypatch):
    _which_map(monkeypatch, {"zip": "/fake/zip", "7z": "/fake/7z", "unzip": None})
    rows = backend_inventory({"backends": {"zip_create": ["zip", "7z"], "zip_extract": ["unzip"]}})
    create = next(row for row in rows if row["role"] == "zip_create")
    assert create["candidates"][0]["installed"] is True
    assert "password" in create["candidates"][0]["capabilities"]
    assert "threads" in create["candidates"][1]["capabilities"]


def test_huge_manifest_transports_do_not_expand_argv(tmp_path: Path):
    entries = [ManifestEntry(tmp_path / f"f-{i}.txt", f"f-{i}.txt", 1) for i in range(5000)]
    seven = SevenZipBackend(BackendInfo("7z", "7z", "/usr/bin/7z"))
    cmd7, meta7 = seven.command(
        "create", tmp_path / "a.7z", fmt=ArchiveFormat("7z"), entries=entries,
        extra=[], level=None, threads=None, follow_symlinks=False, password=None, dry_run=True,
    )
    assert len(cmd7) < 32
    assert any(str(x).startswith("@") for x in cmd7)
    assert meta7["cleanup"]

    zipb = InfoZipBackend(BackendInfo("zip", "zip", "/usr/bin/zip"), "zip")
    cmdz, metaz = zipb.command(
        "create", tmp_path / "a.zip", entries=entries, extra=[], level=None,
        follow_symlinks=False, password=None,
    )
    assert len(cmdz) < 32
    assert "-@" in cmdz
    assert metaz["stdin"].count("\n") == 5000


def test_backends_json_reports_capability_inventory(capsys):
    assert main(["backends", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and "candidates" in data[0]
    assert all("capabilities" in c for row in data for c in row["candidates"])


def test_signal_interrupt_cleans_atomic_temp_and_child(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "zip"
    pidfile = tmp_path / "child.pid"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os,time,pathlib,signal,sys\n"
        "pathlib.Path(os.environ['ARC_FAKE_PID']).write_text(str(os.getpid()))\n"
        "signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))\n"
        "time.sleep(30)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "src.txt").write_text("x")
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["ARC_FAKE_PID"] = str(pidfile)
    srcroot = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = srcroot
    proc = subprocess.Popen(
        [sys.executable, "-m", "arc_cli", "create", "out.zip", "src.txt", "--backend", "zip", "--progress", "never"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline and not pidfile.exists():
        time.sleep(0.05)
    assert pidfile.exists()
    child_pid = int(pidfile.read_text())
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)
    assert proc.returncode == 130
    assert not (tmp_path / "out.zip").exists()
    assert not list(tmp_path.glob(".out.zip.arc-tmp-*"))
    assert not list(tmp_path.glob("arc-manifest-*"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zip") and shutil.which("unzip")), reason="native tar+zip tools required")
def test_r03_qualification_smoke_without_large_manifest(tmp_path: Path):
    output = tmp_path / "qualification.json"
    result = run_qualification(output, include_large_manifest=False)
    assert output.is_file()
    failures = result["summary"].get("failures", [])
    assert result["summary"]["status"] == PASS, failures
    assert result["summary"]["fail"] == 0, failures
    statuses = {row["status"] for row in result["backend_matrix"]}
    assert PASS in statuses
    assert all(row["status"] in {"PASS", "SKIPPED_BACKEND_UNAVAILABLE", "SKIPPED_CAPABILITY_UNSUPPORTED"} for row in result["backend_matrix"])
    assert all(row["status"] == PASS for row in result["safety_matrix"])


def test_failed_create_preserves_existing_archive_and_cleans_temp(tmp_path: Path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "zip"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib,sys\n"
        "for arg in sys.argv[1:]:\n"
        "    if '.arc-tmp-' in arg:\n"
        "        pathlib.Path(arg).write_bytes(b'partial')\n"
        "raise SystemExit(2)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.txt").write_text("new")
    original = b"previous-valid-archive"
    (tmp_path / "out.zip").write_bytes(original)
    assert main(["create", "out.zip", "src.txt", "--overwrite", "--backend", "zip", "--progress", "never"]) == 1
    assert (tmp_path / "out.zip").read_bytes() == original
    assert not list(tmp_path.glob(".out.zip.arc-tmp-*"))


def test_tar_failure_cleans_manifest_and_atomic_temp(tmp_path: Path, monkeypatch):
    import tempfile as tempfile_module

    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tar"
    fake.write_text("#!/usr/bin/env python3\nraise SystemExit(2)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(tempfile_module, "tempdir", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.txt").write_text("x")
    assert main(["create", "out.tar", "src.txt", "--backend", "tar", "--progress", "never"]) == 1
    assert not (tmp_path / "out.tar").exists()
    assert not list(tmp_path.glob("arc-manifest-*"))
    assert not list(tmp_path.glob(".out.tar.arc-tmp-*"))


def test_tar_compressor_resolver_uses_thread_capable_fallback(tmp_path: Path, monkeypatch):
    from arc_cli.backends import TarBackend

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("gzip", "pigz"):
        p = bindir / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    entry = ManifestEntry(tmp_path / "x", "x", 1)
    backend = TarBackend(BackendInfo("tar", "tar", "/bin/tar"))
    _cmd, meta = backend.command(
        "create", tmp_path / "a.tar.gz", fmt=ArchiveFormat("tar", "gzip"), entries=[entry],
        extra=[], level=6, threads=2, follow_symlinks=False,
        config={"backends": {"gzip": ["gzip", "pigz"]}}, password=None,
        preserve_owner=False, preserve_acls=False, preserve_xattrs=False, dry_run=True,
        no_fallback=False,
    )
    assert Path(meta["pipeline"][0]).name == "pigz"
    with pytest.raises(BackendUnavailable):
        backend.command(
            "create", tmp_path / "b.tar.gz", fmt=ArchiveFormat("tar", "gzip"), entries=[entry],
            extra=[], level=6, threads=2, follow_symlinks=False,
            config={"backends": {"gzip": ["gzip", "pigz"]}}, password=None,
            preserve_owner=False, preserve_acls=False, preserve_xattrs=False, dry_run=True,
            no_fallback=True,
        )
