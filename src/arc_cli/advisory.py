from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .backends import _compression_command, backend_capability_profile, backend_inventory, resolve_backend
from .config import ConfigLoadResult, config_inspection_payload
from .doctor import alias_status_rows, collect_doctor_report
from .errors import BackendUnavailable, UsageError
from .formats import extension_for, parse_format
from .transactions import list_transactions

FORMAT_RECOMMENDATION_SCHEMA = "arc.format-recommendation/v1"
BENCHMARK_SCHEMA = "arc.benchmark/v1"
DIAGNOSTICS_BUNDLE_SCHEMA = "arc.diagnostics-bundle/v1"
FORMAT_NAMES = ("tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zstd", "zip", "7z", "rar", "gzip", "bzip2", "xz", "zstd")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _input_facts(path: Path) -> dict[str, Any]:
    p = path.expanduser().resolve()
    if not p.exists() and not p.is_symlink():
        raise UsageError(f"path does not exist: {path}")
    if p.is_symlink():
        return {
            "path": str(p),
            "kind": "symlink",
            "entries": 1,
            "regular_files": 0,
            "directories": 0,
            "symlinks": 1,
            "bytes": 0,
        }
    if p.is_file():
        return {
            "path": str(p),
            "kind": "file",
            "entries": 1,
            "regular_files": 1,
            "directories": 0,
            "symlinks": 0,
            "bytes": p.stat().st_size,
        }
    if not p.is_dir():
        return {
            "path": str(p),
            "kind": "special",
            "entries": 1,
            "regular_files": 0,
            "directories": 0,
            "symlinks": 0,
            "bytes": 0,
        }
    files = dirs = links = total = entries = 0
    for root, dirnames, filenames in os.walk(p, followlinks=False):
        dirs += len(dirnames)
        entries += len(dirnames) + len(filenames)
        base = Path(root)
        for name in [*dirnames, *filenames]:
            child = base / name
            try:
                if child.is_symlink():
                    links += 1
                    continue
                if child.is_file():
                    files += 1
                    total += child.stat().st_size
            except OSError:
                continue
    return {
        "path": str(p),
        "kind": "directory",
        "entries": entries,
        "regular_files": files,
        "directories": dirs,
        "symlinks": links,
        "bytes": total,
    }


def _backend_chain(fmt_name: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    fmt = parse_format(fmt_name)
    chain: list[dict[str, Any]] = []
    blockers: list[str] = []
    try:
        backend = resolve_backend(fmt, "create", config)
        profile = backend.info.capability_profile or backend_capability_profile(backend.info.binary)
        chain.append({
            "role": "archive" if fmt.container else "compressor",
            "binary": backend.info.binary,
            "path": backend.info.path,
            "profile": profile.to_dict(),
        })
    except (BackendUnavailable, Exception) as exc:
        # UnsupportedFormat is intentionally rendered as factual availability evidence.
        blockers.append(str(exc))
        return chain, blockers
    if fmt.container == "tar" and fmt.compression:
        try:
            cmd = _compression_command(fmt.compression, config, None, None)
            binary = Path(cmd[0]).name
            path = shutil.which(binary) or cmd[0]
            profile = backend_capability_profile(binary)
            chain.append({"role": "compressor", "binary": binary, "path": path, "profile": profile.to_dict()})
        except Exception as exc:
            blockers.append(str(exc))
    return chain, blockers


def format_recommendation(path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    facts = _input_facts(Path(path))
    candidates: list[dict[str, Any]] = []
    for name in FORMAT_NAMES:
        fmt = parse_format(name)
        blockers: list[str] = []
        if fmt.is_stream and facts["kind"] != "file":
            blockers.append("single-stream formats require one regular file")
        chain, backend_blockers = _backend_chain(name, config)
        blockers.extend(backend_blockers)
        profiles = [item["profile"] for item in chain]
        metadata = sorted({value for profile in profiles for value in profile.get("metadata", [])})
        encryption = any(bool(profile.get("encryption", {}).get("write")) for profile in profiles)
        multipart = any(bool(profile.get("archive_features", {}).get("multipart")) for profile in profiles)
        streamable = bool(fmt.is_stream or fmt.container == "tar")
        candidates.append({
            "format": name,
            "kind": "stream" if fmt.is_stream else ("archive+compression" if fmt.compression else "archive"),
            "compatible_with_input": not any("single-stream" in item for item in blockers),
            "available": not backend_blockers,
            "blockers": blockers,
            "backend_chain": chain,
            "tradeoffs": {
                "metadata_preservation": metadata,
                "streaming_create": streamable,
                "encryption_write": encryption,
                "multipart": multipart,
                "random_access_container": fmt.container in {"zip", "7z", "rar"},
                "single_stream": fmt.is_stream,
            },
        })
    return {
        "schema": FORMAT_RECOMMENDATION_SCHEMA,
        "schema_version": 1,
        "input": facts,
        "candidates": candidates,
        "selection": None,
        "selection_policy": "factual-only; Arc does not choose a format for the user",
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _corpus_digest(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    digest = hashlib.sha256()
    total = files = 0
    if root.is_file():
        items = [(root.name, root)]
    elif root.is_dir():
        items = [(p.relative_to(root).as_posix(), p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()]
    else:
        raise UsageError(f"benchmark corpus must be a regular file or directory: {path}")
    for name, item in items:
        h = hashlib.sha256()
        with item.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
                total += len(chunk)
        encoded = name.encode("utf-8", "surrogateescape")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(h.digest())
        files += 1
    result = {"sha256": digest.hexdigest(), "bytes": total, "files": files, "kind": "file" if root.is_file() else "directory"}
    if root.is_file():
        result["content_sha256"] = _file_sha256(root)
    return result


def _generated_corpus(root: Path, size_mib: int, seed: int) -> Path:
    path = root / "arc-benchmark-corpus.bin"
    remaining = max(1, int(size_mib)) * 1024 * 1024
    rng = random.Random(seed)
    with path.open("wb") as fh:
        index = 0
        while remaining:
            size = min(65536, remaining)
            # Alternate compressible and pseudo-random blocks while remaining deterministic.
            if index % 2 == 0:
                seed_text = f"arc-benchmark-{seed}-{index}\n".encode()
                block = (seed_text * ((size // len(seed_text)) + 1))[:size]
            else:
                block = rng.randbytes(size)
            fh.write(block)
            remaining -= size
            index += 1
    return path


def _proc_tree_rss_bytes(root_pid: int) -> int | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    ppid: dict[int, int] = {}
    rss: dict[int, int] = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            tail = stat.rsplit(")", 1)[1].strip().split()
            ppid[pid] = int(tail[1])
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError, IndexError):
            continue
        match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
        if match:
            rss[pid] = int(match.group(1)) * 1024
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in ppid.items():
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return sum(rss.get(pid, 0) for pid in selected)


def _run_measured(argv: list[str], env: Mapping[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(env), start_new_session=True)
    peak: int | None = None
    while proc.poll() is None:
        current = _proc_tree_rss_bytes(proc.pid)
        if current is not None:
            peak = max(peak or 0, current)
        time.sleep(0.02)
    stdout, stderr = proc.communicate()
    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "returncode": proc.returncode,
        "seconds": elapsed,
        "peak_rss_bytes": peak,
        "memory_method": "linux-proc-process-tree-rss" if peak is not None else None,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }


def _arc_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1])
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not current else src + os.pathsep + current
    env["ARC_PROGRESS"] = "never"
    return env


def benchmark(
    config: dict[str, Any],
    *,
    corpus: str | Path | None = None,
    formats: list[str] | None = None,
    iterations: int = 1,
    size_mib: int = 8,
    seed: int = 12345,
) -> dict[str, Any]:
    if iterations < 1 or iterations > 100:
        raise UsageError("--iterations must be between 1 and 100")
    if size_mib < 1 or size_mib > 4096:
        raise UsageError("--size-mib must be between 1 and 4096")
    selected = list(dict.fromkeys(formats or FORMAT_NAMES))
    unknown = [name for name in selected if name not in FORMAT_NAMES]
    if unknown:
        raise UsageError("unsupported benchmark format(s): " + ", ".join(unknown))
    with tempfile.TemporaryDirectory(prefix="arc-benchmark-") as tmp:
        work = Path(tmp)
        source = Path(corpus).expanduser().resolve() if corpus is not None else _generated_corpus(work, size_mib, seed)
        if not source.exists():
            raise UsageError(f"benchmark corpus does not exist: {source}")
        corpus_info = _corpus_digest(source)
        corpus_info.update({
            "source": "user" if corpus is not None else "generated",
            "path": str(source) if corpus is not None else None,
            "seed": None if corpus is not None else seed,
            "requested_size_mib": None if corpus is not None else size_mib,
        })
        env = _arc_subprocess_env()
        results: list[dict[str, Any]] = []
        for name in selected:
            fmt = parse_format(name)
            if fmt.is_stream and not source.is_file():
                results.append({"format": name, "status": "unavailable", "reason": "single-stream benchmark requires a regular-file corpus", "iterations": []})
                continue
            chain, blockers = _backend_chain(name, config)
            if blockers:
                results.append({"format": name, "status": "unavailable", "reason": "; ".join(blockers), "backend_chain": chain, "iterations": []})
                continue
            run_rows: list[dict[str, Any]] = []
            for index in range(iterations):
                suffix = extension_for(fmt)
                archive = work / f"bench-{name.replace('.', '-')}-{index}{suffix}"
                out_dir = work / f"out-{name.replace('.', '-')}-{index}"
                create_argv = [sys.executable, "-m", "arc_cli", "create", str(archive), str(source), "--destination-policy", "replace", "--progress", "never"]
                encode = _run_measured(create_argv, env)
                archive_bytes = archive.stat().st_size if archive.is_file() else 0
                decode: dict[str, Any] | None = None
                verified = False
                if encode["returncode"] == 0:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    decode_argv = [sys.executable, "-m", "arc_cli", "extract", str(archive), "-o", str(out_dir), "--progress", "never"]
                    decode = _run_measured(decode_argv, env)
                    if decode["returncode"] == 0:
                        extracted_files = [p for p in out_dir.rglob("*") if p.is_file() and not p.is_symlink()]
                        if source.is_file() and len(extracted_files) == 1:
                            verified = _file_sha256(extracted_files[0]) == corpus_info.get("content_sha256")
                        elif source.is_dir():
                            roots = [p for p in out_dir.iterdir() if p.name == source.name]
                            if roots and roots[0].is_dir():
                                verified = _corpus_digest(roots[0])["sha256"] == corpus_info["sha256"]
                input_bytes = int(corpus_info["bytes"])
                encoded_to_input = (archive_bytes / input_bytes) if input_bytes else None
                compression_ratio = (input_bytes / archive_bytes) if archive_bytes else None
                row = {
                    "iteration": index + 1,
                    "archive_bytes": archive_bytes,
                    # Keep `ratio` as the compact encoded/input projection while
                    # publishing explicit names so machine consumers never need
                    # to guess which compression-ratio convention Arc uses.
                    "ratio": encoded_to_input,
                    "encoded_to_input_ratio": encoded_to_input,
                    "compression_ratio": compression_ratio,
                    "space_savings_fraction": None if encoded_to_input is None else 1.0 - encoded_to_input,
                    "encode": {
                        **encode,
                        "throughput_bytes_per_second": (input_bytes / float(encode["seconds"])) if encode["returncode"] == 0 and input_bytes else None,
                    },
                    "decode": None if decode is None else {
                        **decode,
                        "throughput_bytes_per_second": (input_bytes / float(decode["seconds"])) if decode["returncode"] == 0 and input_bytes else None,
                    },
                    "verified_roundtrip": verified,
                }
                run_rows.append(row)
                shutil.rmtree(out_dir, ignore_errors=True)
                archive.unlink(missing_ok=True)
            successful = [row for row in run_rows if row["encode"]["returncode"] == 0 and row.get("decode") and row["decode"]["returncode"] == 0 and row["verified_roundtrip"]]
            results.append({
                "format": name,
                "status": "ok" if len(successful) == len(run_rows) else "failed",
                "backend_chain": chain,
                "iterations": run_rows,
            })
    return {
        "schema": BENCHMARK_SCHEMA,
        "schema_version": 1,
        "generated_at": _utc_now(),
        "host_specific": True,
        "host": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "arc_version": __version__,
        },
        "corpus": corpus_info,
        "iterations": iterations,
        "results": results,
    }


def _binary_version(path: str) -> str | None:
    """Return a bounded human-readable version line without trusting backend encoding.

    Some native tools do not keep ``--version`` purely textual.  Termux's
    ``bzip2`` can emit its banner followed by bytes from a compressed stream,
    which makes ``subprocess.run(..., text=True)`` raise ``UnicodeDecodeError``
    under strict UTF-8 decoding.  Diagnostics must never crash merely because
    a backend's version output contains arbitrary bytes.
    """
    for args in ([path, "--version"], [path, "-V"]):
        try:
            proc = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        raw = proc.stdout or b""
        if isinstance(raw, str):  # Defensive for monkeypatched subprocess results.
            text = raw
        else:
            text = raw.decode("utf-8", errors="replace")
        text = text.strip()
        if text:
            return text.splitlines()[0][:500]
    return None


def _sensitive_env_values() -> set[str]:
    marker = re.compile(r"(?:PASS|PASSWORD|TOKEN|SECRET|CREDENTIAL|AUTH|PRIVATE[_-]?KEY|API[_-]?KEY)", re.I)
    return {value for key, value in os.environ.items() if value and marker.search(key)}


def _redact_string(value: str, secrets: set[str]) -> str:
    text = value
    for secret in sorted(secrets, key=len, reverse=True):
        if text == secret:
            return "***"
        if len(secret) >= 3 and secret in text:
            text = text.replace(secret, "***")
    text = re.sub(r"(--(?:source-|left-|right-)?password(?:=|\s+))\S+", r"\1***", text)
    return text


def redact_payload(value: Any, *, secrets: set[str] | None = None, key: str = "") -> Any:
    secrets = secrets if secrets is not None else _sensitive_env_values()
    sensitive_key = bool(re.search(r"(?:password|passphrase|token|secret|credential|private[_-]?key|api[_-]?key)", key, re.I))
    if sensitive_key and value not in (None, ""):
        return "***"
    if isinstance(value, dict):
        return {str(k): redact_payload(v, secrets=secrets, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_payload(item, secrets=secrets, key=key) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item, secrets=secrets, key=key) for item in value]
    if isinstance(value, str):
        return _redact_string(value, secrets)
    return value


def _config_support_payload(result: ConfigLoadResult) -> dict[str, Any]:
    payload = config_inspection_payload(result)
    redacted = redact_payload(payload)
    # Environment provenance is useful, but the bundle never needs its actual value.
    effective = redacted.get("effective") if isinstance(redacted, dict) else None
    if isinstance(effective, dict):
        for item in effective.values():
            if not isinstance(item, dict):
                continue
            if item.get("source") == "environment":
                item["value"] = "***"
            for layer in item.get("layers", []):
                if isinstance(layer, dict) and layer.get("source") == "environment":
                    layer["value"] = "***"
    return redacted


def _cache_base() -> Path:
    explicit = os.environ.get("ARC_CACHE_HOME")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "arc"
    return Path.home() / ".cache" / "arc"


def _cached_remote_evidence(limit: int = 20) -> list[dict[str, Any]]:
    root = _cache_base() / "capabilities"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    files = sorted((p for p in root.rglob("*.json") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"cache_file": str(path.relative_to(root)), "evidence": redact_payload(payload)})
    return rows


def _recent_transactions(limit: int) -> list[dict[str, Any]]:
    rows = list_transactions(include_completed=True)[: max(0, limit)]
    return redact_payload(rows)


def _backend_support(config: dict[str, Any], *, valid_config: bool) -> list[dict[str, Any]]:
    rows = backend_inventory(config if valid_config else {}, environ=None if valid_config else {})
    for role in rows:
        for candidate in role.get("candidates", []):
            path = candidate.get("path")
            candidate["version"] = _binary_version(str(path)) if path else None
    return rows


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_diagnostics_bundle(
    config_result: ConfigLoadResult,
    *,
    output: str | Path | None = None,
    recent: int = 20,
    force: bool = False,
) -> dict[str, Any]:
    if recent < 0 or recent > 1000:
        raise UsageError("--recent must be between 0 and 1000")
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = Path.cwd() / f"arc-diagnostics-{stamp}.zip"
    else:
        target = Path(output).expanduser()
    if target.exists() and not force:
        raise UsageError(f"diagnostics bundle already exists: {target}; use --force to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    config = config_result.data if config_result.valid else {}
    doctor = collect_doctor_report()
    files: dict[str, Any] = {
        "runtime.json": {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "arc_version": __version__,
            "python": {"version": sys.version, "executable": sys.executable, "implementation": platform.python_implementation()},
            "platform": {"platform": platform.platform(), "system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        },
        "config.json": _config_support_payload(config_result),
        "backends.json": _backend_support(config, valid_config=config_result.valid),
        "aliases.json": redact_payload(alias_status_rows()),
        "doctor.json": redact_payload(doctor),
        "diagnostics.json": {"schema_version": 1, "recent_transactions": _recent_transactions(recent)},
        "remote-capabilities.json": {"schema_version": 1, "cached": _cached_remote_evidence(recent)},
    }
    manifest = {
        "schema": DIAGNOSTICS_BUNDLE_SCHEMA,
        "schema_version": 1,
        "generated_at": _utc_now(),
        "redaction": {
            "secret_environment_values": True,
            "password_like_keys": True,
            "archive_contents_included": False,
            "network_probe_performed": False,
        },
        "files": [],
    }
    encoded: dict[str, bytes] = {}
    for name, payload in files.items():
        data = _json_bytes(redact_payload(payload))
        encoded[name] = data
        manifest["files"].append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest_bytes = _json_bytes(manifest)
    encoded["manifest.json"] = manifest_bytes
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(encoded):
                info = zipfile.ZipInfo(name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zf.writestr(info, encoded[name])
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "schema": DIAGNOSTICS_BUNDLE_SCHEMA,
        "schema_version": 1,
        "path": str(target.resolve()),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "files": [item["name"] for item in manifest["files"]] + ["manifest.json"],
        "archive_contents_included": False,
        "network_probe_performed": False,
    }
