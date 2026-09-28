from __future__ import annotations

import configparser
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import BackendUnavailable, UsageError
from .execution import emit_command, record_decision, record_stage
from .progress import ProgressReporter, console


@dataclass(frozen=True, slots=True)
class RemoteLocation:
    kind: str  # ssh | rclone
    name: str  # transport/backend name (actual rclone remote for rclone)
    path: str
    raw: str
    alias: str | None = None  # user-facing configured alias, when different

    @property
    def basename(self) -> str:
        return PurePosixPath(self.path.rstrip("/")).name

    @property
    def parent(self) -> str:
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent

    def render(self, path: str | None = None) -> str:
        value = self.path if path is None else path
        display = self.alias or self.name
        if self.kind == "ssh":
            if self.raw.startswith("ssh://"):
                return f"ssh://{display}/{value.lstrip('/')}"
            return f"{display}:{value}"
        if self.raw.startswith("rclone://"):
            return f"rclone://{display}/{value.lstrip('/')}"
        return f"{display}:{value.lstrip('/')}"


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: str | None = None


DEFAULT_REMOTE_TTL = 60
REMOTE_CAPABILITY_SCHEMA = "arc.remote-capability/v1"
REMOTE_CAPABILITY_SCHEMA_VERSION = 1
REMOTE_CAPABILITY_CACHE_VERSION = 3


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _cache_root() -> Path:
    explicit = os.environ.get("ARC_CACHE_HOME")
    if explicit:
        return Path(explicit).expanduser() / "completion"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "arc" / "completion"
    return Path.home() / ".cache" / "arc" / "completion"


def _configured_remote(config: dict, name: str) -> dict | None:
    remotes = config.get("remotes", {})
    value = remotes.get(name) if isinstance(remotes, dict) else None
    return value if isinstance(value, dict) else None


def _rclone_config_path() -> Path:
    explicit = os.environ.get("RCLONE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "rclone" / "rclone.conf"


def _rclone_names(config: dict, *, probe: bool = True) -> set[str]:
    names: set[str] = set()
    remotes = config.get("remotes", {})
    if isinstance(remotes, dict):
        for name, value in remotes.items():
            if isinstance(value, dict) and value.get("type") == "rclone":
                names.add(str(value.get("remote") or name).rstrip(":"))
    # Read the normal rclone config directly first. This is local-only and
    # keeps native ``remote:path`` classification available during --dry-run
    # without spawning rclone merely for discovery.
    parser = configparser.RawConfigParser()
    try:
        with _rclone_config_path().open("r", encoding="utf-8") as fh:
            parser.read_file(fh)
        names.update(section.rstrip(":") for section in parser.sections() if section)
    except (OSError, configparser.Error, UnicodeError):
        pass
    if probe:
        exe = shutil.which("rclone")
        if exe:
            try:
                proc = subprocess.run([exe, "listremotes"], capture_output=True, text=True, timeout=3, check=False)
            except (OSError, subprocess.TimeoutExpired):
                proc = None
            if proc and proc.returncode == 0:
                names.update(line.strip().rstrip(":") for line in proc.stdout.splitlines() if line.strip())
    return names


def configured_remote_names(config: dict, *, include_rclone_probe: bool = True) -> list[str]:
    names: set[str] = set()
    remotes = config.get("remotes", {})
    if isinstance(remotes, dict):
        names.update(str(name) for name, value in remotes.items() if isinstance(value, dict))
    names.update(_rclone_names(config, probe=include_rclone_probe))
    return sorted(names)


def parse_remote(value: str, config: dict, *, probe_rclone: bool = True) -> RemoteLocation | None:
    if not value.startswith(("ssh://", "rclone://")):
        try:
            if Path(value).expanduser().exists():
                return None
        except OSError:
            pass
    if value.startswith("ssh://"):
        rest = value[6:]
        name, sep, path = rest.partition("/")
        if not name:
            raise UsageError("SSH location requires a remote name")
        return RemoteLocation("ssh", name, "/" + path if sep else "/", value)
    if value.startswith("rclone://"):
        rest = value[9:]
        name, sep, path = rest.partition("/")
        if not name:
            raise UsageError("rclone location requires a remote name")
        return RemoteLocation("rclone", name.rstrip(":"), path if sep else "", value)
    if ":" not in value or value.startswith(("./", "../", "/")):
        return None
    name, path = value.split(":", 1)
    if not name or "/" in name or "\\" in name:
        return None
    cfg = _configured_remote(config, name)
    if cfg and cfg.get("type") == "ssh":
        return RemoteLocation("ssh", name, path or "/", value)
    configured_rclone = cfg and cfg.get("type") == "rclone"
    if configured_rclone:
        # Configured aliases are authoritative and must not invoke rclone just
        # to classify a path. This is especially important for --dry-run,
        # whose zero-I/O contract forbids discovery subprocesses.
        remote_name = str(cfg.get("remote") or name).rstrip(":")
        return RemoteLocation("rclone", remote_name, path, value, alias=name if remote_name != name else None)
    rclone_names = _rclone_names(config, probe=probe_rclone)
    if name in rclone_names:
        return RemoteLocation("rclone", name, path, value)
    return None


def _ssh_args(location: RemoteLocation, config: dict) -> list[str]:
    cfg = _configured_remote(config, location.alias or location.name) or {}
    if cfg and cfg.get("type") not in {None, "ssh"}:
        raise UsageError(f"remote {location.name!r} is not an SSH remote")
    exe = shutil.which("ssh") or "ssh"
    argv = [exe]
    port = cfg.get("port")
    if port:
        argv += ["-p", str(port)]
    identity = cfg.get("identity_file")
    if identity:
        argv += ["-i", os.fspath(Path(str(identity)).expanduser())]
    jump = cfg.get("proxy_jump")
    if jump:
        argv += ["-J", str(jump)]
    extra = cfg.get("ssh_args", [])
    if isinstance(extra, list) and all(isinstance(x, str) for x in extra):
        argv += extra
    host = str(cfg.get("host") or location.name)
    user = cfg.get("user")
    destination = f"{user}@{host}" if user else host
    argv.append(destination)
    return argv


def ssh_command_prefix(location: RemoteLocation, config: dict) -> list[str]:
    """Return the local ssh argv prefix for a configured SSH location."""
    if location.kind != "ssh":
        raise UsageError("SSH command prefix requires an SSH remote")
    return _ssh_args(location, config)


def _rclone_target(location: RemoteLocation, path: str | None = None) -> str:
    value = location.path if path is None else path
    return f"{location.name}:{value.lstrip('/')}"


def _rclone_config_identity() -> dict[str, Any]:
    path = _rclone_config_path()
    try:
        stat = path.stat()
        return {"path": os.fspath(path), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    except OSError:
        return {"path": os.fspath(path), "mtime_ns": None, "size": None}


def same_remote(left: RemoteLocation, right: RemoteLocation, config: dict) -> bool:
    """Return whether two remote locations address the same transport endpoint."""
    if left.kind != right.kind:
        return False
    if left.kind == "rclone":
        return left.name == right.name
    return _ssh_args(left, config) == _ssh_args(right, config)


def _provider_generation(location: RemoteLocation, config: dict) -> str:
    config_name = location.alias or location.name
    if location.kind == "ssh":
        cfg = _configured_remote(config, config_name) or {"type": "ssh", "host": location.name}
        payload = {"config_name": config_name, "config": cfg}
    else:
        cfg = _configured_remote(config, config_name) or {"type": "rclone", "remote": location.name}
        payload = {
            "config_name": config_name,
            "config": cfg,
            "rclone_config": _rclone_config_identity(),
        }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]

def _cache_file(location: RemoteLocation, directory: str, config: dict) -> Path:
    generation = _provider_generation(location, config)
    cache_name = location.alias or location.name
    token = hashlib.sha256(f"{cache_name}\0{location.name}\0{directory}\0{generation}".encode()).hexdigest()
    return _cache_root() / location.kind / cache_name / f"{token}.json"


def _ttl(config: dict, location: RemoteLocation) -> int:
    cfg = _configured_remote(config, location.alias or location.name) or {}
    raw = cfg.get("completion_ttl_seconds", config.get("completion", {}).get("remote_ttl_seconds", DEFAULT_REMOTE_TTL))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_REMOTE_TTL


def _load_cached(location: RemoteLocation, directory: str, config: dict, *, allow_stale: bool = False) -> list[RemoteEntry] | None:
    path = _cache_file(location, directory, config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    age = time.time() - float(data.get("fetched_at", 0))
    if not allow_stale and age > _ttl(config, location):
        return None
    return [RemoteEntry(**entry) for entry in data.get("entries", [])]


def _store_cached(location: RemoteLocation, directory: str, config: dict, entries: list[RemoteEntry]) -> None:
    path = _cache_file(location, directory, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "kind": location.kind,
        "remote": location.name,
        "config_name": location.alias or location.name,
        "location": directory,
        "provider_generation": _provider_generation(location, config),
        "ttl_seconds": _ttl(config, location),
        "fetched_at": time.time(),
        "entries": [asdict(entry) for entry in entries],
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _ssh_list(location: RemoteLocation, directory: str, config: dict) -> list[RemoteEntry]:
    base = _ssh_args(location, config)
    script = (
        "import json,os,sys; p=sys.argv[1]; out=[]; "
        "\nfor e in os.scandir(p):\n"
        "  try: s=e.stat(follow_symlinks=False); size=s.st_size\n"
        "  except OSError: size=0\n"
        "  out.append({'name':e.name,'path':os.path.join(p,e.name),'is_dir':e.is_dir(follow_symlinks=False),'size':size,'modified':None})\n"
        "sys.stdout.write(json.dumps(out,ensure_ascii=True))"
    )
    remote_cmd = shlex.join(["python3", "-c", script, directory or "."])
    argv = [*base, remote_cmd]
    record_stage("ssh-list", argv, description=f"list SSH directory {location.name}:{directory}")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError as exc:
        raise BackendUnavailable("ssh is not installed") from exc
    if proc.returncode == 0:
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError:
            rows = None
        if isinstance(rows, list):
            return [RemoteEntry(str(x["name"]), str(x["path"]), bool(x["is_dir"]), int(x.get("size") or 0), x.get("modified")) for x in rows]

    # Portable fallback for minimal SSH hosts: NUL-delimited one-level find.
    # Directories are suffixed with '/' by a second test command per entry only
    # when Python is unavailable; filenames themselves remain NUL framed.
    find_cmd = shlex.join(["find", directory or ".", "-mindepth", "1", "-maxdepth", "1", "-print0"])
    fallback_argv = [*base, find_cmd]
    record_stage("ssh-list-fallback", fallback_argv, description=f"fallback SSH directory listing {location.name}:{directory}")
    fallback = subprocess.run(fallback_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    if fallback.returncode != 0:
        detail = fallback.stderr.decode(errors="replace").strip()
        raise BackendUnavailable(f"SSH listing failed for {location.name}:{directory}: {detail or fallback.returncode}")
    entries: list[RemoteEntry] = []
    for raw in fallback.stdout.split(b"\0"):
        if not raw:
            continue
        path = os.fsdecode(raw)
        name = PurePosixPath(path).name
        test_cmd = f"test -d {shlex.quote(path)}"
        is_dir = subprocess.run([*base, test_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False).returncode == 0
        entries.append(RemoteEntry(name, path, is_dir))
    return entries


def _rclone_list(location: RemoteLocation, directory: str, config: dict) -> list[RemoteEntry]:
    exe = shutil.which("rclone")
    if not exe:
        raise BackendUnavailable("rclone is not installed")
    target = _rclone_target(location, directory)
    argv = [exe, "lsjson", target, "--max-depth", "1"]
    record_stage("rclone-list", argv, description=f"list rclone directory {target}")
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    if proc.returncode != 0:
        raise BackendUnavailable(f"rclone listing failed for {target}: {proc.stderr.strip() or proc.returncode}")
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BackendUnavailable("rclone returned invalid JSON listing output") from exc
    out: list[RemoteEntry] = []
    for row in rows:
        name = str(row.get("Name") or row.get("Path") or "")
        if not name:
            continue
        child = str(PurePosixPath(directory) / name) if directory else name
        out.append(RemoteEntry(name, child, bool(row.get("IsDir")), int(row.get("Size") or 0), row.get("ModTime")))
    return out


def list_remote(location: RemoteLocation, directory: str, config: dict, *, refresh: bool = False) -> list[RemoteEntry]:
    if not refresh:
        cached = _load_cached(location, directory, config)
        if cached is not None:
            return cached
    entries = _ssh_list(location, directory, config) if location.kind == "ssh" else _rclone_list(location, directory, config)
    _store_cached(location, directory, config, entries)
    return entries


def complete_remote(raw_prefix: str, config: dict, *, refresh: bool = False, dirs_only: bool = False, archives_only: bool = False) -> list[str]:
    loc = parse_remote(raw_prefix, config, probe_rclone=True)
    if loc is None:
        # Complete just the remote name before ':' or scheme path.
        if ":" not in raw_prefix and not raw_prefix.startswith(("ssh://", "rclone://")):
            return [name + ":" for name in configured_remote_names(config) if (name + ":").startswith(raw_prefix)]
        return []
    path = loc.path
    if path.endswith("/"):
        directory, leaf = path.rstrip("/") or "/", ""
    else:
        p = PurePosixPath(path)
        directory = str(p.parent)
        if directory == ".":
            directory = ""
        leaf = p.name
    entries = list_remote(loc, directory, config, refresh=refresh)
    archive_suffixes = (".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".zst")
    out: list[str] = []
    for entry in entries:
        if not entry.name.startswith(leaf):
            continue
        if dirs_only and not entry.is_dir:
            continue
        if archives_only and not entry.is_dir and not entry.name.lower().endswith(archive_suffixes):
            continue
        rendered = loc.render(entry.path)
        if entry.is_dir:
            rendered += "/"
        out.append(rendered)
    return sorted(out)


def invalidate_remote_directory(location: RemoteLocation, directory: str, config: dict) -> None:
    base = _cache_root() / location.kind / (location.alias or location.name)
    if not base.exists():
        return
    normalized = str(PurePosixPath(directory))
    if normalized == ".":
        normalized = ""
    for path in base.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cached = str(data.get("location") or "")
        if cached == normalized or cached.rstrip("/") == normalized.rstrip("/"):
            path.unlink(missing_ok=True)


def invalidate_remote_parent(location: RemoteLocation, config: dict) -> None:
    invalidate_remote_directory(location, location.parent, config)


def clear_completion_cache(location: RemoteLocation | None = None) -> int:
    root = _cache_root()
    if not root.exists():
        return 0
    removed = 0
    paths = root.rglob("*.json") if location is None else (root / location.kind / (location.alias or location.name)).glob("*.json")
    for path in list(paths):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def completion_cache_rows(config: dict | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = _cache_root()
    if not root.exists():
        return rows
    now = time.time()
    config = config or {}
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fetched = float(data.get("fetched_at", 0))
        age = max(0, int(now - fetched))
        ttl = int(data.get("ttl_seconds", DEFAULT_REMOTE_TTL) or 0)
        config_name = str(data.get("config_name") or data.get("remote") or "")
        location = RemoteLocation(
            str(data.get("kind") or "rclone"),
            str(data.get("remote") or config_name),
            str(data.get("location") or ""),
            f"{config_name}:{data.get('location') or ''}",
            alias=config_name or None,
        )
        current_generation = _provider_generation(location, config)
        if data.get("provider_generation") != current_generation:
            state = "stale-config"
        elif age > ttl:
            state = "expired"
        else:
            state = "fresh"
        rows.append({
            "kind": data.get("kind"),
            "remote": config_name or data.get("remote"),
            "provider_remote": data.get("remote"),
            "location": data.get("location"),
            "age_seconds": age,
            "ttl_seconds": ttl,
            "state": state,
            "entries": len(data.get("entries", [])),
            "provider_generation": data.get("provider_generation"),
        })
    return sorted(rows, key=lambda x: (str(x.get("kind")), str(x.get("remote")), str(x.get("location"))))

def _copy_stream(
    argv: list[str],
    *,
    source: Path | None = None,
    destination: Path | None = None,
    dry_run: bool = False,
    kind: str,
    description: str,
    progress: bool = False,
) -> int:
    record_stage(
        kind,
        argv,
        description=description,
        stdin_from=f"cat {shlex.quote(os.fspath(source))}" if source else None,
        stdout_to=destination,
    )
    if dry_run:
        return 0
    total = 0
    if source is not None:
        try:
            total = source.stat().st_size
        except OSError:
            total = 0
    stderr_file = tempfile.TemporaryFile()
    try:
        if source is not None:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file)
            assert proc.stdin is not None
            with source.open("rb") as src, ProgressReporter(description, total, 0, progress) as reporter:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
                    reporter.advance_bytes(len(chunk), current=source.name)
                proc.stdin.close()
                rc = proc.wait()
                if rc == 0:
                    reporter.complete()
        elif destination is not None:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr_file)
            assert proc.stdout is not None
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as dst, ProgressReporter(description, 0, 0, progress) as reporter:
                copied = 0
                while True:
                    chunk = proc.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied += len(chunk)
                    reporter.advance_bytes(len(chunk), current=f"{copied} bytes")
                rc = proc.wait()
                if rc == 0:
                    reporter.complete()
        else:
            proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=stderr_file, check=False)
            rc = proc.returncode
    except FileNotFoundError as exc:
        raise BackendUnavailable(f"{Path(argv[0]).name} is not installed") from exc
    except BrokenPipeError:
        rc = proc.wait() if "proc" in locals() else 1
    stderr_file.seek(0)
    detail = stderr_file.read().decode(errors="replace").strip()
    stderr_file.close()
    if rc != 0:
        raise BackendUnavailable(f"transport failed ({rc}): {detail}")
    return 0

def remote_cat_argv(location: RemoteLocation, config: dict, *, dry_run: bool = False) -> list[str]:
    if location.kind == "rclone":
        exe = shutil.which("rclone")
        if not exe and not dry_run:
            raise BackendUnavailable("rclone is not installed")
        return [exe or "rclone", "cat", _rclone_target(location)]
    base = _ssh_args(location, config)
    remote_cmd = shlex.join(["cat", "--", location.path])
    return [*base, remote_cmd]


def stream_remote_to_local(
    location: RemoteLocation,
    config: dict,
    consumer: list[str],
    *,
    destination: Path | None = None,
    dry_run: bool = False,
    progress: bool = False,
    redact: list[str] | None = None,
    show_command: bool = False,
    forward_stdout: bool = False,
) -> None:
    source = remote_cat_argv(location, config, dry_run=dry_run)
    stage = record_stage(
        "remote-stream-read",
        source,
        description=f"stream {location.raw} into local backend",
        pipeline=[consumer],
        stdout_to=destination,
        redact=redact or [],
    )
    emit_command(stage, force=show_command or dry_run)
    if dry_run:
        return
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
    stderr_files = [tempfile.TemporaryFile(), tempfile.TemporaryFile()]
    source_proc = consumer_proc = None
    try:
        source_proc = subprocess.Popen(source, stdout=subprocess.PIPE, stderr=stderr_files[0])
        assert source_proc.stdout is not None
        out_fh = destination.open("wb") if destination is not None else (None if forward_stdout else subprocess.DEVNULL)
        try:
            consumer_proc = subprocess.Popen(consumer, stdin=source_proc.stdout, stdout=out_fh, stderr=stderr_files[1])
            source_proc.stdout.close()
            with ProgressReporter("Streaming archive", 0, 0, progress) as reporter:
                consumer_rc = consumer_proc.wait()
                source_rc = source_proc.wait()
                if not source_rc and not consumer_rc:
                    reporter.complete()
        finally:
            if destination is not None and hasattr(out_fh, "close"):
                out_fh.close()
        if source_rc or consumer_rc:
            details: list[str] = []
            for file in stderr_files:
                file.seek(0)
                text = file.read().decode(errors="replace").strip()
                if text:
                    details.append(text)
            if destination is not None:
                destination.unlink(missing_ok=True)
            raise BackendUnavailable(
                "remote streaming read failed: " + (" | ".join(details[-2:]) or f"{source_rc}/{consumer_rc}")
            )
    except BaseException:
        for proc in (consumer_proc, source_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
        if destination is not None and destination.exists():
            destination.unlink(missing_ok=True)
        raise
    finally:
        for file in stderr_files:
            file.close()


def download_remote(location: RemoteLocation, destination: Path, config: dict, *, dry_run: bool = False, progress: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True) if not dry_run else None
    if location.kind == "rclone":
        exe = shutil.which("rclone") or "rclone"
        _copy_stream([exe, "cat", _rclone_target(location)], destination=destination, dry_run=dry_run, kind="rclone-download", description=f"download {location.raw}", progress=progress)
        return
    base = _ssh_args(location, config)
    remote_cmd = shlex.join(["cat", "--", location.path])
    _copy_stream([*base, remote_cmd], destination=destination, dry_run=dry_run, kind="ssh-download", description=f"download {location.raw}", progress=progress)


def upload_remote(source: Path, location: RemoteLocation, config: dict, *, dry_run: bool = False, progress: bool = False) -> dict[str, Any]:
    publication = remote_publication_guarantee(location, config, allow_probe=False)
    record_decision(
        "remote_publication",
        publication,
        reason="transport-owned publication guarantee; atomicity is only claimed when provider evidence supports it",
    )
    if location.kind == "rclone":
        exe = shutil.which("rclone") or "rclone"
        final_target = _rclone_target(location)
        temp_target = final_target + f".arc-tmp-{os.getpid()}"
        finalized = False
        try:
            _copy_stream([exe, "rcat", temp_target], source=source, dry_run=dry_run, kind="rclone-upload", description=f"stage upload {location.raw}", progress=progress)
            move_argv = [exe, "moveto", temp_target, final_target]
            record_stage("rclone-finalize", move_argv, description=f"finalize {location.raw} via provider move")
            if not dry_run:
                proc = subprocess.run(move_argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
                if proc.returncode != 0:
                    detail = proc.stderr.decode(errors="replace").strip() if isinstance(proc.stderr, bytes) else str(proc.stderr or "").strip()
                    raise BackendUnavailable(f"rclone finalization failed ({proc.returncode}): {detail}")
                finalized = True
        finally:
            if not dry_run and not finalized:
                try:
                    subprocess.run([exe, "deletefile", temp_target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    else:
        base = _ssh_args(location, config)
        parent = location.parent or "."
        script = """set -e; path=$1; parent=$2; mkdir -p -- "$parent"; tmp="${path}.arc-tmp-$$"; trap 'rm -f -- "$tmp"' EXIT HUP INT TERM; cat > "$tmp"; mv -- "$tmp" "$path"; trap - EXIT HUP INT TERM"""
        remote_cmd = shlex.join(["sh", "-c", script, "arc-upload", location.path, parent])
        _copy_stream([*base, remote_cmd], source=source, dry_run=dry_run, kind="ssh-upload", description=f"upload {location.raw}", progress=progress)
    if not dry_run:
        invalidate_remote_parent(location, config)
    return publication


def _stream_sink(location: RemoteLocation, config: dict, *, dry_run: bool = False) -> tuple[list[str], list[str] | None, list[str] | None]:
    if location.kind == "rclone":
        exe = shutil.which("rclone")
        if not exe and not dry_run:
            raise BackendUnavailable("rclone is not installed")
        exe = exe or "rclone"
        final_target = _rclone_target(location)
        temp_target = final_target + f".arc-tmp-{os.getpid()}"
        return (
            [exe, "rcat", temp_target],
            [exe, "moveto", temp_target, final_target],
            [exe, "deletefile", temp_target],
        )
    base = _ssh_args(location, config)
    parent = location.parent or "."
    script = """set -e; path=$1; parent=$2; mkdir -p -- "$parent"; tmp="${path}.arc-tmp-$$"; trap 'rm -f -- "$tmp"' EXIT HUP INT TERM; cat > "$tmp"; mv -- "$tmp" "$path"; trap - EXIT HUP INT TERM"""
    remote_cmd = shlex.join(["sh", "-c", script, "arc-upload", location.path, parent])
    return [*base, remote_cmd], None, None


def stream_pipeline_to_remote(
    producer: list[str],
    location: RemoteLocation,
    config: dict,
    *,
    pipeline: list[list[str]] | None = None,
    dry_run: bool = False,
    progress: bool = False,
    redact: list[str] | None = None,
    implementation_paths: list[str | os.PathLike[str]] | None = None,
    show_command: bool = False,
) -> dict[str, Any]:
    """Stream producer stdout through optional transforms into a remote destination."""
    publication = remote_publication_guarantee(location, config, allow_probe=False)
    record_decision(
        "remote_publication",
        publication,
        reason="transport-owned publication guarantee; rclone moveto is provider-dependent rather than assumed atomic",
    )
    sink, finalize, cleanup = _stream_sink(location, config, dry_run=dry_run)
    commands = [producer, *(pipeline or []), sink]
    stage = record_stage(
        "remote-stream-create",
        producer,
        description=f"stream archive to {location.raw}",
        pipeline=[*(pipeline or []), sink],
        redact=redact or [],
        implementation_paths=implementation_paths or [],
    )
    emit_command(stage, force=show_command or dry_run)
    if finalize:
        record_stage("rclone-finalize", finalize, description=f"finalize {location.raw} via provider move")
    if dry_run:
        return publication

    processes: list[subprocess.Popen] = []
    stderr_files = [tempfile.TemporaryFile() for _ in commands]
    previous = None
    try:
        for index, command in enumerate(commands):
            proc = subprocess.Popen(
                command,
                stdin=previous.stdout if previous is not None else None,
                stdout=subprocess.PIPE if index < len(commands) - 1 else subprocess.DEVNULL,
                stderr=stderr_files[index],
            )
            if previous is not None and previous.stdout is not None:
                previous.stdout.close()
            processes.append(proc)
            previous = proc
        with ProgressReporter("Streaming archive", 0, 0, progress) as reporter:
            returncodes = [proc.wait() for proc in reversed(processes)]
            if not any(returncodes):
                reporter.complete()
        if any(returncodes):
            details: list[str] = []
            for file in stderr_files:
                file.seek(0)
                text = file.read().decode(errors="replace").strip()
                if text:
                    details.append(text)
            raise BackendUnavailable(
                "streaming archive pipeline failed: " + (" | ".join(details[-3:]) or str(returncodes))
            )
        if finalize:
            proc = subprocess.run(finalize, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
            if proc.returncode != 0:
                detail = proc.stderr.decode(errors="replace").strip() if isinstance(proc.stderr, bytes) else str(proc.stderr or "").strip()
                raise BackendUnavailable(f"rclone finalization failed ({proc.returncode}): {detail}")
        invalidate_remote_parent(location, config)
        return publication
    except (KeyboardInterrupt, BaseException):
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
        if cleanup:
            try:
                subprocess.run(cleanup, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
    finally:
        for file in stderr_files:
            file.close()


def delete_remote(location: RemoteLocation, config: dict, *, dry_run: bool = False) -> None:
    """Delete one remote file after higher-level transactional checks succeed."""
    if location.kind == "rclone":
        exe = shutil.which("rclone") or "rclone"
        argv = [exe, "deletefile", _rclone_target(location)]
        record_stage("rclone-delete", argv, description=f"delete {location.raw}")
    else:
        base = _ssh_args(location, config)
        script = 'set -e; path=$1; test -f "$path" || test -L "$path"; rm -f -- "$path"'
        remote_cmd = shlex.join(["sh", "-c", script, "arc-delete", location.path])
        argv = [*base, remote_cmd]
        record_stage("ssh-delete", argv, description=f"delete {location.raw}")
    if dry_run:
        return
    proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip() if isinstance(proc.stderr, bytes) else str(proc.stderr or "").strip()
        raise BackendUnavailable(f"remote delete failed ({proc.returncode}): {detail}")
    invalidate_remote_parent(location, config)


def remote_exists(location: RemoteLocation, config: dict) -> bool:
    if location.kind == "rclone":
        exe = shutil.which("rclone")
        if not exe:
            raise BackendUnavailable("rclone is not installed")
        proc = subprocess.run([exe, "lsjson", _rclone_target(location)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        return proc.returncode == 0
    base = _ssh_args(location, config)
    cmd = [*base, f"test -e {shlex.quote(location.path)}"]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    return proc.returncode == 0


def stage_remote_input(location: RemoteLocation, destination_root: Path, config: dict, *, dry_run: bool = False, progress: bool = False) -> Path:
    """Materialize a remote file or directory as one create/add/update input."""
    name = location.basename or location.name
    destination = destination_root / name
    if dry_run:
        # Record the transport without touching the network or filesystem.
        download_remote(location, destination, config, dry_run=True, progress=progress)
        return destination
    parent = location.parent
    try:
        siblings = list_remote(location, parent, config)
        match = next((entry for entry in siblings if entry.name == name), None)
    except BackendUnavailable:
        match = None
    is_dir = bool(match and match.is_dir) or location.path.endswith("/")
    if not is_dir:
        download_remote(location, destination, config, progress=progress)
        return destination

    destination.mkdir(parents=True, exist_ok=True)
    if location.kind == "rclone":
        exe = shutil.which("rclone") or "rclone"
        argv = [exe, "copy", _rclone_target(location), os.fspath(destination)]
        record_stage("rclone-download-tree", argv, description=f"stage remote directory {location.raw}")
        proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120, check=False)
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip() if isinstance(proc.stderr, bytes) else str(proc.stderr or "").strip()
            raise BackendUnavailable(f"rclone directory staging failed ({proc.returncode}): {detail}")
        return destination

    # SSH directory staging stays shell-safe by passing the path as a positional
    # argument to sh and using tar on both sides. No user path is interpolated.
    base = _ssh_args(location, config)
    remote_script = 'set -e; path=$1; parent=$(dirname -- "$path"); name=$(basename -- "$path"); cd -- "$parent"; tar -cf - -- "$name"'
    remote_cmd = shlex.join(["sh", "-c", remote_script, "arc-stage", location.path.rstrip("/")])
    ssh_argv = [*base, remote_cmd]
    local_tar = shutil.which("tar") or "tar"
    record_stage("ssh-download-tree", ssh_argv, description=f"stream remote directory {location.raw}")
    record_stage("local-untar", [local_tar, "-xf", "-", "-C", os.fspath(destination_root)], description="materialize remote directory", stdin_from="<ssh-download-tree>")
    first = subprocess.Popen(ssh_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert first.stdout is not None
    second = subprocess.Popen([local_tar, "-xf", "-", "-C", os.fspath(destination_root)], stdin=first.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    first.stdout.close()
    first_err = first.stderr.read() if first.stderr else b""
    first_rc = first.wait()
    _, second_err = second.communicate()
    if first_rc or second.returncode:
        detail = (first_err + (second_err or b"")).decode(errors="replace").strip()
        raise BackendUnavailable(f"SSH directory staging failed: {detail or first_rc or second.returncode}")
    return destination


def stage_remote_for_read(location: RemoteLocation, config: dict, *, dry_run: bool = False, progress: bool = False) -> tuple[Path, Path | None]:
    suffix = Path(location.basename).suffix or ".arc"
    if dry_run:
        return Path(tempfile.gettempdir()) / ("arc-remote-dry-run" + suffix), None
    fd, name = tempfile.mkstemp(prefix="arc-remote-", suffix=suffix)
    os.close(fd)
    target = Path(name)
    try:
        download_remote(location, target, config, progress=progress)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target, target


def _capability_cache_file(location: RemoteLocation, config: dict) -> Path:
    generation = _provider_generation(location, config)
    # v3 makes transport capability/cache provenance explicit and invalidates
    # the pre-R09B cache shape that could not report age/source/atomicity truth.
    return _cache_root().parent / "capabilities" / location.kind / f"{location.alias or location.name}-{generation}-v3.json"


def _capability_ttl(location: RemoteLocation, config: dict) -> int:
    cfg = _configured_remote(config, location.alias or location.name) or {}
    raw = cfg.get("capability_ttl_seconds", config.get("completion", {}).get("capability_ttl_seconds", 300))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 300


def _load_capability_cache(location: RemoteLocation, config: dict) -> dict[str, Any] | None:
    path = _capability_cache_file(location, config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = float(data.get("fetched_at", 0) or 0)
    ttl = _capability_ttl(location, config)
    generation = _provider_generation(location, config)
    if data.get("provider_generation") not in {None, generation}:
        return None
    if time.time() - fetched_at > ttl:
        return None
    value = data.get("value")
    if not isinstance(value, dict):
        return None
    return {
        "value": value,
        "fetched_at": fetched_at,
        "ttl_seconds": ttl,
        "provider_generation": generation,
        "provider": str(data.get("provider") or "cache"),
    }


def _store_capability_cache(
    location: RemoteLocation,
    config: dict,
    value: dict[str, Any],
    *,
    provider: str,
    fetched_at: float,
) -> None:
    path = _capability_cache_file(location, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {
                "schema_version": REMOTE_CAPABILITY_CACHE_VERSION,
                "fetched_at": fetched_at,
                "ttl_seconds": _capability_ttl(location, config),
                "provider_generation": _provider_generation(location, config),
                "provider": provider,
                "value": value,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _rclone_provider_features(exe: str, location: RemoteLocation) -> dict[str, Any]:
    argv = [exe, "backend", "features", _rclone_target(location, "")]
    record_stage("rclone-capabilities", argv, description=f"probe rclone provider features for {location.alias or location.name}")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(getattr(proc, "stdout", "") or "")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("Features") if isinstance(data.get("Features"), dict) else data
    if not isinstance(raw, dict):
        return {}
    # Keep the stable transport-relevant subset. Provider-specific details can
    # vary dramatically and do not become Arc's transport-policy authority.
    keys = ("Move", "DirMove", "Copy", "PutStream", "ListR", "About")
    return {key: raw.get(key) for key in keys if key in raw and isinstance(raw.get(key), (bool, int, str, type(None)))}


def _normalize_remote_backend_inventory(data: Any) -> list[dict[str, Any]] | None:
    if not isinstance(data, list):
        return None
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        candidates = row.get("candidates")
        if not isinstance(candidates, list):
            continue
        typed = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            profile = candidate.get("capability_profile")
            if isinstance(profile, dict):
                typed.append({
                    "binary": candidate.get("binary"),
                    "path": candidate.get("path"),
                    "capability_profile": profile,
                })
        if typed:
            rows.append({"role": row.get("role"), "candidates": typed})
    return rows or None


def _publication_guarantee(location: RemoteLocation, facts: dict[str, Any], *, probed: bool) -> dict[str, Any]:
    if location.kind == "ssh":
        tools = set(str(x) for x in facts.get("tools", []) if x)
        required = {"sh", "cat", "mkdir", "mv", "rm"}
        supported = required.issubset(tools) if probed else False
        return {
            "strategy": "same-parent-temporary-file+rename",
            "scope": "remote-filesystem",
            "finalizer": "mv",
            "temporary_object": True,
            "atomicity": "same-filesystem-rename" if supported else "unproven",
            "guaranteed_atomic": supported,
            "replace_semantics": "rename-replace" if supported else "unproven",
            "evidence": (
                sorted(required)
                if supported
                else (["provider-not-probed"] if not probed else [f"missing:{name}" for name in sorted(required - tools)])
            ),
        }
    provider = facts.get("provider_features") if isinstance(facts.get("provider_features"), dict) else {}
    move = provider.get("Move")
    evidence = []
    if move is True:
        evidence.append("rclone-provider:Move=true")
    elif move is False:
        evidence.append("rclone-provider:Move=false")
    else:
        evidence.append("rclone-provider:Move=unknown")
    if not probed:
        evidence.append("provider-not-probed")
    return {
        "strategy": "temporary-object+rclone-moveto",
        "scope": "remote-provider",
        "finalizer": "rclone moveto",
        "temporary_object": True,
        # rclone's moveto may be a provider-side move or copy+delete fallback.
        # Even Move=true does not prove cross-provider atomic replacement.
        "atomicity": "provider-dependent",
        "guaranteed_atomic": False,
        "replace_semantics": "provider-dependent",
        "server_side_move": move if isinstance(move, bool) else None,
        "evidence": evidence,
    }


def _transport_profile(location: RemoteLocation, facts: dict[str, Any], *, probed: bool) -> dict[str, Any]:
    if location.kind == "ssh":
        tools = set(str(x) for x in facts.get("tools", []) if x)
        features = set(str(x) for x in facts.get("features", []) if x)
        remote_arc = "arc" in tools
        return {
            "locality": {
                "transport": "ssh",
                "read_execution": "local-consumer-over-ssh",
                "write_execution": "local-producer-over-ssh",
                "remote_arc_execution": remote_arc,
            },
            "staging": {
                "file_read": True,
                "directory_read": "tar" in tools,
                "random_access_requires_local_stage": True,
                "stream_read": "cat" in tools if probed else None,
                "stream_write": "cat" in tools if probed else None,
                "machine_safe_listing": bool({"python-scandir", "find-print0"} & features),
            },
            "publication": _publication_guarantee(location, facts, probed=probed),
        }
    provider = facts.get("provider_features") if isinstance(facts.get("provider_features"), dict) else {}
    return {
        "locality": {
            "transport": "rclone",
            "read_execution": "local-consumer-over-rclone",
            "write_execution": "local-producer-over-rclone",
            "remote_arc_execution": False,
        },
        "staging": {
            "file_read": True,
            "directory_read": True,
            "random_access_requires_local_stage": True,
            "stream_read": True,
            "stream_write": True,
            "provider_put_stream": provider.get("PutStream") if probed else None,
        },
        "publication": _publication_guarantee(location, facts, probed=probed),
    }


def _decorate_capabilities(
    location: RemoteLocation,
    config: dict,
    facts: dict[str, Any],
    *,
    source: str,
    fetched_at: float | None,
    provider: str,
    probed: bool,
) -> dict[str, Any]:
    now = time.time()
    generation = _provider_generation(location, config)
    live = source == "live"
    ttl = _capability_ttl(location, config)
    transport = _transport_profile(location, facts, probed=probed)
    result = dict(facts)
    result.update({
        "schema": REMOTE_CAPABILITY_SCHEMA,
        "schema_version": REMOTE_CAPABILITY_SCHEMA_VERSION,
        "transport": location.kind,
        **transport,
        "probe": {
            "source": source,
            "provider": provider,
            "fetched_at": _utc_iso(fetched_at) if fetched_at else None,
            # Live evidence is current by definition. Cache age begins when the
            # provider probe completed, not when it started.
            "age_seconds": 0 if live else (max(0, int(now - fetched_at)) if fetched_at else None),
            "ttl_seconds": ttl,
            "provider_generation": generation,
            "fresh": True if live else bool(fetched_at is not None and now - fetched_at <= ttl),
        },
    })
    return result


def remote_publication_guarantee(
    location: RemoteLocation,
    config: dict,
    *,
    capabilities: dict[str, Any] | None = None,
    refresh: bool = False,
    allow_probe: bool = True,
) -> dict[str, Any]:
    """Return the transport-owned publication guarantee for one destination.

    Dry-run callers set ``allow_probe=False`` so planning remains zero-network;
    the result then stays deliberately unproven instead of claiming atomicity.
    """
    caps = capabilities
    if caps is None and allow_probe:
        try:
            caps = remote_capabilities(location, config, refresh=refresh)
        except BackendUnavailable:
            caps = None
    elif caps is None and hasattr(location, "name"):
        # Normal RemoteLocation instances may reuse already-probed evidence.
        # Lightweight adapters/test doubles with only kind/raw semantics stay
        # zero-I/O and simply receive the fail-closed unproven guarantee.
        cached = _load_capability_cache(location, config)
        if cached is not None:
            caps = _decorate_capabilities(
                location,
                config,
                cached["value"],
                source="cache",
                fetched_at=float(cached["fetched_at"]),
                provider=str(cached["provider"]),
                probed=True,
            )
    if caps is not None and isinstance(caps.get("publication"), dict):
        result = dict(caps["publication"])
        if isinstance(caps.get("probe"), dict):
            result["probe"] = dict(caps["probe"])
        return result
    facts: dict[str, Any] = {"type": location.kind}
    return _publication_guarantee(location, facts, probed=False)


def remote_capabilities(location: RemoteLocation, config: dict, *, refresh: bool = False) -> dict[str, Any]:
    if not refresh:
        cached = _load_capability_cache(location, config)
        if cached is not None:
            return _decorate_capabilities(
                location,
                config,
                cached["value"],
                source="cache",
                fetched_at=float(cached["fetched_at"]),
                provider=str(cached["provider"]),
                probed=True,
            )

    if location.kind == "rclone":
        exe = shutil.which("rclone")
        if not exe:
            raise BackendUnavailable("rclone is not installed")
        provider_features = _rclone_provider_features(exe, location)
        facts = {
            "remote": location.alias or location.name,
            "provider_remote": location.name,
            "type": "rclone",
            "rclone": exe,
            "config": _rclone_config_identity(),
            "provider_features": provider_features,
            # Compatibility projection retained for existing integrations.
            "capabilities": ["cat", "rcat", "lsjson", "staging", "completion", "moveto"],
        }
        provider = "rclone-local+backend-features"
        fetched_at = time.time()
        _store_capability_cache(location, config, facts, provider=provider, fetched_at=fetched_at)
        return _decorate_capabilities(
            location, config, facts, source="live", fetched_at=fetched_at, provider=provider, probed=True
        )

    base = _ssh_args(location, config)
    tools = [
        "arc", "sh", "cat", "mkdir", "mv", "rm", "python3", "find", "stat", "tar", "bsdtar", "7z", "7zz",
        "zip", "unzip", "rar", "unrar", "gzip", "bzip2", "xz", "zstd",
    ]
    tool_words = " ".join(shlex.quote(x) for x in tools)
    shell = (
        "for x in " + tool_words + "; do "
        "command -v \"$x\" >/dev/null 2>&1 && printf 'tool:%s\\n' \"$x\"; done; "
        "if command -v arc >/dev/null 2>&1; then "
        "v=$(arc --version 2>/dev/null); "
        "[ -n \"$v\" ] && printf 'arc-version:%s\\n' \"$v\"; fi; "
        "find . -maxdepth 0 -print0 >/dev/null 2>&1 && printf 'feature:find-print0\\n'; "
        "command -v python3 >/dev/null 2>&1 && printf 'feature:python-scandir\\n'; "
        "command -v stat >/dev/null 2>&1 && printf 'feature:stat\\n'; "
        "if command -v arc >/dev/null 2>&1; then "
        "b=$(arc backends --json 2>/dev/null); "
        "[ -n \"$b\" ] && printf 'arc-backends-json:%s\\n' \"$b\"; fi"
    )
    argv = [*base, shell]
    record_stage("ssh-capabilities", argv, description=f"probe SSH capabilities for {location.alias or location.name}")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError as exc:
        raise BackendUnavailable("ssh is not installed") from exc
    if proc.returncode != 0:
        raise BackendUnavailable(f"SSH capability probe failed for {location.alias or location.name}")
    found: list[str] = []
    features: list[str] = []
    arc_version: str | None = None
    remote_backend_inventory: list[dict[str, Any]] | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("tool:"):
            found.append(line.split(":", 1)[1])
        elif line.startswith("feature:"):
            features.append(line.split(":", 1)[1])
        elif line.startswith("arc-version:"):
            arc_version = line.split(":", 1)[1].strip() or None
        elif line.startswith("arc-backends-json:"):
            try:
                remote_backend_inventory = _normalize_remote_backend_inventory(
                    json.loads(line.split(":", 1)[1])
                )
            except json.JSONDecodeError:
                remote_backend_inventory = None
        elif line in tools:
            # Backward-compatible with older/fake providers used in tests.
            found.append(line)
    facts = {
        "remote": location.alias or location.name,
        "type": "ssh",
        "tools": sorted(set(found)),
        "features": sorted(set(features)),
        "arc_version": arc_version,
        "remote_backend_inventory": remote_backend_inventory,
        # Compatibility projection retained for integrations that predate the
        # typed R09B transport contract.
        "capabilities": ["cat", "staging", "completion", "remote-probe", "remote-arc"],
    }
    provider = "ssh-shell+arc-backends"
    fetched_at = time.time()
    _store_capability_cache(location, config, facts, provider=provider, fetched_at=fetched_at)
    return _decorate_capabilities(
        location, config, facts, source="live", fetched_at=fetched_at, provider=provider, probed=True
    )

