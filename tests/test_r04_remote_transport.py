from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from arc_cli.cli import main, parser
from arc_cli.completion import completion_candidates
from arc_cli.remote import (
    RemoteLocation,
    clear_completion_cache,
    complete_remote,
    completion_cache_rows,
    download_remote,
    parse_remote,
    upload_remote,
)


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_rclone(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    root = tmp_path / "remote-root"
    root.mkdir()
    log = tmp_path / "rclone.log"
    _exe(
        tmp_path / "rclone",
        "import json,os,pathlib,sys\n"
        "root=pathlib.Path(os.environ['FAKE_RCLONE_ROOT'])\n"
        "log=pathlib.Path(os.environ['FAKE_RCLONE_LOG'])\n"
        "with log.open('a',encoding='utf-8') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "cmd=sys.argv[1]\n"
        "if cmd=='listremotes': print('gdrive:'); raise SystemExit(0)\n"
        "target=sys.argv[2]; path=target.split(':',1)[1].lstrip('/'); p=root/path\n"
        "if cmd=='lsjson':\n"
        "  if not p.exists(): raise SystemExit(3)\n"
        "  if p.is_file(): print(json.dumps({'Name':p.name,'Path':p.name,'IsDir':False,'Size':p.stat().st_size})); raise SystemExit(0)\n"
        "  rows=[]\n"
        "  for x in p.iterdir(): rows.append({'Name':x.name,'Path':x.name,'IsDir':x.is_dir(),'Size':0 if x.is_dir() else x.stat().st_size,'ModTime':None})\n"
        "  print(json.dumps(rows)); raise SystemExit(0)\n"
        "if cmd=='cat': sys.stdout.buffer.write(p.read_bytes()); raise SystemExit(0)\n"
        "if cmd=='rcat': p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(sys.stdin.buffer.read()); raise SystemExit(0)\n"
        "if cmd=='moveto':\n"
        "  dst=root/sys.argv[3].split(':',1)[1].lstrip('/'); dst.parent.mkdir(parents=True,exist_ok=True); p.replace(dst); raise SystemExit(0)\n"
        "if cmd=='copy':\n"
        "  import shutil; dst=pathlib.Path(sys.argv[3]); dst.mkdir(parents=True,exist_ok=True)\n"
        "  for child in p.rglob('*'):\n"
        "    rel=child.relative_to(p); out=dst/rel; out.mkdir(parents=True,exist_ok=True) if child.is_dir() else (out.parent.mkdir(parents=True,exist_ok=True) or shutil.copy2(child,out))\n"
        "  raise SystemExit(0)\n"
        "if cmd=='deletefile': p.unlink(missing_ok=True); raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(root))
    monkeypatch.setenv("FAKE_RCLONE_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    return root, log


def test_remote_parser_supports_ssh_alias_and_rclone_native(monkeypatch, tmp_path: Path):
    _fake_rclone(tmp_path, monkeypatch)
    config = {"remotes": {"tablet": {"type": "ssh", "host": "example"}}}
    ssh = parse_remote("tablet:/home/me/archive.7z", config)
    assert ssh == RemoteLocation("ssh", "tablet", "/home/me/archive.7z", "tablet:/home/me/archive.7z")
    assert ssh.render("/home/me/Code") == "tablet:/home/me/Code"
    rc = parse_remote("gdrive:Backups/archive.zip", config)
    assert rc and rc.kind == "rclone" and rc.name == "gdrive"
    uri = parse_remote("rclone://gdrive/Backups/a.zip", config, probe_rclone=False)
    assert uri and uri.render("Backups/b.zip") == "rclone://gdrive/Backups/b.zip"


def test_configured_rclone_parser_does_not_probe(monkeypatch):
    config = {"remotes": {"cloud": {"type": "rclone", "remote": "gdrive"}}}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("configured rclone parsing must not invoke a subprocess")

    monkeypatch.setattr("arc_cli.remote.subprocess.run", forbidden)
    location = parse_remote("cloud:Backups/a.zip", config)
    assert location == RemoteLocation(
        "rclone",
        "gdrive",
        "Backups/a.zip",
        "cloud:Backups/a.zip",
        alias="cloud",
    )


def test_native_rclone_dry_run_discovers_local_config_without_subprocess(tmp_path: Path, monkeypatch):
    config_home = tmp_path / "config"
    rclone_config = config_home / "rclone" / "rclone.conf"
    rclone_config.parent.mkdir(parents=True)
    rclone_config.write_text("[gdrive]\ntype = drive\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run rclone discovery must not invoke a subprocess")

    monkeypatch.setattr("arc_cli.remote.subprocess.run", forbidden)
    location = parse_remote("gdrive:Backups/a.zip", {}, probe_rclone=False)
    assert location == RemoteLocation(
        "rclone", "gdrive", "Backups/a.zip", "gdrive:Backups/a.zip"
    )
    assert main([
        "create",
        str(tmp_path / "out.zip"),
        "gdrive:Inputs/one.txt",
        "--dry-run",
        "--quiet",
    ]) == 0


def test_existing_local_colon_path_wins_over_rclone(tmp_path: Path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "gdrive:literal.zip"
    local.write_bytes(b"x")
    assert parse_remote("gdrive:literal.zip", {}) is None


def test_rclone_completion_cache_and_mutation_invalidation(tmp_path: Path, monkeypatch):
    root, log = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    (root / "Backups" / "old.zip").write_bytes(b"old")
    config = {}

    first = complete_remote("gdrive:Backups/", config)
    assert "gdrive:Backups/old.zip" in first
    second = complete_remote("gdrive:Backups/", config)
    assert second == first
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(row and row[0] == "lsjson" for row in calls) == 1

    src = tmp_path / "new.zip"
    src.write_bytes(b"new")
    upload_remote(src, RemoteLocation("rclone", "gdrive", "Backups/new.zip", "gdrive:Backups/new.zip"), config)
    refreshed = complete_remote("gdrive:Backups/", config)
    assert "gdrive:Backups/new.zip" in refreshed
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(row and row[0] == "lsjson" for row in calls) == 2


def test_double_star_remote_completion_forces_refresh(tmp_path: Path, monkeypatch):
    root, log = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    (root / "Backups" / "a.zip").write_bytes(b"a")
    assert "gdrive:Backups/a.zip" in completion_candidates(["extract", "gdrive:Backups/**"])
    (root / "Backups" / "b.7z").write_bytes(b"b")
    assert "gdrive:Backups/b.7z" in completion_candidates(["extract", "gdrive:Backups/**"])
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(row and row[0] == "lsjson" for row in calls) == 2


def test_cache_cli_reports_and_clears_entries(tmp_path: Path, monkeypatch, capsys):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    complete_remote("gdrive:Backups/", {})
    assert completion_cache_rows()
    assert main(["completion", "cache", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and data[0]["remote"] == "gdrive" and data[0]["state"] in {"fresh", "expired"}
    assert main(["completion", "clear-cache"]) == 0
    capsys.readouterr()
    assert completion_cache_rows() == []


def test_remote_completion_refresh_cli(tmp_path: Path, monkeypatch, capsys):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    assert main(["completion", "refresh", "gdrive:Backups/", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["location"] == "gdrive:Backups/"


def test_remote_transfer_dry_run_performs_no_io(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr("arc_cli.remote.subprocess.run", lambda *a, **k: calls.append((a, k)) or None)
    loc = RemoteLocation("rclone", "gdrive", "Backups/a.zip", "gdrive:Backups/a.zip")
    source = tmp_path / "missing.zip"
    destination = tmp_path / "not-created.zip"
    upload_remote(source, loc, {}, dry_run=True)
    download_remote(loc, destination, {}, dry_run=True)
    assert calls == []
    assert not destination.exists()


def test_remote_create_dispatch_stages_then_uploads(tmp_path: Path, monkeypatch, capsys):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    uploaded: list[tuple[bytes, str, bool]] = []

    def fake_create(args, extra, config):
        Path(args.archive).write_bytes(b"archive-bytes")
        return 0

    def fake_upload(source, location, config, *, dry_run=False):
        uploaded.append((source.read_bytes() if source.exists() else b"", location.raw, dry_run))

    monkeypatch.setattr("arc_cli.cli._create_like", fake_create)
    monkeypatch.setattr("arc_cli.cli.remote_exists", lambda *a, **k: False)
    monkeypatch.setattr("arc_cli.cli.upload_remote", fake_upload)
    monkeypatch.setattr("arc_cli.cli.invalidate_remote_parent", lambda *a, **k: None)
    assert main(["create", "gdrive:Backups/a.zip", "src", "--quiet"]) == 0
    assert uploaded == [(b"archive-bytes", "gdrive:Backups/a.zip", False)]
    capsys.readouterr()


def test_remote_create_dry_run_does_not_probe_or_upload(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    called = {"exists": 0, "upload": []}

    def fake_create(args, extra, config):
        assert args.dry_run is True
        assert not Path(args.archive).exists()
        return 0

    monkeypatch.setattr("arc_cli.cli._create_like", fake_create)
    monkeypatch.setattr("arc_cli.cli.remote_exists", lambda *a, **k: called.__setitem__("exists", called["exists"] + 1))
    monkeypatch.setattr("arc_cli.cli.upload_remote", lambda source, loc, config, dry_run=False: called["upload"].append(dry_run))
    assert main(["create", "gdrive:Backups/a.zip", "src", "--dry-run", "--quiet"]) == 0
    assert called == {"exists": 0, "upload": [True]}


def test_ssh_completion_uses_configured_alias_and_cache(tmp_path: Path, monkeypatch):
    log = tmp_path / "ssh.log"
    _exe(
        tmp_path / "ssh",
        "import json,os,pathlib,sys\n"
        "with pathlib.Path(os.environ['SSH_LOG']).open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "cmd=sys.argv[-1]\n"
        "if 'python3' in cmd: print(json.dumps([{'name':'home','path':'/home','is_dir':True,'size':0,'modified':None}])); raise SystemExit(0)\n"
        "raise SystemExit(0)\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("SSH_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    config = {"remotes": {"tablet": {"type": "ssh", "host": "fake", "port": 8022}}}
    values = complete_remote("tablet:/ho", config)
    assert values == ["tablet:/home/"]
    assert complete_remote("tablet:/ho", config) == values
    assert len(log.read_text().splitlines()) == 1


def test_parser_has_remote_native_options():
    args = parser().parse_args(["list", "gdrive:a.zip", "--show-native=both", "--native-style", "exact", "--execution", "local"])
    assert args.show_native == "both"
    assert args.native_style == "exact"
    assert args.execution == "local"


def test_rclone_upload_is_temp_then_moveto(tmp_path: Path, monkeypatch):
    root, log = _fake_rclone(tmp_path, monkeypatch)
    src = tmp_path / "payload.bin"
    src.write_bytes(b"payload")
    loc = RemoteLocation("rclone", "gdrive", "Backups/final.bin", "gdrive:Backups/final.bin")
    upload_remote(src, loc, {})
    assert (root / "Backups" / "final.bin").read_bytes() == b"payload"
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert any(row and row[0] == "rcat" and ".arc-tmp-" in row[1] for row in calls)
    assert any(row and row[0] == "moveto" and row[-1] == "gdrive:Backups/final.bin" for row in calls)


def test_remote_capability_cache_is_generation_scoped(tmp_path: Path, monkeypatch):
    log = tmp_path / "ssh-cap.log"
    _exe(
        tmp_path / "ssh",
        "import json,os,pathlib,sys\n"
        "with pathlib.Path(os.environ['SSH_LOG']).open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print('arc\\npython3\\ntar')\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("SSH_LOG", str(log))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    from arc_cli.remote import remote_capabilities

    loc = RemoteLocation("ssh", "tablet", "/x", "tablet:/x")
    cfg1 = {"remotes": {"tablet": {"type": "ssh", "host": "one"}}}
    cfg2 = {"remotes": {"tablet": {"type": "ssh", "host": "two"}}}
    assert "arc" in remote_capabilities(loc, cfg1)["tools"]
    assert "arc" in remote_capabilities(loc, cfg1)["tools"]
    assert len(log.read_text().splitlines()) == 1
    assert "arc" in remote_capabilities(loc, cfg2)["tools"]
    assert len(log.read_text().splitlines()) == 2


def test_remote_execution_requires_remote_arc(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.tablet]\ntype="ssh"\nhost="fake"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *a, **k: {"type": "ssh", "tools": ["tar"]})
    assert main(["list", "tablet:/a.zip", "--execution", "remote", "--quiet"]) != 0


def test_remote_execution_delegates_to_remote_arc_when_available(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.tablet]\ntype="ssh"\nhost="fake"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *a, **k: {"type": "ssh", "tools": ["arc"]})
    seen = []

    class Result:
        returncode = 0

    monkeypatch.setattr("arc_cli.cli.subprocess.run", lambda argv, **kwargs: seen.append(argv) or Result())
    assert main(["test", "tablet:/a.zip", "--execution", "remote", "--quiet"]) == 0
    assert seen and "arc test /a.zip" in seen[0][-1]


def test_rclone_remote_file_and_directory_inputs_stage_for_create(tmp_path: Path, monkeypatch):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Inputs").mkdir()
    (root / "Inputs" / "one.txt").write_text("one", encoding="utf-8")
    (root / "Inputs" / "tree").mkdir()
    (root / "Inputs" / "tree" / "two.txt").write_text("two", encoding="utf-8")
    from arc_cli.remote import stage_remote_input

    stage = tmp_path / "stage"
    stage.mkdir()
    file_loc = RemoteLocation("rclone", "gdrive", "Inputs/one.txt", "gdrive:Inputs/one.txt")
    dir_loc = RemoteLocation("rclone", "gdrive", "Inputs/tree/", "gdrive:Inputs/tree/")
    staged_file = stage_remote_input(file_loc, stage, {})
    staged_dir = stage_remote_input(dir_loc, stage, {})
    assert staged_file.read_text() == "one"
    assert (staged_dir / "two.txt").read_text() == "two"


def test_remote_input_dry_run_records_without_network(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls = []
    monkeypatch.setattr("arc_cli.remote.subprocess.run", lambda *a, **k: calls.append(1) or None)
    assert main(["create", str(tmp_path / "out.zip"), "gdrive:Inputs/one.txt", "--dry-run", "--quiet"]) == 0
    assert calls == []


def test_generated_zsh_fzf_descends_single_value_remote_directories():
    from arc_cli.completion import zsh_completion

    text = zsh_completion()
    assert 'query_words[-1]="${selected[1]}**"' in text
    assert '"$mode" == single' in text
    assert '"${selected[1]}" == */' in text


def test_remote_execution_dry_run_does_not_probe_network(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.tablet]\ntype="ssh"\nhost="fake"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("arc_cli.cli.remote_capabilities", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network probe")))
    monkeypatch.setattr("arc_cli.cli.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("execution")))
    assert main(["test", "tablet:/a.zip", "--execution", "remote", "--dry-run", "--quiet"]) == 0


def test_rclone_failed_upload_cleans_temporary_object(tmp_path: Path, monkeypatch):
    src = tmp_path / "payload"
    src.write_bytes(b"x")
    loc = RemoteLocation("rclone", "gdrive", "a.bin", "gdrive:a.bin")
    calls = []

    class Result:
        def __init__(self, rc=0, stderr=b""):
            self.returncode = rc
            self.stderr = stderr

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if len(argv) > 1 and argv[1] == "rcat":
            return Result(1, b"upload failed")
        return Result(0)

    monkeypatch.setattr("arc_cli.remote.shutil.which", lambda name: "/fake/rclone" if name == "rclone" else None)
    monkeypatch.setattr("arc_cli.remote.subprocess.run", fake_run)
    with pytest.raises(Exception):
        upload_remote(src, loc, {})
    assert any(len(row) > 1 and row[1] == "deletefile" for row in calls)


def test_configured_rclone_alias_is_preserved_in_completion(tmp_path: Path, monkeypatch):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    (root / "Backups" / "a.zip").write_bytes(b"x")
    config = {"remotes": {"drive": {"type": "rclone", "remote": "gdrive"}}}
    loc = parse_remote("drive:Backups/a.zip", config)
    assert loc is not None
    assert loc.name == "gdrive"
    assert loc.alias == "drive"
    assert loc.render() == "drive:Backups/a.zip"
    assert complete_remote("drive:Backups/a", config) == ["drive:Backups/a.zip"]


def test_default_rclone_config_change_invalidates_provider_generation(tmp_path: Path, monkeypatch):
    from arc_cli.remote import _provider_generation

    xdg = tmp_path / "xdg"
    cfg = xdg / "rclone" / "rclone.conf"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[gdrive]\ntype = local\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("RCLONE_CONFIG", raising=False)
    loc = RemoteLocation("rclone", "gdrive", "Backups", "gdrive:Backups")
    first = _provider_generation(loc, {})
    cfg.write_text("[gdrive]\ntype = local\nremote = changed\n", encoding="utf-8")
    second = _provider_generation(loc, {})
    assert first != second


def test_completion_cache_state_uses_configured_ttl_and_generation(tmp_path: Path, monkeypatch):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    config = {"remotes": {"gdrive": {"type": "rclone", "remote": "gdrive", "completion_ttl_seconds": 1}}}
    now = {"value": 1000.0}
    monkeypatch.setattr("arc_cli.remote.time.time", lambda: now["value"])
    complete_remote("gdrive:Backups/", config)
    rows = completion_cache_rows(config)
    assert rows and rows[0]["state"] == "fresh"
    assert rows[0]["ttl_seconds"] == 1
    now["value"] = 1002.0
    assert completion_cache_rows(config)[0]["state"] == "expired"
    changed = {"remotes": {"gdrive": {"type": "rclone", "remote": "gdrive", "completion_ttl_seconds": 1, "tag": "new"}}}
    assert completion_cache_rows(changed)[0]["state"] == "stale-config"


def test_remote_capabilities_require_rclone_binary(monkeypatch):
    from arc_cli.errors import BackendUnavailable
    from arc_cli.remote import remote_capabilities

    monkeypatch.setattr("arc_cli.remote.shutil.which", lambda _name: None)
    loc = RemoteLocation("rclone", "gdrive", "", "gdrive:")
    with pytest.raises(BackendUnavailable):
        remote_capabilities(loc, {}, refresh=True)


def test_ssh_capability_probe_reports_machine_safe_listing_features(tmp_path: Path, monkeypatch):
    _exe(
        tmp_path / "ssh",
        "print('tool:python3\\ntool:find\\ntool:stat\\nfeature:find-print0\\nfeature:python-scandir\\nfeature:stat')\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH','')}")
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    from arc_cli.remote import remote_capabilities

    loc = RemoteLocation("ssh", "tablet", "/", "tablet:/")
    caps = remote_capabilities(loc, {}, refresh=True)
    assert {"python3", "find", "stat"}.issubset(set(caps["tools"]))
    assert {"find-print0", "python-scandir", "stat"}.issubset(set(caps["features"]))


@pytest.mark.skipif(not __import__("shutil").which("tar"), reason="tar required")
def test_rclone_tar_create_streams_directly_and_finalizes_atomically(tmp_path: Path, monkeypatch):
    import tarfile

    root, log = _fake_rclone(tmp_path, monkeypatch)
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    source = tmp_path / "src"
    source.mkdir()
    (source / "hello.txt").write_text("hello", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["create", "gdrive:Backups/a.tar", "src", "--quiet"]) == 0
    archive = root / "Backups" / "a.tar"
    assert archive.is_file()
    with tarfile.open(archive) as tf:
        assert "src/hello.txt" in tf.getnames()
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert any(row and row[0] == "rcat" for row in calls)
    assert any(row and row[0] == "moveto" for row in calls)


@pytest.mark.skipif(not __import__("shutil").which("tar"), reason="tar required")
def test_remote_add_extension_changes_actual_remote_target(tmp_path: Path, monkeypatch):
    root, _ = _fake_rclone(tmp_path, monkeypatch)
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["create", "gdrive:Backups/archive", "one.txt", "--format", "tar", "--add-extension", "--quiet"]) == 0
    assert (root / "Backups" / "archive.tar").is_file()
    assert not (root / "Backups" / "archive").exists()


@pytest.mark.skipif(not __import__("shutil").which("gzip"), reason="gzip required")
def test_remote_gzip_extract_streams_without_archive_staging(tmp_path: Path, monkeypatch):
    import gzip

    root, log = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    (root / "Backups" / "data.txt.gz").write_bytes(gzip.compress(b"payload"))
    cfg = tmp_path / "config" / "arc" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[remotes.gdrive]\ntype="rclone"\nremote="gdrive"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    out = tmp_path / "out"
    assert main(["extract", "gdrive:Backups/data.txt.gz", "-o", str(out), "--quiet"]) == 0
    assert (out / "data.txt").read_bytes() == b"payload"
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert any(row and row[0] == "cat" for row in calls)


def test_ssh_atomic_upload_quotes_hostile_remote_path(tmp_path: Path, monkeypatch):
    captured = []
    monkeypatch.setattr("arc_cli.remote._copy_stream", lambda argv, **kwargs: captured.append(argv) or 0)
    monkeypatch.setattr("arc_cli.remote.invalidate_remote_parent", lambda *a, **k: None)
    weird = "/tmp/a b'c;$(touch nope)\nfile.zip"
    loc = RemoteLocation("ssh", "tablet", weird, f"tablet:{weird}")
    source = tmp_path / "archive"
    source.write_bytes(b"x")
    upload_remote(source, loc, {}, dry_run=True)
    assert captured
    import shlex

    parsed = shlex.split(captured[0][-1])
    assert parsed[-2] == weird
    assert parsed[-1] == str(Path(weird).parent).replace("\\", "/")


def test_remote_completion_preserves_newline_filename_via_nul_transport(tmp_path: Path, monkeypatch):
    from arc_cli.completion import encode_candidates_nul

    root, _ = _fake_rclone(tmp_path, monkeypatch)
    (root / "Backups").mkdir()
    name = "line\nbreak.zip"
    (root / "Backups" / name).write_bytes(b"x")
    values = complete_remote("gdrive:Backups/line", {})
    assert values == [f"gdrive:Backups/{name}"]
    payload = encode_candidates_nul(values)
    assert payload.endswith(b"\0")
    assert b"line\nbreak.zip\0" in payload


def test_stage_remote_read_cleans_partial_file_on_transport_failure(tmp_path: Path, monkeypatch):
    from arc_cli.errors import BackendUnavailable
    from arc_cli.remote import stage_remote_for_read

    staged = tmp_path / "partial.zip"

    def fake_mkstemp(*_args, **_kwargs):
        fd = os.open(staged, os.O_CREAT | os.O_RDWR, 0o600)
        return fd, str(staged)

    def fail_download(_loc, destination, _config, **_kwargs):
        destination.write_bytes(b"partial")
        raise BackendUnavailable("connection dropped")

    monkeypatch.setattr("arc_cli.remote.tempfile.mkstemp", fake_mkstemp)
    monkeypatch.setattr("arc_cli.remote.download_remote", fail_download)
    loc = RemoteLocation("ssh", "tablet", "/a.zip", "tablet:/a.zip")
    with pytest.raises(BackendUnavailable):
        stage_remote_for_read(loc, {})
    assert not staged.exists()


def test_rclone_upload_interrupt_path_deletes_temporary_object(tmp_path: Path, monkeypatch):
    src = tmp_path / "payload"
    src.write_bytes(b"x")
    loc = RemoteLocation("rclone", "gdrive", "a.bin", "gdrive:a.bin")
    cleanup_calls = []
    monkeypatch.setattr("arc_cli.remote.shutil.which", lambda name: "/fake/rclone" if name == "rclone" else None)
    monkeypatch.setattr("arc_cli.remote._copy_stream", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))

    class Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr("arc_cli.remote.subprocess.run", lambda argv, **kwargs: cleanup_calls.append(list(argv)) or Result())
    with pytest.raises(KeyboardInterrupt):
        upload_remote(src, loc, {})
    assert any(len(row) > 1 and row[1] == "deletefile" for row in cleanup_calls)


def test_streaming_create_failure_cleans_rclone_temp_object(tmp_path: Path, monkeypatch):
    root, log = _fake_rclone(tmp_path, monkeypatch)
    from arc_cli.errors import BackendUnavailable
    from arc_cli.remote import stream_pipeline_to_remote

    loc = RemoteLocation("rclone", "gdrive", "Backups/fail.tar", "gdrive:Backups/fail.tar")
    with pytest.raises(BackendUnavailable):
        stream_pipeline_to_remote(["sh", "-c", "exit 7"], loc, {})
    assert not (root / "Backups" / "fail.tar").exists()
    assert not list((root / "Backups").glob("fail.tar.arc-tmp-*")) if (root / "Backups").exists() else True
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert any(row and row[0] == "deletefile" for row in calls)


def test_ssh_atomic_sink_contains_interrupt_cleanup_trap(tmp_path: Path, monkeypatch):
    captured = []
    monkeypatch.setattr("arc_cli.remote._copy_stream", lambda argv, **kwargs: captured.append(argv) or 0)
    monkeypatch.setattr("arc_cli.remote.invalidate_remote_parent", lambda *a, **k: None)
    src = tmp_path / "a"
    src.write_bytes(b"x")
    loc = RemoteLocation("ssh", "tablet", "/remote/a.zip", "tablet:/remote/a.zip")
    upload_remote(src, loc, {}, dry_run=True)
    command = captured[0][-1]
    assert "EXIT HUP INT TERM" in command
    assert 'mv -- "$tmp" "$path"' in command
