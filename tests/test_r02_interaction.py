from __future__ import annotations

import os
import stat
import shutil
from pathlib import Path

import pytest

from arc_cli.backends import InfoZipBackend, RarBackend, SevenZipBackend, TarBackend, parse_7z_slt, parse_rar_lt
from arc_cli.cli import _apply_defaults, _apply_profile, _validate_yazi_context, main, parser
from arc_cli.completion import completion_candidates, completion_mode, zsh_completion
from arc_cli.errors import UnsafeArchive, UsageError
from arc_cli.interactive import choose_auto, fzf_choose, rg_files, yazi_choose
from arc_cli.model import ArchiveFormat, BackendInfo, ManifestEntry, Member
from arc_cli.progress import ProgressReporter, progress_enabled
from arc_cli.safety import validate_members


def test_completion_is_command_specific():
    identify = completion_candidates(["identify", "--"])
    create = completion_candidates(["create", "out.zip", "--"])
    extract = completion_candidates(["extract", "a.zip", "--"])
    assert "--overwrite" not in identify
    assert "--level" in create
    assert "--unsafe-paths" not in create
    assert "--unsafe-paths" in extract
    assert "--level" not in extract


def test_password_env_and_profile_completion(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[profiles.backup]\nlevel=8\n[profiles.fast]\nlevel=1\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_TEST_SECRET", "x")
    assert completion_candidates(["create", "a.zip", "--password-env", "ARC_TEST_"]) == ["ARC_TEST_SECRET"]
    assert completion_candidates(["create", "a.zip", "--profile", "ba"]) == ["backup"]


def test_completion_supports_attached_long_option_values(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[profiles.backup]\nlevel=8\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ARC_COMPLETION_SECRET", "x")
    assert completion_candidates(["extract", "a.zip", "--yazi=ou"]) == ["--yazi=output"]
    assert completion_candidates(["create", "a.zip", "--profile=ba"]) == ["--profile=backup"]
    assert completion_candidates(["create", "a.zip", "--password-env=ARC_COMPLETION_"]) == ["--password-env=ARC_COMPLETION_SECRET"]


def test_completion_mode_single_vs_multi():
    assert completion_mode(["create", ""]) == "single"
    assert completion_mode(["create", "out.zip", ""]) == "multi"
    assert completion_mode(["extract", ""]) == "single"
    assert completion_mode(["extract", "a.zip", ""]) == "multi"
    assert completion_mode(["create", "out.zip", "--level", ""]) == "single"


def test_generated_zsh_uses_nul_and_context_mode():
    text = zsh_completion()
    assert "#compdef arc" in text
    assert "__complete0" in text
    assert "__complete-mode" in text
    assert "--read0" in text
    assert "--print0" in text
    assert "[[ \"$mode\" == multi ]] && fzf_args+=(--multi)" in text
    assert "fzf --multi" not in text


def test_profile_applies_before_cli_rules_and_cli_scalars_win():
    args = parser().parse_args(["create", "out.zip", "src", "--level", "3", "--include", "keep.txt"])
    config = {
        "profiles": {
            "backup": {
                "level": 8,
                "threads": 4,
                "exclude": ["*.tmp", "*.log"],
                "include": "profile-keep.txt",
            }
        }
    }
    args.profile = "backup"
    _apply_profile(args, config)
    _apply_defaults(args, config)
    assert args.level == 3
    assert args.threads == 4
    assert args.filter_rules[:3] == [
        ("exclude", "*.tmp"),
        ("exclude", "*.log"),
        ("include", "profile-keep.txt"),
    ]
    assert args.filter_rules[-1] == ("include", "keep.txt")


def test_unknown_profile_is_rejected():
    args = parser().parse_args(["create", "out.zip", "src", "--profile", "missing"])
    with pytest.raises(UsageError):
        _apply_profile(args, {"profiles": {}})


def _fake_executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_fzf_nul_roundtrip_and_single_multi_flags(tmp_path: Path, monkeypatch):
    log = tmp_path / "args.log"
    _fake_executable(
        tmp_path / "fzf",
        "import os,sys\n"
        "open(os.environ['FZF_LOG'],'w').write('\\n'.join(sys.argv[1:]))\n"
        "parts=[x for x in sys.stdin.buffer.read().split(b'\\0') if x]\n"
        "chosen=parts if '--multi' in sys.argv else parts[:1]\n"
        "sys.stdout.buffer.write(b'\\0'.join(chosen)+(b'\\0' if chosen else b''))\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("FZF_LOG", str(log))
    odd = "line\nbreak.txt"
    assert fzf_choose([odd, "two"], multi=False, prompt="x> ") == [odd]
    args = log.read_text()
    assert "--read0" in args and "--print0" in args
    assert "--multi" not in args
    assert fzf_choose([odd, "two"], multi=True, prompt="x> ") == [odd, "two"]
    assert "--multi" in log.read_text()


def test_choose_auto_prefers_fzf_without_explicit_flag(tmp_path: Path, monkeypatch):
    _fake_executable(
        tmp_path / "fzf",
        "import sys\n"
        "parts=[x for x in sys.stdin.buffer.read().split(b'\\0') if x]\n"
        "sys.stdout.buffer.write(parts[0]+b'\\0')\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    assert choose_auto(["one", "two"], multi=False, prompt="pick> ") == ["one"]


def test_rg_files_preserves_newline_filename(tmp_path: Path, monkeypatch):
    if not os.path.exists("/usr/bin/rg"):
        pytest.skip("ripgrep unavailable")
    monkeypatch.chdir(tmp_path)
    name = "odd\nname.txt"
    (tmp_path / name).write_text("x")
    assert name in rg_files(tmp_path)


def test_yazi_chooser_cleanup_and_modes(tmp_path: Path, monkeypatch):
    _fake_executable(
        tmp_path / "yazi",
        "import pathlib,sys\n"
        "arg=next(x for x in sys.argv if x.startswith('--chooser-file='))\n"
        "p=pathlib.Path(arg.split('=',1)[1])\n"
        "p.write_bytes(b'/tmp/one\\n/tmp/two\\n')\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    assert yazi_choose(multi=False) == ["/tmp/one"]
    assert yazi_choose(multi=True) == ["/tmp/one", "/tmp/two"]


def test_yazi_cancellation_cleans_chooser_file(tmp_path: Path, monkeypatch):
    log = tmp_path / "chooser.log"
    _fake_executable(
        tmp_path / "yazi",
        "import os,sys\n"
        "arg=next(x for x in sys.argv if x.startswith('--chooser-file='))\n"
        "open(os.environ['YAZI_CHOOSER_LOG'],'w').write(arg.split('=',1)[1])\n"
        "raise SystemExit(130)\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("YAZI_CHOOSER_LOG", str(log))
    with pytest.raises(KeyboardInterrupt):
        yazi_choose(multi=False)
    chooser = Path(log.read_text())
    assert not chooser.exists()


def test_json_mode_disables_animated_progress():
    assert progress_enabled("always", json_mode=True) is False
    assert progress_enabled("always", quiet=True) is False


def test_progress_unknown_totals_are_indeterminate():
    assert ProgressReporter("x", 0, 0, False).kind == "indeterminate"
    assert ProgressReporter("x", 0, 4, False).kind == "files"
    assert ProgressReporter("x", 12, 4, False).kind == "bytes"


def test_parse_rar_lt_symlink_and_unknown_type():
    text = """
        Name: file.txt
        Type: File
        Size: 12
        Attributes: -rw-r--r--

        Name: link
        Type: Unix symbolic link
        Target: ../outside
        Size: 10
        Attributes: lrwxrwxrwx

        Name: mystery
        Type: Future redirect
        Size: 0
    """
    members = parse_rar_lt(text)
    assert [(m.name, m.kind) for m in members] == [
        ("file.txt", "file"),
        ("link", "symlink"),
        ("mystery", "unknown"),
    ]
    assert members[1].link_target == "../outside"
    with pytest.raises(UnsafeArchive):
        validate_members(members)


def test_parse_7z_slt_unknown_textual_type_fails_closed():
    members = parse_7z_slt("""
Path = junction-like
Size = 0
Type = Future Junction Variant

""")
    assert [(m.name, m.kind) for m in members] == [("junction-like", "special")]
    with pytest.raises(UnsafeArchive):
        validate_members(members)


def test_parse_7z_slt_special_and_links():
    text = """
Path = file.txt
Size = 4
Attributes = A -rw-r--r--

Path = pipe
Size = 0
Mode = prw-------

Path = link
Size = 0
Attributes = A lrwxrwxrwx
Symbolic Link = ../outside

"""
    members = parse_7z_slt(text)
    assert [(m.name, m.kind) for m in members] == [
        ("file.txt", "file"),
        ("pipe", "special"),
        ("link", "symlink"),
    ]
    with pytest.raises(UnsafeArchive):
        validate_members(members)


def test_tar_manifest_is_nul_framed_on_dry_run(tmp_path: Path):
    entry = ManifestEntry(tmp_path / "line\nbreak.txt", "line\nbreak.txt", 1)
    backend = TarBackend(BackendInfo("tar", "tar", "/bin/tar"))
    cmd, _meta = backend.command(
        "create", tmp_path / "a.tar", fmt=ArchiveFormat("tar"), entries=[entry], extra=[],
        level=None, threads=None, follow_symlinks=False, config={}, password=None,
        preserve_owner=False, preserve_acls=False, preserve_xattrs=False, dry_run=True,
    )
    assert "--null" in cmd
    assert "-T" in cmd


def test_zip_odd_filename_uses_direct_argv(tmp_path: Path):
    entry = ManifestEntry(tmp_path / "line\nbreak.txt", "line\nbreak.txt", 1)
    backend = InfoZipBackend(BackendInfo("zip", "zip", "/usr/bin/zip"), "zip")
    cmd, meta = backend.command(
        "create", tmp_path / "a.zip", entries=[entry], extra=[], level=None,
        follow_symlinks=False, password=None,
    )
    assert "-@" not in cmd
    assert "--" in cmd
    assert os.fspath(entry.source) in cmd
    assert meta.get("stdin") is None


def test_7z_and_rar_odd_filename_avoid_line_manifest(tmp_path: Path):
    entry = ManifestEntry(tmp_path / "line\nbreak.txt", "line\nbreak.txt", 1)
    seven = SevenZipBackend(BackendInfo("7z", "7z", "/usr/bin/7z"))
    cmd7, meta7 = seven.command(
        "create", tmp_path / "a.7z", fmt=ArchiveFormat("7z"), entries=[entry], extra=[],
        level=None, threads=None, follow_symlinks=False, password=None, dry_run=True,
    )
    assert not any(str(x).startswith("@") for x in cmd7)
    assert os.fspath(entry.source) in cmd7
    assert not meta7["cleanup"]

    rar = RarBackend(BackendInfo("rar", "rar", "/usr/bin/rar"), "rar")
    cmdr, metar = rar.command(
        "create", tmp_path / "a.rar", entries=[entry], extra=[], level=None,
        password=None, dry_run=True,
    )
    assert not any(str(x).startswith("@") for x in cmdr)
    assert os.fspath(entry.source) in cmdr
    assert not metar["cleanup"]


def test_yazi_context_validation():
    args = parser().parse_args(["create", "a.zip", "src", "--yazi=archive"])
    with pytest.raises(UsageError):
        _validate_yazi_context(args)
    args = parser().parse_args(["extract", "a.zip", "--yazi=output"])
    _validate_yazi_context(args)


def test_odd_filename_direct_argv_keeps_backend_extra_before_separator(tmp_path: Path):
    entry = ManifestEntry(tmp_path / "line\nbreak.txt", "line\nbreak.txt", 1)
    seven = SevenZipBackend(BackendInfo("7z", "7z", "/usr/bin/7z"))
    cmd7, _ = seven.command(
        "create", tmp_path / "a.7z", fmt=ArchiveFormat("7z"), entries=[entry], extra=["-mhe=on"],
        level=7, threads=2, follow_symlinks=False, password=None, dry_run=True,
    )
    sep = cmd7.index("--")
    assert cmd7.index("-mhe=on") < sep
    assert cmd7.index("-mx=7") < sep
    assert cmd7.index("-mmt=2") < sep

    zipb = InfoZipBackend(BackendInfo("zip", "zip", "/usr/bin/zip"), "zip")
    cmdz, _ = zipb.command(
        "create", tmp_path / "a.zip", entries=[entry], extra=["-X"], level=None,
        follow_symlinks=False, password=None,
    )
    assert cmdz.index("-X") < cmdz.index("--")

    rar = RarBackend(BackendInfo("rar", "rar", "/usr/bin/rar"), "rar")
    cmdr, _ = rar.command(
        "create", tmp_path / "a.rar", entries=[entry], extra=["-ma5"], level=None,
        password=None, dry_run=True,
    )
    assert cmdr.index("-ma5") < cmdr.index("--")


@pytest.mark.skipif(not (shutil.which("zip") and shutil.which("unzip")), reason="zip/unzip required")
def test_native_zip_roundtrip_preserves_newline_filename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    odd = "line\nbreak.txt"
    (tmp_path / "src" / odd).write_text("odd-data")
    assert main(["create", "odd.zip", "src", "--backend", "zip", "--progress", "never"]) == 0
    assert main(["extract", "odd.zip", "--backend", "unzip", "-o", "out", "--progress", "never"]) == 0
    assert (tmp_path / "out" / "src" / odd).read_text() == "odd-data"


@pytest.mark.skipif(not shutil.which("tar"), reason="tar required")
def test_native_tar_roundtrip_preserves_newline_filename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    odd = "line\nbreak.txt"
    (tmp_path / "src" / odd).write_text("odd-data")
    assert main(["create", "odd.tar", "src", "--backend", "tar", "--progress", "never"]) == 0
    assert main(["extract", "odd.tar", "--backend", "tar", "-o", "out", "--progress", "never"]) == 0
    assert (tmp_path / "out" / "src" / odd).read_text() == "odd-data"
