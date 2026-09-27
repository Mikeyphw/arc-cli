from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

from rich.filesize import decimal
from rich.table import Table
from rich.text import Text

from .backends import backend_inventory, resolve_backend, run_backend
from .completion import FORMATS, completion_candidates, completion_mode, encode_candidates_nul, zsh_completion
from .config import get_profile, load_config
from .errors import ArcError, BackendUnavailable, ConflictError, CorruptArchive, PasswordError, UnsafeArchive, UnsupportedFormat, UsageError
from .execution import begin_plan, emit_after, record_stage
from .filtering import build_manifest, expand_rule_files, filter_members
from . import __version__
from .formats import CREATE_SUFFIX_SHORTCUTS, detect, extension_for, infer_from_name, parse_format, resolve_create_format
from .interactive import choose_auto, filesystem_candidates, rg_files, yazi_choose
from .model import FilterRule, Member
from .progress import ProgressReporter, console, progress_enabled, stdout_console
from .remote import (
    RemoteLocation,
    clear_completion_cache,
    completion_cache_rows,
    complete_remote,
    configured_remote_names,
    download_remote,
    invalidate_remote_directory,
    invalidate_remote_parent,
    list_remote,
    parse_remote,
    remote_capabilities,
    remote_exists,
    same_remote,
    ssh_command_prefix,
    stage_remote_for_read,
    stage_remote_input,
    stream_pipeline_to_remote,
    stream_remote_to_local,
    upload_remote,
)
from .safety import validate_members


class RuleAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        rules = getattr(namespace, "filter_rules", None)
        if rules is None:
            rules = []
            setattr(namespace, "filter_rules", rules)
        kind = option_string.lstrip("-")
        rules.append((kind, values))


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    idx = argv.index("--")
    return argv[:idx], argv[idx + 1 :]


def add_common(
    p: argparse.ArgumentParser,
    *,
    create: bool = False,
    extract: bool = False,
    member_filter: bool = False,
    format_option: bool = True,
):
    if format_option:
        p.add_argument("-F", "--format")
    p.add_argument("--backend")
    p.add_argument("--no-fallback", action="store_true", help="use only the first configured compatible backend preference")
    p.add_argument("--profile", help="apply a named config profile before explicit CLI overrides")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show-command", action="store_true")
    p.add_argument("--show-native", nargs="?", const="after", choices=["before", "after", "both"], default=None, help="show the native command plan")
    p.add_argument("--native-style", choices=["exact", "reproducible"], default=None)
    p.add_argument("--execution", choices=["auto", "local", "remote"], default=None, help="remote archive execution strategy")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--progress", choices=["auto", "always", "never"], default=None)
    p.add_argument("--yazi", nargs="?", const="auto", choices=["auto", "archive", "inputs", "output"])
    p.add_argument("--password", nargs="?", const="__PROMPT__", help="archive password; omit value to prompt securely")
    p.add_argument("--password-file", help="read password from first line of file")
    p.add_argument("--password-env", metavar="NAME", help="read password from environment variable NAME")
    if create:
        p.add_argument("--level", type=int, choices=range(0, 10))
        p.add_argument("--threads", type=int)
        p.add_argument("--add-extension", action="store_true")
        p.add_argument("--follow-symlinks", action="store_true")
        p.add_argument("--one-file-system", action="store_true")
        p.add_argument("--preserve-owner", action="store_true")
        p.add_argument("--preserve-acls", action="store_true")
        p.add_argument("--preserve-xattrs", action="store_true")
        p.add_argument("--exclude", action=RuleAction)
        p.add_argument("--include", action=RuleAction)
        p.add_argument("--exclude-from", action=RuleAction)
        p.add_argument("--include-from", action=RuleAction)
    elif member_filter:
        p.add_argument("--exclude", action=RuleAction)
        p.add_argument("--include", action=RuleAction)
        p.add_argument("--exclude-from", action=RuleAction)
        p.add_argument("--include-from", action=RuleAction)
    if extract:
        p.add_argument("-o", "--output", default=".")
        group = p.add_mutually_exclusive_group()
        group.add_argument("--overwrite", action="store_true")
        group.add_argument("--skip-existing", action="store_true")
        group.add_argument("--rename-existing", action="store_true")
        p.add_argument("--unsafe-paths", action="store_true")
        p.add_argument("--stdout", action="store_true", help="write one extracted member to stdout")
        p.add_argument("--preserve-owner", action="store_true")
        p.add_argument("--preserve-acls", action="store_true")
        p.add_argument("--preserve-xattrs", action="store_true")


def _add_create_suffix_shortcuts(p: argparse.ArgumentParser) -> None:
    section = p.add_argument_group(
        "create suffix shortcuts",
        "select the create format explicitly; if ARCHIVE has no known archive suffix, append the selected suffix",
    )
    group = section.add_mutually_exclusive_group()
    group.add_argument("-F", "--format")
    for flag, fmt, suffix in CREATE_SUFFIX_SHORTCUTS:
        group.add_argument(
            flag,
            dest="format_shortcut",
            action="store_const",
            const=(fmt, suffix),
            help=f"create {fmt} and use {suffix} when ARCHIVE has no recognized archive suffix",
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arc", description="Normalized archive CLI over native backends")
    p.add_argument("--version", action="version", version="arc 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("identify", help="identify archive format")
    q.add_argument("files", nargs="*")
    q.add_argument("-F", "--format")
    q.add_argument("--json", action="store_true")
    q.add_argument("--yazi", nargs="?", const="archive", choices=["auto", "archive"])
    q.add_argument("--show-native", nargs="?", const="after", choices=["before", "after", "both"], default=None)
    q.add_argument("--native-style", choices=["exact", "reproducible"], default=None)
    q.add_argument("--execution", choices=["auto", "local", "remote"], default=None)

    for name in ["list", "test"]:
        q = sub.add_parser(name)
        q.add_argument("archive", nargs="?")
        if name == "list":
            q.add_argument("members", nargs="*")
        add_common(q, member_filter=True)

    q = sub.add_parser("extract")
    q.add_argument("archive", nargs="?")
    q.add_argument("members", nargs="*")
    add_common(q, extract=True, member_filter=True)

    for name in ["create", "add", "update"]:
        q = sub.add_parser(name)
        q.add_argument("archive")
        q.add_argument("inputs", nargs="*")
        add_common(q, create=True, format_option=name != "create")
        if name == "create":
            _add_create_suffix_shortcuts(q)
            q.add_argument("--overwrite", action="store_true")

    q = sub.add_parser("remove")
    q.add_argument("archive", nargs="?")
    q.add_argument("members", nargs="*")
    add_common(q)

    q = sub.add_parser("backends")
    q.add_argument("--json", action="store_true")
    q.add_argument("--remote")
    q = sub.add_parser("formats")
    q.add_argument("--json", action="store_true")
    q.add_argument("--remote")
    c = sub.add_parser("completion")
    c.add_argument("action", choices=["zsh", "cache", "refresh", "clear-cache"])
    c.add_argument("location", nargs="?")
    c.add_argument("--json", action="store_true")
    return p


def _create_format_options(args) -> tuple[str | None, bool, str | None]:
    shortcut = getattr(args, "format_shortcut", None)
    explicit = getattr(args, "format", None)
    if shortcut is not None and explicit:
        raise UsageError("use either -F/--format or a create suffix shortcut, not both")
    if shortcut is not None:
        fmt, suffix = shortcut
        return fmt, True, suffix
    return explicit, bool(getattr(args, "add_extension", False)), None


def _resolve_create_target(args, path: str | Path) -> tuple[object, Path]:
    explicit, add_extension, suffix = _create_format_options(args)
    return resolve_create_format(path, explicit, add_extension, extension=suffix)


def _explicit_format(args) -> str | None:
    if getattr(args, "command", None) == "create":
        explicit, _add, _suffix = _create_format_options(args)
        return explicit
    return getattr(args, "format", None)


def _display_invocation(argv: list[str], args) -> str:
    """Render the Arc argv we actually received, with credentials redacted.

    Shell syntax such as ``~`` expansion or the user's original quote style is
    gone before Arc starts, so this is intentionally the received argv rather
    than a claim that we can reconstruct the literal shell input.
    """
    words = ["arc", *argv]
    secret = getattr(args, "password", None)
    if secret and secret != "__PROMPT__":
        words = ["***" if word == secret else word.replace(f"--password={secret}", "--password=***") for word in words]
    return shlex.join(words)


def _show_invocation(argv: list[str], args) -> None:
    if getattr(args, "quiet", False) or getattr(args, "json", False):
        return
    if getattr(args, "command", None) not in {"identify", "list", "extract", "create", "add", "update", "remove", "test"}:
        return
    # Keep redirected/scripted output clean. In an interactive terminal, emit
    # one logical line and let the terminal soft-wrap it visually. Rich table
    # folding would insert real newlines/indentation, making a copied command
    # no longer directly pasteable on narrow Termux screens.
    if not console.is_terminal:
        return
    line = Text.assemble(
        ("Command  ", "bold cyan"),
        (_display_invocation(argv, args), "dim"),
    )
    console.print(line, soft_wrap=True)


def _scan_status_text(visited: int, selected_count: int, selected_bytes: int, width: int) -> str:
    if width < 36:
        return f"[bold]Scanning…[/] {visited} · {selected_count} sel"
    if width < 46:
        return f"[bold]Scanning…[/] {visited} seen · {selected_count} selected"
    if width < 72:
        return f"[bold]Scanning…[/] {selected_count} selected · {decimal(selected_bytes)}"
    return f"[bold]Scanning inputs…[/] {visited} visited, {selected_count} selected, {decimal(selected_bytes)}"


def _plural_files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _backend_display(backend, meta: dict) -> str:
    names = [backend.info.binary]
    pipeline = meta.get("pipeline")
    if pipeline:
        binary = Path(str(pipeline[0])).name
        if binary and binary != names[-1]:
            names.append(binary)
    return " → ".join(names)


def _show_create_plan(path: Path, fmt, backend, meta: dict, files: int, original_bytes: int) -> None:
    if not console.is_terminal:
        return
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=8, no_wrap=True)
    grid.add_column(ratio=1, overflow="fold")
    grid.add_row(Text("Create", style="bold cyan"), Text(str(path), style="bold"))
    grid.add_row("", Text(f"{_plural_files(files)} · {decimal(original_bytes)}", style="dim"))
    grid.add_row("", Text(f"{fmt.canonical} · backend {_backend_display(backend, meta)}", style="dim"))
    console.print(grid)


def _compression_metrics(original_bytes: int, compressed_bytes: int) -> dict[str, float | None]:
    if original_bytes <= 0:
        return {"compressed_percent": None, "saved_percent": None, "ratio": None}
    compressed_percent = (compressed_bytes / original_bytes) * 100.0
    saved = 100.0 - compressed_percent
    ratio = (original_bytes / compressed_bytes) if compressed_bytes > 0 else None
    return {"compressed_percent": compressed_percent, "saved_percent": saved, "ratio": ratio}


def _format_percent(value: float) -> str:
    """Format percentages without rounding tiny/non-total values to 0%/100%."""
    for digits in range(1, 5):
        rounded = round(value, digits)
        if value != 0 and rounded == 0:
            continue
        if 0 < value < 100 and rounded >= 100:
            continue
        return f"{value:.{digits}f}%"
    return f"{value:.4f}%"


def _print_create_success(path: Path, original_bytes: int, compressed_bytes: int, backend_name: str) -> None:
    metrics = _compression_metrics(original_bytes, compressed_bytes)
    stdout_console.print(Text.assemble(("✓ ", "bold green"), ("Created  ", "bold"), (str(path), "")))

    compressed_percent = metrics["compressed_percent"]
    saved = metrics["saved_percent"]
    ratio = metrics["ratio"]
    if saved is None:
        rate = "n/a (empty input)"
        savings = "n/a"
    elif saved >= 0:
        rate = f"{_format_percent(compressed_percent)} of original"
        savings = f"{_format_percent(saved)} saved"
    else:
        rate = f"{_format_percent(compressed_percent)} of original"
        savings = f"{_format_percent(abs(saved))} larger"
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=13, no_wrap=True, style="dim")
    grid.add_column(ratio=1, overflow="fold")
    grid.add_row("Original", decimal(original_bytes))
    grid.add_row("Compressed", decimal(compressed_bytes))
    grid.add_row("Compression", rate)
    grid.add_row("Saved", savings)
    if ratio is not None:
        grid.add_row("Ratio", f"{ratio:.2f}×")
    grid.add_row("Backend", backend_name)
    stdout_console.print(grid)


def _apply_profile(args, config: dict) -> None:
    name = getattr(args, "profile", None)
    if not name:
        return
    profile = get_profile(config, name)
    allowed = {
        "backend", "progress", "level", "threads", "yazi", "show_native", "native_style", "execution",
        "exclude", "include", "exclude_from", "include_from",
    }
    unknown = sorted(set(profile) - allowed)
    if unknown:
        raise UsageError(f"profile {name!r} contains unsupported option(s): {', '.join(unknown)}")

    normalized_scalars: dict[str, object] = {}
    if "backend" in profile:
        if not isinstance(profile["backend"], str):
            raise UsageError(f"profile {name!r} option 'backend' must be a string")
        normalized_scalars["backend"] = profile["backend"]
    if "progress" in profile:
        if profile["progress"] not in {"auto", "always", "never"}:
            raise UsageError(f"profile {name!r} option 'progress' must be auto, always, or never")
        normalized_scalars["progress"] = profile["progress"]
    if "level" in profile:
        try:
            value = int(profile["level"])
        except (TypeError, ValueError) as exc:
            raise UsageError(f"profile {name!r} option 'level' must be an integer") from exc
        if not 0 <= value <= 9:
            raise UsageError(f"profile {name!r} option 'level' must be between 0 and 9")
        normalized_scalars["level"] = value
    if "threads" in profile:
        try:
            normalized_scalars["threads"] = int(profile["threads"])
        except (TypeError, ValueError) as exc:
            raise UsageError(f"profile {name!r} option 'threads' must be an integer") from exc
    if "yazi" in profile:
        if profile["yazi"] not in {"auto", "archive", "inputs", "output"}:
            raise UsageError(f"profile {name!r} option 'yazi' is invalid")
        normalized_scalars["yazi"] = profile["yazi"]
    if "show_native" in profile:
        if profile["show_native"] not in {"before", "after", "both"}:
            raise UsageError(f"profile {name!r} option 'show_native' is invalid")
        normalized_scalars["show_native"] = profile["show_native"]
    if "native_style" in profile:
        if profile["native_style"] not in {"exact", "reproducible"}:
            raise UsageError(f"profile {name!r} option 'native_style' is invalid")
        normalized_scalars["native_style"] = profile["native_style"]
    if "execution" in profile:
        if profile["execution"] not in {"auto", "local", "remote"}:
            raise UsageError(f"profile {name!r} option 'execution' is invalid")
        normalized_scalars["execution"] = profile["execution"]

    for key, value in normalized_scalars.items():
        if hasattr(args, key) and getattr(args, key) is None:
            setattr(args, key, value)

    profile_rules: list[tuple[str, str]] = []
    for key, kind in (("exclude", "exclude"), ("include", "include"), ("exclude_from", "exclude-from"), ("include_from", "include-from")):
        raw = profile.get(key)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(value, str) for value in values):
            raise UsageError(f"profile {name!r} option {key!r} must be a string or list of strings")
        profile_rules.extend((kind, value) for value in values)
    if profile_rules:
        # Profile rules run before CLI rules. The normalized matcher is ordered
        # and last-match-wins, so an explicit CLI rule can always override a
        # profile decision.
        args.filter_rules = profile_rules + (getattr(args, "filter_rules", None) or [])


def _validate_yazi_context(args) -> None:
    value = getattr(args, "yazi", None)
    if value is None or value == "auto":
        return
    allowed = {
        "identify": {"archive"},
        "list": {"archive"},
        "test": {"archive"},
        "extract": {"archive", "output"},
        "create": {"inputs"},
        "add": {"inputs"},
        "update": {"inputs"},
        "remove": {"archive"},
    }.get(getattr(args, "command", ""), set())
    if value not in allowed:
        choices = ", ".join(sorted(allowed)) or "none"
        raise UsageError(f"--yazi={value} is not valid for {args.command}; valid target(s): {choices}")


def _apply_defaults(args, config: dict) -> None:
    if hasattr(args, "progress") and args.progress is None:
        value = os.environ.get("ARC_PROGRESS") or config.get("ui", {}).get("progress") or "auto"
        if value not in {"auto", "always", "never"}:
            raise UsageError(f"invalid configured progress mode: {value}")
        args.progress = value
    if hasattr(args, "level") and args.level is None:
        raw = os.environ.get("ARC_LEVEL", config.get("create", {}).get("level"))
        if raw is not None:
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise UsageError(f"invalid configured compression level: {raw}") from exc
            if not 0 <= value <= 9:
                raise UsageError("configured compression level must be between 0 and 9")
            args.level = value
    if hasattr(args, "threads") and args.threads is None:
        raw = os.environ.get("ARC_THREADS", config.get("create", {}).get("threads"))
        if raw is not None:
            try:
                args.threads = int(raw)
            except (TypeError, ValueError) as exc:
                raise UsageError(f"invalid configured thread count: {raw}") from exc

    if hasattr(args, "show_native") and args.show_native is None:
        value = config.get("ui", {}).get("show_native")
        if value is not None:
            if value not in {"before", "after", "both"}:
                raise UsageError(f"invalid configured show_native mode: {value}")
            args.show_native = value
    if hasattr(args, "native_style") and args.native_style is None:
        value = config.get("ui", {}).get("native_command_style", "reproducible")
        if value not in {"exact", "reproducible"}:
            raise UsageError(f"invalid configured native command style: {value}")
        args.native_style = value
    if hasattr(args, "execution") and args.execution is None:
        value = config.get("remote", {}).get("execution", "auto")
        if value not in {"auto", "local", "remote"}:
            raise UsageError(f"invalid configured remote execution strategy: {value}")
        args.execution = value


def _warn_extension_mismatch(path: Path, fmt, args) -> None:
    if str(path) == "-" or getattr(args, "quiet", False):
        return
    hint = infer_from_name(path)
    if hint and hint.canonical != fmt.canonical:
        console.print(f"[yellow]warning:[/] filename suggests {hint.canonical}, but selected/detected format is {fmt.canonical}")


def _atomic_replace(temp: Path, final: Path) -> None:
    with temp.open("rb") as fh:
        os.fsync(fh.fileno())
    os.replace(temp, final)
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(final.parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _backend_requirements(args, fmt, operation: str, password: str | None) -> set[str]:
    required: set[str] = set()
    if password:
        required.add("password")
    # TAR compression threading belongs to the external compressor, not the TAR container backend.
    if getattr(args, "threads", None) is not None and fmt.container != "tar":
        required.add("threads")
    return required


def _require_tar_metadata_backend(args, backend) -> None:
    requested = any(getattr(args, name, False) for name in ("preserve_owner", "preserve_acls", "preserve_xattrs"))
    if requested and backend.name != "tar":
        raise UnsupportedFormat("normalized ownership/ACL/xattr preservation currently requires the tar/bsdtar backend")


def _resolve_password(args) -> str | None:
    sources = sum(getattr(args, name, None) is not None for name in ("password", "password_file", "password_env"))
    if sources > 1:
        raise UsageError("use only one of --password, --password-file, or --password-env")
    if getattr(args, "password_file", None):
        try:
            return Path(args.password_file).expanduser().read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError) as exc:
            raise UsageError(f"cannot read password file: {exc}") from exc
    if getattr(args, "password_env", None):
        value = os.environ.get(args.password_env)
        if value is None:
            raise UsageError(f"password environment variable is not set: {args.password_env}")
        return value
    value = getattr(args, "password", None)
    if value == "__PROMPT__":
        if not sys.stdin.isatty():
            raise UsageError("--password without a value requires an interactive TTY")
        return getpass.getpass("Archive password: ")
    return value


def _materialize_stdin(path: Path, explicit_format: str | None, *, dry_run: bool = False) -> tuple[Path, Path | None]:
    if str(path) != "-":
        return path, None
    if not explicit_format:
        raise UsageError("archives read from stdin require --format")
    if dry_run:
        return path, None
    suffix = ".arc-stdin"
    fd, name = tempfile.mkstemp(prefix="arc-stdin-", suffix=suffix)
    os.close(fd)
    temp = Path(name)
    with temp.open("wb") as fh:
        shutil.copyfileobj(sys.stdin.buffer, fh)
    return temp, temp


def _choose_archive(yazi: str | None = None) -> str:
    if yazi in {"auto", "archive"}:
        selected = yazi_choose(multi=False)
    else:
        candidates = rg_files()
        selected = choose_auto(candidates, multi=False, prompt="archive> ", preview="arc identify {} 2>/dev/null")
    if not selected:
        raise UsageError("no archive selected")
    return selected[0]


def _resolve_archive_arg(args) -> Path:
    value = getattr(args, "archive", None)
    if not value:
        value = _choose_archive(getattr(args, "yazi", None))
    return Path(value).expanduser()


def _resolve_create_inputs(args) -> list[str]:
    if args.inputs:
        return args.inputs
    if args.yazi in {"auto", "inputs"}:
        picks = yazi_choose(multi=True)
    else:
        picks = choose_auto(filesystem_candidates(include_dirs=True), multi=True, prompt="inputs> ")
    if not picks:
        raise UsageError("no input paths selected")
    return picks


def _member_sizes(members: list[Member]) -> dict[str, int]:
    return {m.name: m.size for m in members if m.kind != "dir"}


def _list_members(backend, archive: Path, password: str | None = None) -> list[Member]:
    return backend.list_members(archive, password=password)


def _validate_destination_parents(output: Path, members: list[Member]) -> None:
    root = output.resolve()
    for m in members:
        current = root
        for part in Path(m.name).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise UnsafeArchive(f"existing destination symlink parent rejected: {current}")


def _preflight_conflicts(output: Path, members: list[Member], args) -> None:
    conflicts = []
    for m in members:
        if m.kind == "dir":
            continue
        target = output / m.name
        if target.exists() or target.is_symlink():
            conflicts.append(target)
            if len(conflicts) >= 10:
                break
    if conflicts and not (args.overwrite or args.skip_existing or args.rename_existing):
        sample = "\n".join(f"  {x}" for x in conflicts)
        raise ConflictError(f"extraction targets already exist; use --overwrite, --skip-existing, or --rename-existing:\n{sample}")


def _filter_rules(args) -> list[FilterRule]:
    return expand_rule_files(getattr(args, "filter_rules", []) or [])


def _select_archive_members(members: list[Member], args) -> list[Member]:
    selected_members = list(members)
    positional = getattr(args, "members", None) or []
    if positional:
        wanted = set(positional)
        selected_members = [
            m for m in selected_members
            if m.name in wanted or any(m.name.startswith(x.rstrip("/") + "/") for x in wanted)
        ]
    rules = _filter_rules(args)
    if rules:
        selected_members = filter_members(selected_members, rules)
    return selected_members


def _stream_output_name(archive: Path) -> str:
    stem = archive.name
    for suffix in (".gzip", ".gz", ".bz2", ".xz", ".zst", ".zstd"):
        if stem.lower().endswith(suffix):
            return stem[:-len(suffix)]
    return stem + ".out"


def _raise_backend_failure(rc: int, meta: dict, *, password: str | None = None, operation: str = "operation") -> None:
    text = "\n".join(meta.get("output_tail", [])).lower()
    password_markers = (
        "wrong password", "incorrect password", "password is incorrect", "bad password",
        "can not open encrypted archive", "cannot open encrypted archive",
        "password required", "encrypted file", "wrong password?",
        "unable to get password", "unable to get passphrase",
    )
    if any(marker in text for marker in password_markers):
        raise PasswordError(f"{operation} failed: password rejected by backend")
    corruption_markers = (
        "crc failed", "crc error", "data error", "unexpected end", "corrupt", "damaged",
        "not a tar archive", "checksum error", "bad zipfile", "end-of-central-directory",
    )
    if operation == "test" or any(marker in text for marker in corruption_markers):
        raise CorruptArchive(f"{operation} failed; backend status {rc}")
    raise ArcError(f"backend exited with status {rc}")


def _needs_exact_name_extraction(backend, members: list[Member]) -> bool:
    return getattr(backend, "mode", None) == "unzip" and any("\n" in m.name or "\r" in m.name for m in members)


def _extract_infozip_exact_names(
    backend, archive: Path, output: Path, members: list[Member], *, password: str | None,
    extra: list[str], args, enabled: bool,
) -> int:
    sizes = _member_sizes(members)
    total = sum(sizes.values())
    with ProgressReporter("Extracting", total, len(sizes), enabled) as rep:
        for member in members:
            target = output / member.name
            if member.kind == "dir":
                if not args.dry_run:
                    target.mkdir(parents=True, exist_ok=True)
                continue
            if member.kind == "symlink":
                if args.show_command or args.dry_run:
                    console.print(f"[bold cyan]link[/] {member.name} -> {member.link_target}")
                if not args.dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(member.link_target or "", target)
                rep.member_done(member.name, member.size)
                continue
            target.parent.mkdir(parents=True, exist_ok=True) if not args.dry_run else None
            cmd, meta = backend.command(
                "print-member", archive, fmt=None, members=[member.name], extra=extra,
                password=password, output=output, dry_run=args.dry_run,
            )
            meta["stdout_file"] = target
            rc = run_backend(cmd, meta, None, {}, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
            if rc != 0:
                _raise_backend_failure(rc, meta, password=password, operation="extract")
            rep.member_done(member.name, member.size)
    return 0


def _extract(args, extra: list[str], config: dict) -> int:
    if args.stdout and args.json:
        raise UsageError("--stdout and --json cannot be used together")
    archive_arg = _resolve_archive_arg(args)
    archive, stdin_temp = _materialize_stdin(archive_arg, args.format, dry_run=args.dry_run)
    password = _resolve_password(args)
    stdout_temp_dir: Path | None = None
    renamed: list[tuple[Path, Path]] = []
    success = False
    try:
        fmt = parse_format(args.format) if str(archive) == "-" and args.format else detect(archive, args.format)
        _warn_extension_mismatch(archive_arg, fmt, args)
        backend = resolve_backend(
            fmt, "extract", config, args.backend,
            required_capabilities=_backend_requirements(args, fmt, "extract", password),
            no_fallback=args.no_fallback,
        )
        _require_tar_metadata_backend(args, backend)
        index_enabled = progress_enabled(args.progress, args.json, args.quiet)
        ctx = console.status("[bold]Indexing archive members…[/]") if index_enabled and not fmt.is_stream else nullcontext()
        with ctx:
            members = _list_members(backend, archive, password) if not fmt.is_stream else []
        selected_members = _select_archive_members(members, args)
        selection_requested = bool(getattr(args, "members", None) or _filter_rules(args))
        if members and not args.unsafe_paths:
            validate_members(selected_members)
        if not fmt.is_stream and selection_requested and not selected_members:
            if args.json:
                print(json.dumps({"operation": "extract", "archive": str(archive_arg), "format": fmt.canonical, "backend": backend.info.binary, "output": str(Path(args.output).expanduser()), "members": 0, "bytes": 0, "skipped": True}))
            return 0

        if str(archive_arg) == "-" and fmt.is_stream and not args.stdout:
            raise UsageError("stream-compressed stdin has no output filename; use --stdout")

        if args.stdout:
            if not fmt.is_stream and (len(selected_members) != 1 or selected_members[0].kind != "file"):
                raise UsageError("--stdout requires exactly one regular archive member")
            if args.dry_run:
                stdout_temp_dir = None
                output = Path(tempfile.gettempdir()) / "arc-dry-run-stdout"
            else:
                stdout_temp_dir = Path(tempfile.mkdtemp(prefix="arc-stdout-"))
                output = stdout_temp_dir
        else:
            output = Path(args.output).expanduser()
            if args.yazi == "output":
                picked = yazi_choose(multi=False)
                if not picked:
                    raise UsageError("no output directory selected")
                output = Path(picked[0])
                if output.exists() and not output.is_dir():
                    raise UsageError(f"Yazi output selection is not a directory: {output}")
            if not args.dry_run:
                output.mkdir(parents=True, exist_ok=True)

        # Stream formats have one implicit output member.  Model it explicitly
        # so normalized conflict handling is identical to archive extraction.
        if fmt.is_stream and not args.stdout:
            selected_members = [Member(_stream_output_name(archive), 0, "file")]

        if selected_members and not args.stdout:
            if not args.unsafe_paths:
                _validate_destination_parents(output, selected_members)
            _preflight_conflicts(output, selected_members, args)
            if args.skip_existing:
                selected_members = [m for m in selected_members if m.kind == "dir" or not (output / m.name).exists()]
                if fmt.is_stream and not selected_members:
                    if args.json:
                        print(json.dumps({"operation": "extract", "archive": str(archive_arg), "format": fmt.canonical, "backend": backend.info.binary, "output": str(output), "members": 0, "bytes": 0, "skipped": True}))
                    return 0
            if args.rename_existing and not args.dry_run:
                for m in selected_members:
                    if m.kind == "dir":
                        continue
                    target = output / m.name
                    if target.exists() or target.is_symlink():
                        i = 1
                        while True:
                            candidate = target.with_name(target.name + f".old.{i}")
                            if not candidate.exists():
                                target.rename(candidate)
                                renamed.append((target, candidate))
                                break
                            i += 1
        selected_names = [m.name for m in selected_members] if (args.members or _filter_rules(args) or args.skip_existing or args.stdout) and not fmt.is_stream else ([] if fmt.is_stream else args.members)
        enabled = progress_enabled(args.progress, args.json, args.quiet) and not args.stdout
        sizes = _member_sizes(selected_members)
        total = sum(sizes.values())
        if _needs_exact_name_extraction(backend, selected_members):
            _extract_infozip_exact_names(
                backend, archive, output, selected_members, password=password, extra=extra, args=args, enabled=enabled
            )
        else:
            cmd, meta = backend.command(
                "extract", archive, fmt=fmt, output=output, members=selected_names, extra=extra,
                overwrite=args.overwrite or args.stdout, skip_existing=args.skip_existing, password=password,
                preserve_owner=args.preserve_owner, preserve_acls=args.preserve_acls, preserve_xattrs=args.preserve_xattrs,
                config=config, dry_run=args.dry_run, no_fallback=args.no_fallback,
            )
            progress_total = 0 if meta.get("progress_indeterminate") else total
            progress_files = 0 if meta.get("progress_indeterminate") else len(sizes)
            with ProgressReporter("Extracting", progress_total, progress_files, enabled) as rep:
                rc = run_backend(cmd, meta, rep, sizes, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
            if rc != 0:
                _raise_backend_failure(rc, meta, password=password, operation="extract")
        if args.stdout and not args.dry_run:
            if fmt.is_stream:
                target = output / _stream_output_name(archive)
            else:
                target = output / selected_members[0].name
            with target.open("rb") as fh:
                shutil.copyfileobj(fh, sys.stdout.buffer)
        elif args.json:
            print(json.dumps({
                "operation": "extract", "archive": str(archive_arg), "format": fmt.canonical,
                "backend": backend.info.binary, "output": str(output), "members": len(sizes), "bytes": total,
            }))
        success = True
        return 0
    finally:
        if not success:
            for original, backup in reversed(renamed):
                try:
                    if backup.exists() or backup.is_symlink():
                        if original.is_symlink() or original.is_file():
                            original.unlink(missing_ok=True)
                        elif original.is_dir():
                            shutil.rmtree(original)
                        backup.rename(original)
                except OSError:
                    pass
        if stdin_temp:
            stdin_temp.unlink(missing_ok=True)
        if stdout_temp_dir:
            shutil.rmtree(stdout_temp_dir, ignore_errors=True)


def _create_like(args, extra: list[str], config: dict) -> int:
    inputs = _resolve_create_inputs(args)
    remote_inputs: list[tuple[str, RemoteLocation]] = []
    dry_run = bool(getattr(args, "dry_run", False))
    for raw in inputs:
        location = parse_remote(str(raw), config, probe_rclone=not dry_run)
        if location is not None:
            remote_inputs.append((raw, location))
    if not remote_inputs:
        return _create_like_local(args, extra, config)

    if getattr(args, "dry_run", False):
        # A dry-run cannot inspect remote directory trees without violating its
        # no-network contract. Record every requested transfer and stop before
        # filesystem scanning/backend execution.
        root = Path(tempfile.gettempdir()) / "arc-remote-input-dry-run"
        for _raw, location in remote_inputs:
            stage_remote_input(location, root, config, dry_run=True, progress=False)
        if not getattr(args, "quiet", False):
            console.print("[dim]dry-run:[/] remote inputs require staging; no network connection was made")
        return 0

    with tempfile.TemporaryDirectory(prefix="arc-remote-inputs-") as td:
        root = Path(td)
        staged: dict[str, str] = {}
        for raw, location in remote_inputs:
            staged[raw] = os.fspath(stage_remote_input(location, root, config, progress=progress_enabled(args.progress, args.json, args.quiet)))
        local_inputs = [staged.get(raw, raw) for raw in inputs]
        local_args = _clone_args(args, inputs=local_inputs)
        return _create_like_local(local_args, extra, config)


def _create_like_local(args, extra: list[str], config: dict) -> int:
    operation = args.command
    inputs = _resolve_create_inputs(args)
    stdout_archive = args.archive == "-"
    if stdout_archive and args.json:
        raise UsageError("archive stdout and --json cannot be used together")
    password = _resolve_password(args)
    if operation == "create":
        fmt, archive = _resolve_create_target(args, args.archive)
        _warn_extension_mismatch(archive, fmt, args)
        if not stdout_archive and archive.exists() and not args.overwrite:
            raise ConflictError(f"archive already exists: {archive}; use --overwrite")
    else:
        if stdout_archive:
            raise UsageError(f"{operation} cannot mutate an archive on stdout")
        archive = Path(args.archive).expanduser()
        fmt = detect(archive, args.format)
        _warn_extension_mismatch(archive, fmt, args)
    raw_rules = getattr(args, "filter_rules", []) or []
    rules = expand_rule_files(raw_rules)
    scan_enabled = progress_enabled(args.progress, args.json, args.quiet)
    if scan_enabled:
        with console.status("[bold]Scanning inputs…[/]") as status:
            def on_scan(visited: int, selected_count: int, selected_bytes: int) -> None:
                if visited == 1 or visited % 128 == 0:
                    status.update(_scan_status_text(visited, selected_count, selected_bytes, console.size.width))
            manifest = build_manifest(
                inputs, rules, follow_symlinks=args.follow_symlinks,
                one_file_system=args.one_file_system, on_scan=on_scan,
            )
    else:
        manifest = build_manifest(
            inputs, rules, follow_symlinks=args.follow_symlinks,
            one_file_system=args.one_file_system,
        )
    if not manifest:
        raise UsageError("no input files remain after filtering")
    backend = resolve_backend(
        fmt, operation, config, args.backend,
        required_capabilities=_backend_requirements(args, fmt, operation, password),
        no_fallback=args.no_fallback,
    )
    _require_tar_metadata_backend(args, backend)

    if operation == "add":
        existing = {m.name.rstrip("/") for m in backend.list_members(archive, password=password)}
        conflicts = [e.member_name for e in manifest if e.member_name.rstrip("/") in existing]
        if conflicts:
            sample = "\n".join(f"  {name}" for name in conflicts[:10])
            raise ConflictError(
                "add would replace existing archive member(s); use update for replacement semantics:\n" + sample
            )

    final_archive = archive
    temp_archive = None
    if operation == "create" and not args.dry_run:
        if stdout_archive:
            fd, tmp = tempfile.mkstemp(prefix="arc-stdout-archive-", suffix=extension_for(fmt))
            os.close(fd)
            Path(tmp).unlink(missing_ok=True)
            temp_archive = Path(tmp)
        else:
            final_archive.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{final_archive.name}.arc-tmp-", suffix=extension_for(fmt), dir=final_archive.parent)
            os.close(fd)
            Path(tmp).unlink(missing_ok=True)
            temp_archive = Path(tmp)
        archive_for_backend = temp_archive
    else:
        archive_for_backend = final_archive

    cmd, meta = backend.command(
        operation, archive_for_backend, fmt=fmt, entries=manifest, extra=extra,
        level=args.level, threads=args.threads, follow_symlinks=args.follow_symlinks,
        config=config, password=password, preserve_owner=args.preserve_owner,
        preserve_acls=args.preserve_acls, preserve_xattrs=args.preserve_xattrs, dry_run=args.dry_run,
        no_fallback=args.no_fallback,
    )
    sizes = {e.member_name: e.size for e in manifest if not e.is_dir}
    original_bytes = sum(sizes.values())
    enabled = progress_enabled(args.progress, args.json, args.quiet) and not stdout_archive
    if operation == "create" and not args.quiet and not args.json and not stdout_archive:
        _show_create_plan(final_archive, fmt, backend, meta, len(sizes), original_bytes)

    compressed_bytes: int | None = None
    try:
        progress_total = 0 if meta.get("progress_indeterminate") else original_bytes
        progress_files = 0 if meta.get("progress_indeterminate") else len(sizes)
        with ProgressReporter("Archiving" if operation == "create" else operation.capitalize(), progress_total, progress_files, enabled) as rep:
            rc = run_backend(cmd, meta, rep, sizes, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
        if rc != 0:
            _raise_backend_failure(rc, meta, password=password, operation=operation)
        if temp_archive and temp_archive.exists():
            if stdout_archive:
                with temp_archive.open("rb") as fh:
                    shutil.copyfileobj(fh, sys.stdout.buffer)
            else:
                _atomic_replace(temp_archive, final_archive)
                compressed_bytes = final_archive.stat().st_size
        elif operation == "create" and not args.dry_run and not stdout_archive and final_archive.exists():
            compressed_bytes = final_archive.stat().st_size
    finally:
        if temp_archive and temp_archive.exists():
            temp_archive.unlink(missing_ok=True)

    if args.json and not stdout_archive:
        payload = {
            "operation": operation,
            "archive": str(final_archive),
            "format": fmt.canonical,
            "files": len(sizes),
            "bytes": original_bytes,
            "backend": backend.info.binary,
        }
        if operation == "create":
            payload["original_bytes"] = original_bytes
            if compressed_bytes is not None:
                metrics = _compression_metrics(original_bytes, compressed_bytes)
                payload["compressed_bytes"] = compressed_bytes
                payload["compression_percent_of_original"] = metrics["compressed_percent"]
                payload["compression_saved_percent"] = metrics["saved_percent"]
                payload["compression_ratio"] = metrics["ratio"]
        print(json.dumps(payload))
    elif not args.quiet and not stdout_archive:
        if operation == "create" and not args.dry_run and compressed_bytes is not None:
            _print_create_success(final_archive, original_bytes, compressed_bytes, _backend_display(backend, meta))
        elif args.dry_run:
            stdout_console.print(f"[cyan]DRY RUN[/] {operation}: {final_archive} ({_plural_files(len(sizes))})")
        else:
            stdout_console.print(f"[green]OK[/] {operation}: {final_archive} ({_plural_files(len(sizes))}, backend={backend.info.binary})")
    return 0


def _list_or_test(args, extra: list[str], config: dict) -> int:
    archive_arg = _resolve_archive_arg(args)
    archive, stdin_temp = _materialize_stdin(archive_arg, args.format, dry_run=args.dry_run)
    password = _resolve_password(args)
    try:
        fmt = parse_format(args.format) if str(archive) == "-" and args.format else detect(archive, args.format)
        _warn_extension_mismatch(archive_arg, fmt, args)
        rules = _filter_rules(args)
        if fmt.is_stream and args.command == "list":
            raise UnsupportedFormat(f"{fmt.canonical} is a stream compression format and has no member list")
        if fmt.is_stream and rules:
            raise UnsupportedFormat(f"{fmt.canonical} is a stream compression format and has no members to filter")
        backend = resolve_backend(
            fmt, args.command, config, args.backend,
            required_capabilities=_backend_requirements(args, fmt, args.command, password),
            no_fallback=args.no_fallback,
        )

        if args.command == "list":
            ctx = console.status("[bold]Indexing archive members…[/]") if progress_enabled(args.progress, args.json, args.quiet) and not args.dry_run else nullcontext()
            with ctx:
                members = [] if args.dry_run and str(archive) == "-" else backend.list_members(archive, password=password)
            members = _select_archive_members(members, args)
            selection_requested = bool(getattr(args, "members", None) or rules)
            selected_names = [m.name for m in members] if selection_requested else []
            if selection_requested and not members:
                if args.json:
                    print("[]")
                return 0

            # Native passthrough is an explicit escape hatch: when present,
            # execute the backend's listing command so backend-specific options
            # actually take effect.  Normalized JSON and native text output are
            # intentionally not mixed.
            if extra:
                if args.json:
                    raise UsageError("list backend passthrough cannot be combined with --json")
                cmd, meta = backend.command(
                    "list", archive, fmt=fmt, members=selected_names, extra=extra,
                    password=password, config=config, dry_run=args.dry_run,
                )
                meta["forward_output"] = True
                rc = run_backend(cmd, meta, None, {}, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
                if rc != 0:
                    _raise_backend_failure(rc, meta, password=password, operation="list")
                return 0

            if args.show_command or args.dry_run:
                cmd, meta = backend.command(
                    "list", archive, fmt=fmt, members=selected_names, extra=[],
                    password=password, config=config, dry_run=True,
                )
                run_backend(cmd, meta, None, {}, show_command=True, dry_run=True, verbose=args.verbose)
                if args.dry_run:
                    return 0

            if args.json:
                print(json.dumps([dataclasses.asdict(m) for m in members], ensure_ascii=False))
            else:
                table = Table(title=f"{archive_arg} [{fmt.canonical}]", show_header=True)
                table.add_column("Type")
                table.add_column("Size", justify="right")
                table.add_column("Path")
                for m in members:
                    table.add_row(m.kind, str(m.size), m.name)
                stdout_console.print(table)
            return 0

        members = [] if fmt.is_stream else backend.list_members(archive, password=password)
        selected_members = _select_archive_members(members, args)
        if rules and not selected_members:
            if args.json:
                print(json.dumps({"operation": "test", "archive": str(archive_arg), "format": fmt.canonical, "backend": backend.info.binary, "ok": True, "members": 0, "skipped": True}))
            elif not args.quiet:
                stdout_console.print(f"[green]OK[/] no archive members matched filters: {archive_arg}")
            return 0
        selected_names = [m.name for m in selected_members] if rules else []
        cmd, meta = backend.command(
            "test", archive, fmt=fmt, members=selected_names, extra=extra,
            password=password, config=config, dry_run=args.dry_run, no_fallback=args.no_fallback,
        )
        enabled = progress_enabled(args.progress, args.json, args.quiet)
        sizes = _member_sizes(selected_members)
        progress_total = 0 if meta.get("progress_indeterminate") else sum(sizes.values())
        progress_files = 0 if meta.get("progress_indeterminate") else len(sizes)
        with ProgressReporter("Testing", progress_total, progress_files, enabled) as rep:
            rc = run_backend(cmd, meta, rep, sizes, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
        if rc != 0:
            _raise_backend_failure(rc, meta, password=password, operation="test")
        if args.json:
            print(json.dumps({"operation": "test", "archive": str(archive_arg), "format": fmt.canonical, "backend": backend.info.binary, "ok": True, "members": len(selected_members)}))
        elif not args.quiet:
            stdout_console.print(f"[green]OK[/] archive passed backend test: {archive_arg}")
        return 0
    finally:
        if stdin_temp:
            stdin_temp.unlink(missing_ok=True)


def _remove(args, extra: list[str], config: dict) -> int:
    archive = _resolve_archive_arg(args)
    fmt = detect(archive, args.format)
    _warn_extension_mismatch(archive, fmt, args)
    password = _resolve_password(args)
    backend = resolve_backend(
        fmt, "remove", config, args.backend,
        required_capabilities=_backend_requirements(args, fmt, "remove", password),
        no_fallback=args.no_fallback,
    )
    members = args.members
    if not members:
        choices = [m.name for m in backend.list_members(archive, password=password)]
        members = choose_auto(choices, multi=True, prompt="members> ")
    if not members:
        raise UsageError("no members selected")
    cmd, meta = backend.command("remove", archive, fmt=fmt, members=members, extra=extra, password=password, config=config, no_fallback=args.no_fallback)
    with ProgressReporter("Removing", 0, len(members), progress_enabled(args.progress, args.json, args.quiet)) as rep:
        rc = run_backend(cmd, meta, rep, {m: 0 for m in members}, show_command=args.show_command, dry_run=args.dry_run, verbose=args.verbose)
    if rc != 0:
        _raise_backend_failure(rc, meta, password=password, operation="remove")
    if args.json:
        print(json.dumps({"operation": "remove", "archive": str(archive), "format": fmt.canonical, "backend": backend.info.binary, "members": members}))
    return 0


def _remote_identify_execution(args, config: dict) -> int | None:
    if getattr(args, "execution", "auto") != "remote":
        return None
    files = list(args.files)
    if not files:
        raise UsageError("--execution=remote identify requires explicit SSH archive paths")
    locations: list[RemoteLocation] = []
    for raw in files:
        location = parse_remote(str(raw), config, probe_rclone=False)
        if location is None or location.kind != "ssh":
            raise UnsupportedFormat("--execution=remote identify requires SSH paths")
        locations.append(location)
    root = locations[0]
    if any(not same_remote(root, other, config) for other in locations[1:]):
        raise UnsupportedFormat("--execution=remote identify requires all archives on the same SSH remote")
    _verify_remote_arc_compatibility(remote_capabilities(root, config), root)
    remote_argv = ["arc", "identify", *[item.path for item in locations]]
    if getattr(args, "format", None):
        remote_argv += ["--format", args.format]
    if getattr(args, "json", False):
        remote_argv.append("--json")
    return _run_remote_arc(root, config, remote_argv, dry_run=False, description="remote Arc identify")


def _identify(args, config: dict) -> int:
    remote_rc = _remote_identify_execution(args, config)
    if remote_rc is not None:
        return remote_rc
    files = list(args.files)
    if not files:
        files = yazi_choose(multi=True) if args.yazi else choose_auto(rg_files(), multi=True, prompt="archive> ")
    results = []
    for raw in files:
        remote = parse_remote(str(raw), config, probe_rclone=True)
        staged: Path | None = None
        try:
            if remote is not None:
                path, staged = stage_remote_for_read(remote, config, progress=progress_enabled(getattr(args, "progress", "auto"), getattr(args, "json", False), getattr(args, "quiet", False)))
                display = raw
                hint = infer_from_name(Path(remote.path))
            else:
                path = Path(raw).expanduser()
                display = str(path)
                hint = infer_from_name(path)
            fmt = detect(path, args.format)
            try:
                backend = resolve_backend(fmt, "list" if not fmt.is_stream else "test", config)
                backend_name = backend.info.binary
            except ArcError:
                backend_name = None
            results.append({"file": str(display), "detected": fmt.canonical, "extension": hint.canonical if hint else None, "backend": backend_name, "transport": remote.kind if remote else "local"})
        finally:
            if staged:
                staged.unlink(missing_ok=True)
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    else:
        t = Table(title="Archive identification")
        t.add_column("File")
        t.add_column("Detected")
        t.add_column("Extension")
        t.add_column("Backend")
        for r in results:
            ext = r["extension"] or "—"
            if r["extension"] and r["extension"] != r["detected"]:
                ext += " (mismatch)"
            t.add_row(r["file"], r["detected"], ext, r["backend"] or "missing")
        stdout_console.print(t)
    return 0


def _remote_name_location(value: str, config: dict) -> RemoteLocation:
    candidate = value if ":" in value or value.startswith(("ssh://", "rclone://")) else value + ":"
    location = parse_remote(candidate, config, probe_rclone=True)
    if location is None:
        known = ", ".join(configured_remote_names(config)) or "none"
        raise UsageError(f"unknown remote {value!r}; configured/discovered remotes: {known}")
    return location


def _remote_archive_arg(args, config: dict) -> RemoteLocation | None:
    raw = getattr(args, "archive", None)
    if not raw:
        return None
    return parse_remote(
        str(raw),
        config,
        probe_rclone=not bool(getattr(args, "dry_run", False)),
    )


def _clone_args(args, **changes):
    values = vars(args).copy()
    values.update(changes)
    return argparse.Namespace(**values)


def _clone_create_for_staging(args, archive: str | Path, **changes):
    """Clone create args for an Arc-owned staging path.

    Suffix shortcuts are a user-facing naming decision.  Once the final target
    is resolved, staging must keep the selected format without appending that
    user suffix to Arc's temporary filename.
    """
    explicit = _explicit_format(args)
    return _clone_args(
        args,
        archive=os.fspath(archive),
        format=explicit,
        format_shortcut=None,
        add_extension=False,
        **changes,
    )


def _parsed_arc_version(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    text = value.strip()
    if text.lower().startswith("arc "):
        text = text.split(None, 1)[1]
    pieces: list[int] = []
    for raw in text.split("."):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            break
        pieces.append(int(digits))
    return tuple(pieces) if pieces else None


def _verify_remote_arc_compatibility(capabilities: dict, location: RemoteLocation) -> None:
    if "arc" not in capabilities.get("tools", []):
        raise BackendUnavailable(
            f"remote {location.alias or location.name!r} does not have arc installed; use --execution=local"
        )
    remote_version = _parsed_arc_version(capabilities.get("arc_version"))
    local_version = _parsed_arc_version(__version__)
    if not remote_version or not local_version:
        return
    width = 2 if local_version[0] == 0 else 1
    if remote_version[:width] != local_version[:width]:
        raise UnsupportedFormat(
            "remote Arc version is not execution-compatible: "
            f"local={__version__}, remote={capabilities.get('arc_version')}"
        )


def _remote_filter_args(args) -> list[str]:
    rendered: list[str] = []
    for kind, value in getattr(args, "filter_rules", []) or []:
        if kind in {"include-from", "exclude-from"}:
            raise UnsupportedFormat(
                "--execution=remote does not transfer --include-from/--exclude-from files; "
                "use inline rules or --execution=local"
            )
        if kind not in {"include", "exclude"}:
            raise UnsupportedFormat(f"unsupported remote filter rule: {kind}")
        rendered += [f"--{kind}", value]
    return rendered


def _remote_common_native_args(args) -> list[str]:
    out: list[str] = []
    explicit_format = _explicit_format(args)
    if explicit_format:
        out += ["--format", explicit_format]
    if getattr(args, "backend", None):
        out += ["--backend", args.backend]
    if getattr(args, "no_fallback", False):
        out.append("--no-fallback")
    if getattr(args, "json", False):
        out.append("--json")
    if getattr(args, "quiet", False):
        out.append("--quiet")
    out += ["--progress", "never"]
    return out


def _remote_create_native_args(args) -> list[str]:
    out: list[str] = []
    if getattr(args, "level", None) is not None:
        out += ["--level", str(args.level)]
    if getattr(args, "threads", None) is not None:
        out += ["--threads", str(args.threads)]
    if getattr(args, "add_extension", False):
        out.append("--add-extension")
    if getattr(args, "follow_symlinks", False):
        out.append("--follow-symlinks")
    if getattr(args, "one_file_system", False):
        out.append("--one-file-system")
    if getattr(args, "preserve_owner", False):
        out.append("--preserve-owner")
    if getattr(args, "preserve_acls", False):
        out.append("--preserve-acls")
    if getattr(args, "preserve_xattrs", False):
        out.append("--preserve-xattrs")
    if getattr(args, "overwrite", False):
        out.append("--overwrite")
    out += _remote_filter_args(args)
    return out


def _remote_extract_native_args(args, config: dict, location: RemoteLocation) -> list[str]:
    out: list[str] = []
    if getattr(args, "stdout", False) and getattr(args, "json", False):
        raise UsageError("--stdout and --json cannot be used together")
    if getattr(args, "stdout", False):
        out.append("--stdout")
    else:
        output = str(getattr(args, "output", "."))
        target = parse_remote(output, config, probe_rclone=False)
        if target is None or target.kind != "ssh" or not same_remote(location, target, config):
            raise UnsupportedFormat(
                "--execution=remote extract requires --stdout or an SSH output on the same remote"
            )
        out += ["--output", target.path]
    for name in ("overwrite", "skip_existing", "rename_existing", "unsafe_paths", "preserve_owner", "preserve_acls", "preserve_xattrs"):
        if getattr(args, name, False):
            out.append("--" + name.replace("_", "-"))
    out += _remote_filter_args(args)
    return out


def _run_remote_arc(
    location: RemoteLocation,
    config: dict,
    remote_argv: list[str],
    *,
    dry_run: bool,
    description: str,
    show_command: bool = False,
) -> int:
    import shlex

    command = shlex.join(remote_argv)
    argv = [*ssh_command_prefix(location, config), command]
    record_stage("ssh-remote-exec", argv, description=description)
    if show_command or dry_run:
        console.print("[bold cyan]$[/] " + shlex.join(argv))
    if dry_run:
        return 0
    proc = subprocess.run(argv, check=False)
    return proc.returncode


def _remote_native_execution(args, extra: list[str], config: dict, location: RemoteLocation) -> int | None:
    if getattr(args, "execution", "auto") != "remote":
        return None
    if location.kind != "ssh":
        raise UnsupportedFormat("--execution=remote currently requires an SSH remote")
    if any(getattr(args, name, None) for name in ("password", "password_file", "password_env")):
        raise UnsupportedFormat(
            "--execution=remote does not forward archive credentials; use --execution=local staging"
        )

    operation = args.command
    if operation not in {"create", "add", "update", "remove", "extract", "list", "test"}:
        raise UnsupportedFormat(f"--execution=remote is not available for {operation}")
    dry_run = bool(getattr(args, "dry_run", False))

    if operation == "create":
        _fmt, location = _remote_create_target(args, location)

    remote_argv = ["arc", operation, location.path]
    remote_argv += _remote_common_native_args(args)

    if operation in {"create", "add", "update"}:
        raw_inputs = list(getattr(args, "inputs", []) or [])
        if not raw_inputs:
            raise UsageError("--execution=remote requires explicit SSH input paths")
        remote_inputs: list[str] = []
        for raw in raw_inputs:
            candidate = parse_remote(str(raw), config, probe_rclone=False)
            if candidate is None or candidate.kind != "ssh" or not same_remote(location, candidate, config):
                raise UnsupportedFormat(
                    "--execution=remote create/add/update requires every input to be on the same SSH remote as the archive"
                )
            remote_inputs.append(candidate.path)
        remote_argv += _remote_create_native_args(args)
        remote_argv += remote_inputs
    elif operation == "extract":
        remote_argv += _remote_extract_native_args(args, config, location)
        remote_argv += list(getattr(args, "members", []) or [])
    elif operation == "remove":
        members = list(getattr(args, "members", []) or [])
        if not members:
            raise UsageError("--execution=remote remove requires explicit member names")
        remote_argv += members
    else:
        remote_argv += _remote_filter_args(args)
        remote_argv += list(getattr(args, "members", []) or [])

    if extra:
        remote_argv += ["--", *extra]
    if not dry_run:
        _verify_remote_arc_compatibility(remote_capabilities(location, config), location)
    rc = _run_remote_arc(
        location,
        config,
        remote_argv,
        dry_run=dry_run,
        description=f"remote Arc {operation}",
        show_command=bool(getattr(args, "show_command", False)),
    )
    if rc == 0 and not dry_run:
        if operation in {"create", "add", "update", "remove"}:
            invalidate_remote_parent(location, config)
        elif operation == "extract" and not getattr(args, "stdout", False):
            output_location = parse_remote(str(args.output), config, probe_rclone=False)
            if output_location is not None:
                invalidate_remote_directory(output_location, output_location.path, config)
                invalidate_remote_parent(output_location, config)
    return rc


def _remote_create_target(args, location: RemoteLocation):
    fmt, resolved = _resolve_create_target(args, location.path)
    resolved_text = os.fspath(resolved)
    if resolved_text != location.path:
        location = dataclasses.replace(location, path=resolved_text, raw=location.render(resolved_text))
    return fmt, location


def _remote_create_streamable(fmt) -> bool:
    return fmt.container == "tar" or fmt.is_stream


def _stream_create_remote(args, extra: list[str], config: dict, location: RemoteLocation, fmt) -> int:
    if args.command != "create":
        raise UnsupportedFormat("streaming remote writes are only available for create")
    inputs = _resolve_create_inputs(args)
    dry_run = bool(getattr(args, "dry_run", False))
    remote_inputs = [
        (raw, parse_remote(str(raw), config, probe_rclone=not dry_run))
        for raw in inputs
    ]
    remote_inputs = [(raw, loc) for raw, loc in remote_inputs if loc is not None]
    transfer_progress = progress_enabled(args.progress, args.json, args.quiet)

    def execute(local_inputs: list[str]) -> int:
        password = _resolve_password(args)
        rules = expand_rule_files(getattr(args, "filter_rules", []) or [])
        manifest = build_manifest(
            local_inputs,
            rules,
            follow_symlinks=args.follow_symlinks,
            one_file_system=args.one_file_system,
        )
        if not manifest:
            raise UsageError("no input files remain after filtering")
        backend = resolve_backend(
            fmt,
            "create",
            config,
            args.backend,
            required_capabilities=_backend_requirements(args, fmt, "create", password),
            no_fallback=args.no_fallback,
        )
        _require_tar_metadata_backend(args, backend)
        cmd, meta = backend.command(
            "create",
            Path("-"),
            fmt=fmt,
            entries=manifest,
            extra=extra,
            level=args.level,
            threads=args.threads,
            follow_symlinks=args.follow_symlinks,
            config=config,
            password=password,
            preserve_owner=args.preserve_owner,
            preserve_acls=args.preserve_acls,
            preserve_xattrs=args.preserve_xattrs,
            dry_run=dry_run,
            no_fallback=args.no_fallback,
        )
        cleanup = [Path(x) for x in meta.get("cleanup", [])]
        try:
            stream_pipeline_to_remote(
                cmd,
                location,
                config,
                pipeline=[meta["pipeline"]] if meta.get("pipeline") else [],
                dry_run=dry_run,
                progress=transfer_progress,
                redact=[str(x) for x in meta.get("redact", []) if x],
                implementation_paths=cleanup,
                show_command=args.show_command,
            )
        finally:
            if not dry_run:
                for item in cleanup:
                    item.unlink(missing_ok=True)
        if args.json:
            print(json.dumps({
                "operation": "create",
                "archive": location.raw,
                "transport": location.kind,
                "format": fmt.canonical,
                "backend": backend.info.binary,
                "streamed": True,
                "files": sum(1 for entry in manifest if not entry.is_dir),
            }))
        elif not args.quiet:
            stdout_console.print(f"[green]OK[/] create: {location.raw} (streamed via {location.kind})")
        return 0

    if dry_run and remote_inputs:
        root = Path(tempfile.gettempdir()) / "arc-remote-input-dry-run"
        for _raw, remote_input in remote_inputs:
            stage_remote_input(remote_input, root, config, dry_run=True, progress=False)
        # Remote input contents cannot be scanned without network I/O. Record
        # the source transports, but do not pretend to know a backend manifest.
        return 0

    if not remote_inputs:
        return execute(inputs)
    with tempfile.TemporaryDirectory(prefix="arc-remote-inputs-") as td:
        root = Path(td)
        staged: dict[str, str] = {}
        for raw, remote_input in remote_inputs:
            staged[raw] = os.fspath(
                stage_remote_input(remote_input, root, config, progress=transfer_progress)
            )
        return execute([staged.get(raw, raw) for raw in inputs])


def _stream_remote_read(args, extra: list[str], config: dict, location: RemoteLocation, fmt) -> int:
    operation = args.command
    if operation not in {"extract", "test"}:
        raise UnsupportedFormat("remote streaming reads are available for stream extract/test")
    password = _resolve_password(args)
    backend = resolve_backend(
        fmt,
        operation,
        config,
        args.backend,
        required_capabilities=_backend_requirements(args, fmt, operation, password),
        no_fallback=args.no_fallback,
    )
    if password:
        raise UnsupportedFormat(f"{fmt.canonical} stream compression does not provide archive encryption")
    consumer = [backend.info.path, "-q", "-dc" if operation == "extract" else "-t", *extra, "-"]
    enabled = progress_enabled(args.progress, args.json, args.quiet) and not getattr(args, "stdout", False)

    if operation == "test":
        stream_remote_to_local(
            location,
            config,
            consumer,
            dry_run=args.dry_run,
            progress=enabled,
            show_command=args.show_command,
        )
        if args.json:
            print(json.dumps({"operation": "test", "archive": location.raw, "format": fmt.canonical, "backend": backend.info.binary, "ok": True, "streamed": True}))
        elif not args.quiet and not args.dry_run:
            stdout_console.print(f"[green]OK[/] remote stream passed backend test: {location.raw}")
        return 0

    if args.stdout:
        stream_remote_to_local(
            location,
            config,
            consumer,
            dry_run=args.dry_run,
            progress=False,
            show_command=args.show_command,
            forward_stdout=True,
        )
        return 0

    output = Path(args.output).expanduser()
    name = _stream_output_name(Path(location.path))
    target = output / name
    if target.exists() or target.is_symlink():
        if args.skip_existing:
            if args.json:
                print(json.dumps({"operation": "extract", "archive": location.raw, "format": fmt.canonical, "backend": backend.info.binary, "output": str(output), "members": 0, "bytes": 0, "skipped": True, "streamed": True}))
            return 0
        if not (args.overwrite or args.rename_existing):
            raise ConflictError(f"extraction target already exists: {target}; use --overwrite, --skip-existing, or --rename-existing")
    renamed: Path | None = None
    if args.rename_existing and not args.dry_run and (target.exists() or target.is_symlink()):
        index = 1
        while True:
            candidate = target.with_name(target.name + f".old.{index}")
            if not candidate.exists():
                target.rename(candidate)
                renamed = candidate
                break
            index += 1
    try:
        stream_remote_to_local(
            location,
            config,
            consumer,
            destination=target,
            dry_run=args.dry_run,
            progress=enabled,
            show_command=args.show_command,
        )
    except BaseException:
        if renamed is not None and not target.exists():
            renamed.rename(target)
        raise
    if args.json:
        print(json.dumps({"operation": "extract", "archive": location.raw, "format": fmt.canonical, "backend": backend.info.binary, "output": str(output), "streamed": True}))
    elif not args.quiet and not args.dry_run:
        stdout_console.print(f"[green]OK[/] extract: {location.raw} -> {target} (streamed)")
    return 0


def _dispatch_remote_archive(args, extra: list[str], config: dict) -> int | None:
    location = _remote_archive_arg(args, config)
    if location is None:
        return None
    remote_exec = _remote_native_execution(args, extra, config, location)
    if remote_exec is not None:
        return remote_exec

    operation = args.command
    dry_run = bool(getattr(args, "dry_run", False))
    explicit_format = _explicit_format(args)
    remote_hint = parse_format(explicit_format) if explicit_format else infer_from_name(Path(location.path))
    if remote_hint is not None and remote_hint.is_stream:
        if operation in {"extract", "test"}:
            return _stream_remote_read(args, extra, config, location, remote_hint)
        if operation == "list":
            raise UnsupportedFormat(f"{remote_hint.canonical} is a stream compression format and has no member list")
    transfer_progress = progress_enabled(
        getattr(args, "progress", "auto"),
        bool(getattr(args, "json", False)),
        bool(getattr(args, "quiet", False)),
    )

    if operation == "create":
        fmt, location = _remote_create_target(args, location)
        if not dry_run and not getattr(args, "overwrite", False) and remote_exists(location, config):
            raise ConflictError(f"remote archive already exists: {location.raw}; use --overwrite")
        if _remote_create_streamable(fmt):
            return _stream_create_remote(args, extra, config, location, fmt)

    if dry_run:
        # Dry-run must perform no network I/O and create no staging files. The
        # transport stage is still recorded so --show-native remains useful.
        if operation == "create":
            fake = Path(tempfile.gettempdir()) / (location.basename or "archive.arc")
            local_args = _clone_create_for_staging(args, fake, quiet=True, json=False)
            rc = _create_like(local_args, extra, config)
            if rc == 0:
                upload_remote(fake, location, config, dry_run=True)
            return rc
        download_remote(
            location,
            Path(tempfile.gettempdir()) / (location.basename or "archive.arc"),
            config,
            dry_run=True,
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="arc-remote-stage-") as td:
        stage = Path(td) / (location.basename or "archive.arc")
        if operation == "create":
            local_args = _clone_create_for_staging(args, stage, quiet=True, json=False)
            rc = _create_like(local_args, extra, config)
            if rc == 0:
                upload_remote(stage, location, config, **({"progress": True} if transfer_progress else {}))
        elif operation in {"add", "update", "remove"}:
            download_remote(location, stage, config, **({"progress": True} if transfer_progress else {}))
            local_args = _clone_args(args, archive=str(stage), quiet=True, json=False)
            if operation in {"add", "update"}:
                rc = _create_like(local_args, extra, config)
            else:
                rc = _remove(local_args, extra, config)
            if rc == 0:
                upload_remote(stage, location, config, **({"progress": True} if transfer_progress else {}))
        elif operation == "extract":
            download_remote(location, stage, config, **({"progress": True} if transfer_progress else {}))
            local_args = _clone_args(args, archive=str(stage))
            rc = _extract(local_args, extra, config)
        elif operation in {"list", "test"}:
            download_remote(location, stage, config, **({"progress": True} if transfer_progress else {}))
            local_args = _clone_args(args, archive=str(stage))
            rc = _list_or_test(local_args, extra, config)
        else:
            return None
    if rc == 0 and operation in {"create", "add", "update", "remove"}:
        invalidate_remote_parent(location, config)
        if getattr(args, "json", False):
            print(json.dumps({"operation": operation, "archive": location.raw, "transport": location.kind, "ok": True}))
        elif not getattr(args, "quiet", False):
            stdout_console.print(f"[green]OK[/] {operation}: {location.raw} (transport={location.kind})")
    return rc

def _completion_command(args, config: dict) -> int:
    if args.action == "zsh":
        print(zsh_completion(), end="")
        return 0
    if args.action == "cache":
        rows = completion_cache_rows(config)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False))
            return 0
        t = Table(title="Arc remote completion cache")
        for col in ("Provider", "Remote", "Location", "Age", "State", "Entries"):
            t.add_column(col)
        for row in rows:
            t.add_row(str(row["kind"]), str(row["remote"]), str(row["location"]), f"{row['age_seconds']}s", str(row["state"]), str(row["entries"]))
        stdout_console.print(t)
        return 0
    if args.action == "clear-cache":
        location = _remote_name_location(args.location, config) if args.location else None
        removed = clear_completion_cache(location)
        if args.json:
            print(json.dumps({"removed": removed}))
        else:
            stdout_console.print(f"[green]OK[/] removed {removed} completion cache entr{'y' if removed == 1 else 'ies'}")
        return 0
    if not args.location:
        raise UsageError("completion refresh requires a remote location")
    # Completion itself resolves the relevant parent directory and refreshes
    # only that directory's cache entry.
    candidates = complete_remote(args.location, config, refresh=True)
    if args.json:
        print(json.dumps({"location": args.location, "candidates": candidates}, ensure_ascii=False))
    else:
        stdout_console.print(f"[green]OK[/] refreshed {args.location}: {len(candidates)} candidate(s)")
    return 0


def _show_backends(config: dict, json_mode: bool = False, remote: str | None = None) -> int:
    if remote:
        location = _remote_name_location(remote, config)
        data = remote_capabilities(location, config)
        if json_mode:
            print(json.dumps(data, ensure_ascii=False))
        else:
            stdout_console.print_json(json.dumps(data, ensure_ascii=False))
        return 0
    rows = backend_inventory(config)
    if json_mode:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    t = Table(title="Archive backends")
    t.add_column("Role")
    t.add_column("Candidate")
    t.add_column("Status")
    t.add_column("Capabilities")
    for row in rows:
        for index, candidate in enumerate(row["candidates"]):
            t.add_row(
                row["role"] if index == 0 else "",
                candidate["binary"],
                candidate["path"] or "missing",
                " ".join(candidate["capabilities"]),
            )
    stdout_console.print(t)
    return 0


def _show_formats(json_mode: bool = False, remote: str | None = None, config: dict | None = None) -> int:
    remote_info = None
    if remote:
        remote_info = remote_capabilities(_remote_name_location(remote, config or {}), config or {})
    rows = []
    for name in FORMATS:
        stream = name in {"gzip", "bzip2", "xz", "zstd"}
        if stream:
            ops = ["create", "extract", "test"]
            kind = "stream"
        elif name.startswith("tar."):
            ops = ["create", "list", "extract", "test"]
            kind = "archive+compression"
        elif name == "tar":
            ops = ["create", "list", "extract", "test", "add", "update", "remove"]
            kind = "archive"
        else:
            ops = ["create", "list", "extract", "test", "add", "update", "remove"]
            kind = "archive"
        rows.append({"format": name, "kind": kind, "operations": ops})
    if json_mode:
        print(json.dumps({"remote": remote_info, "formats": rows} if remote_info else rows))
        return 0
    t = Table(title="Supported formats")
    t.add_column("Format")
    t.add_column("Kind")
    t.add_column("Normalized operations")
    for row in rows:
        t.add_row(row["format"], row["kind"], " ".join(row["operations"]))
    stdout_console.print(t)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"__complete", "__complete0", "__complete-mode"}:
        hidden = argv[0]
        words = argv[1:]
        if words and words[0] == "--":
            words = words[1:]
        if hidden == "__complete-mode":
            print(completion_mode(words))
        elif hidden == "__complete0":
            sys.stdout.buffer.write(encode_candidates_nul(completion_candidates(words)))
        else:
            print("\n".join(completion_candidates(words)))
        return 0
    wrapper_argv, extra = split_passthrough(argv)
    args = parser().parse_args(wrapper_argv)
    config = load_config()
    try:
        _apply_profile(args, config)
        _validate_yazi_context(args)
        _apply_defaults(args, config)
        if args.command == "create":
            _create_format_options(args)
        _show_invocation(argv, args)
        begin_plan(
            args.command,
            mode=getattr(args, "show_native", None),
            style=getattr(args, "native_style", None) or "reproducible",
        )
        rc: int
        if args.command == "identify":
            rc = _identify(args, config)
        elif args.command in {"create", "add", "update", "list", "test", "extract", "remove"}:
            remote_rc = _dispatch_remote_archive(args, extra, config)
            if remote_rc is not None:
                rc = remote_rc
            elif args.command in {"create", "add", "update"}:
                rc = _create_like(args, extra, config)
            elif args.command in {"list", "test"}:
                rc = _list_or_test(args, extra, config)
            elif args.command == "extract":
                rc = _extract(args, extra, config)
            else:
                rc = _remove(args, extra, config)
        elif args.command == "backends":
            rc = _show_backends(config, args.json, args.remote)
        elif args.command == "formats":
            rc = _show_formats(args.json, args.remote, config)
        elif args.command == "completion":
            rc = _completion_command(args, config)
        else:
            raise UsageError(f"unknown command: {args.command}")
        if rc == 0:
            emit_after(json_mode=bool(getattr(args, "json", False)))
        return rc
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted[/]")
        return 130
    except ArcError as exc:
        console.print(f"[bold red]error:[/] {exc}")
        return exc.exit_code
    except BrokenPipeError:
        return 141


if __name__ == "__main__":
    raise SystemExit(main())
