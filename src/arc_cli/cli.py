from __future__ import annotations

import argparse
import contextvars
import io
import dataclasses
import getpass
import json
import gzip
import tarfile
import zipfile
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path

from rich.filesize import decimal
from rich.table import Table
from rich.text import Text

from .batch import execute_batch, load_batch_input
from .backends import backend_capability_profile, backend_inventory, resolve_backend, run_backend, _compression_command, _decompression_command
from .capabilities import VerificationLevel
from .machine import MachineError, diagnostic as machine_diagnostic, dumps as machine_dumps, envelope as machine_envelope, load_schema, schema_names
from .completion import FORMATS, completion_candidates, completion_mode, encode_candidates_nul, zsh_completion
from .command_docs import COMMAND_DOCS, EXECUTABLE_ALIASES
from .doctor import alias_status_rows, collect_doctor_report, fix_and_recheck
from .manpages import available_topics, show_manpage
from .config import get_profile, load_config
from .errors import ArcError, BackendUnavailable, ConflictError, CorruptArchive, PasswordError, UnsafeArchive, UnsupportedFormat, UsageError
from .execution import begin_plan, emit_after, emit_command, mark_mutation, plan_dict, record_decision, record_stage
from .filtering import build_manifest, expand_rule_files, filter_members
from . import __version__
from .formats import CREATE_SUFFIX_SHORTCUTS, detect, extension_for, infer_from_name, parse_format, resolve_create_format, strip_archive_suffix
from .interactive import choose_auto, filesystem_candidates, rg_files, yazi_choose
from .model import FilterRule, Member
from .progress import ProgressReporter, SemanticProgress, console, progress_enabled, stdout_console
from .provenance import FINGERPRINT_NORMALIZATION, build_fingerprint, compare_fingerprints, fingerprint_summary, materialized_member_map, member_record, validate_logical_member_set
from .verification import VerificationEvidence, choose_level, verify_with_backend
from .remote import (
    RemoteLocation,
    clear_completion_cache,
    completion_cache_rows,
    complete_remote,
    configured_remote_names,
    download_remote,
    delete_remote,
    invalidate_remote_directory,
    invalidate_remote_parent,
    list_remote,
    parse_remote,
    remote_capabilities,
    remote_publication_guarantee,
    remote_exists,
    same_remote,
    ssh_command_prefix,
    stage_remote_for_read,
    stage_remote_input,
    stream_pipeline_to_remote,
    stream_remote_to_local,
    upload_remote,
)
from .mutation_policy import DestinationPolicy, backup_existing, decide_destination, policy_from_args, validate_backup_policy
from .safety import validate_members
from .transactions import (
    BatchManifest,
    TransactionJournal,
    batch_policy_key,
    current_transaction,
    file_fingerprint,
    list_transactions,
    same_fingerprint,
    transaction_scope,
    tx_cleanup,
    tx_cleanup_done,
    tx_event,
)


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


def _normalize_json_argv(argv: list[str]) -> list[str]:
    """Keep historical bare --json unambiguous beside positional arguments.

    argparse optional-value options otherwise consume the next positional token
    (for example ``arc explain --json convert ...``). Normalizing the bare
    spelling to an inline legacy value preserves the old CLI while still
    allowing the explicit ``--json=v1`` machine contract. Backend passthrough
    tokens after ``--`` are left untouched.
    """
    out: list[str] = []
    passthrough = False
    for token in argv:
        if token == "--":
            passthrough = True
            out.append(token)
            continue
        if not passthrough and token == "--json":
            out.append("--json=legacy")
        else:
            out.append(token)
    return out


def _add_json_option(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--json",
        nargs="?",
        const="legacy",
        choices=["legacy", "v1"],
        default=False,
        help="emit JSON; bare --json preserves the legacy shape, --json=v1 emits the stable machine envelope",
    )


def _json_v1(args) -> bool:
    return getattr(args, "json", False) == "v1"


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
    _add_json_option(p)
    p.add_argument("--progress", choices=["auto", "always", "never"], default=None)
    if create:
        p.add_argument("--destination-policy", choices=[p.value for p in DestinationPolicy], help="destination collision policy; legacy --overwrite/--force map to replace")
        p.add_argument("--backup-existing", nargs="?", const="auto", metavar="PATH", help="preserve an existing destination before replacement")
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
        p.add_argument("--destination-policy", choices=[p.value for p in DestinationPolicy], help="destination collision policy")
        p.add_argument("--unsafe-paths", action="store_true")
        p.add_argument("--stdout", action="store_true", help="write one extracted member to stdout")
        p.add_argument("--preserve-owner", action="store_true")
        p.add_argument("--preserve-acls", action="store_true")
        p.add_argument("--preserve-xattrs", action="store_true")


def _add_create_suffix_shortcuts(p: argparse.ArgumentParser, *, operation: str = "create") -> None:
    section = p.add_argument_group(
        f"{operation} suffix shortcuts",
        f"select the {operation} format explicitly; if the destination has no known archive suffix, append the selected suffix",
    )
    group = section.add_mutually_exclusive_group()
    group.add_argument("-F", "--format")
    for flag, fmt, suffix in CREATE_SUFFIX_SHORTCUTS:
        group.add_argument(
            flag,
            dest="format_shortcut",
            action="store_const",
            const=(fmt, suffix),
            help=f"select {fmt}; use {suffix} when the destination has no recognized archive suffix",
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arc", description="Safe, backend-aware archive and compression utility")
    p.add_argument("--version", action="version", version="arc 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("identify", help=COMMAND_DOCS["identify"].summary)
    q.add_argument("files", nargs="*")
    q.add_argument("-F", "--format")
    _add_json_option(q)
    q.add_argument("--yazi", nargs="?", const="archive", choices=["auto", "archive"])
    q.add_argument("--show-native", nargs="?", const="after", choices=["before", "after", "both"], default=None)
    q.add_argument("--native-style", choices=["exact", "reproducible"], default=None)
    q.add_argument("--execution", choices=["auto", "local", "remote"], default=None)

    for name in ["list", "test"]:
        q = sub.add_parser(name, help=COMMAND_DOCS[name].summary)
        q.add_argument("archive", nargs="?")
        if name == "list":
            q.add_argument("members", nargs="*")
        add_common(q, member_filter=True)
        if name == "test":
            q.add_argument("--verify-level", choices=[level.value for level in VerificationLevel], help="verification proof level; defaults to the strongest level the selected backend can prove")
            q.add_argument("--allow-verification-downgrade", action="store_true", help="accept the strongest weaker proof when the requested verification level is unavailable")

    q = sub.add_parser("extract", help=COMMAND_DOCS["extract"].summary)
    q.add_argument("archive", nargs="?")
    q.add_argument("members", nargs="*")
    add_common(q, extract=True, member_filter=True)

    for name in ["create", "add", "update"]:
        q = sub.add_parser(name, help=COMMAND_DOCS[name].summary)
        q.add_argument("archive")
        q.add_argument("inputs", nargs="*")
        add_common(q, create=True, format_option=name != "create")
        if name == "create":
            _add_create_suffix_shortcuts(q)
            q.add_argument("--overwrite", action="store_true")

    q = sub.add_parser("remove", help=COMMAND_DOCS["remove"].summary)
    q.add_argument("archive", nargs="?")
    q.add_argument("members", nargs="*")
    add_common(q)

    q = sub.add_parser("info", help=COMMAND_DOCS["info"].summary)
    q.add_argument("archives", nargs="+")
    q.add_argument("-F", "--format")
    q.add_argument("--backend")
    q.add_argument("--no-fallback", action="store_true")
    q.add_argument("--profile")
    q.add_argument("--password", nargs="?", const="__PROMPT__")
    q.add_argument("--password-file")
    q.add_argument("--password-env", metavar="NAME")
    q.add_argument("--members", action="store_true", help="include compact member statistics")
    q.add_argument("--fingerprint", action="store_true", help="compute a logical content fingerprint plus byte-level archive provenance")
    q.add_argument("--verify", action="store_true", help="run verification using the strongest level the selected backend can prove")
    q.add_argument("--verify-level", choices=[level.value for level in VerificationLevel], help="request an explicit verification proof level; implies --verify")
    q.add_argument("--allow-verification-downgrade", action="store_true", help="accept the strongest weaker proof when the requested verification level is unavailable")
    q.add_argument("--technical", action="store_true", help="include backend-oriented technical metadata")
    _add_json_option(q)
    q.add_argument("-q", "--quiet", action="store_true")
    q.add_argument("-v", "--verbose", action="count", default=0)
    q.add_argument("--progress", choices=["auto", "always", "never"], default=None)
    q.add_argument("--show-command", action="store_true", help="show native commands used for verification/metadata probes when available")
    q.add_argument("--show-native", nargs="?", const="after", choices=["before", "after", "both"], default=None)
    q.add_argument("--native-style", choices=["exact", "reproducible"], default=None)
    q.add_argument("--execution", choices=["auto", "local", "remote"], default=None)

    q = sub.add_parser("diff", help=COMMAND_DOCS["diff"].summary)
    q.add_argument("left", help="left/archive A")
    q.add_argument("right", help="right/archive B")
    q.add_argument("--backend", help="preferred backend for both inputs")
    q.add_argument("--no-fallback", action="store_true")
    q.add_argument("--password", nargs="?", const="__PROMPT__", help="password used for both archives unless a side-specific password is supplied")
    q.add_argument("--password-file")
    q.add_argument("--password-env", metavar="NAME")
    q.add_argument("--left-password", nargs="?", const="__PROMPT__")
    q.add_argument("--left-password-file")
    q.add_argument("--left-password-env", metavar="NAME")
    q.add_argument("--right-password", nargs="?", const="__PROMPT__")
    q.add_argument("--right-password-file")
    q.add_argument("--right-password-env", metavar="NAME")
    q.add_argument("-q", "--quiet", action="store_true")
    q.add_argument("-v", "--verbose", action="count", default=0)
    q.add_argument("--progress", choices=["auto", "always", "never"], default=None)
    q.add_argument("--show-command", action="store_true")
    q.add_argument("--show-native", nargs="?", const="after", choices=["before", "after", "both"], default=None)
    q.add_argument("--native-style", choices=["exact", "reproducible"], default=None)
    _add_json_option(q)

    q = sub.add_parser("convert", help=COMMAND_DOCS["convert"].summary)
    q.add_argument("paths", nargs="+", help="SOURCE [DESTINATION], or multiple existing sources with an explicit target format")
    add_common(q, create=True, format_option=False)
    _add_create_suffix_shortcuts(q, operation="convert")
    q.add_argument("-f", "--force", action="store_true", help="replace an existing destination")
    q.add_argument("--replace-source", action="store_true", help="remove each source only after destination verification")
    q.add_argument("--prove-equivalent", action="store_true", help="prove the converted result is logically equivalent to the source before publication")
    q.add_argument("--batch", action="store_true", help="treat every positional path as an independent source and derive each destination")
    q.add_argument("--resume", action="store_true", help="reuse completed batch items only when source and verified destination evidence still match")
    q.add_argument("--batch-id", help="override the deterministic resumable batch identity")
    q.add_argument("--verify-level", choices=[level.value for level in VerificationLevel], help="verification proof level; defaults to the strongest level the selected backend can prove")
    q.add_argument("--allow-verification-downgrade", action="store_true", help="accept the strongest weaker proof when the requested verification level is unavailable")
    q.add_argument("--source-password", nargs="?", const="__PROMPT__")
    q.add_argument("--source-password-file")
    q.add_argument("--source-password-env", metavar="NAME")

    q = sub.add_parser("explain", help=COMMAND_DOCS["explain"].summary)
    _add_json_option(q)
    q.add_argument("argv", nargs=argparse.REMAINDER, help="Arc command and arguments to plan without mutation")

    q = sub.add_parser("recover", help=COMMAND_DOCS["recover"].summary)
    q.add_argument("transaction_id", nargs="?")
    q.add_argument("--cleanup", action="store_true", help="remove only paths explicitly registered as transaction-owned temporary state")
    q.add_argument("--all", action="store_true", help="include completed transactions when listing")
    _add_json_option(q)

    q = sub.add_parser("batch", help=COMMAND_DOCS["batch"].summary)
    q.add_argument("input", nargs="?", default="-", help="batch JSON file, or - for stdin")
    q.add_argument("--validate-only", action="store_true", help="validate the batch input without executing operations")
    _add_json_option(q)

    q = sub.add_parser("backends", help=COMMAND_DOCS["backends"].summary)
    _add_json_option(q)
    q.add_argument("--verbose", action="store_true", help="show typed normalized capability profiles")
    q.add_argument("--remote")
    q.add_argument("--refresh", action="store_true", help="refresh remote capability evidence instead of using a fresh cache entry")
    q = sub.add_parser("formats", help=COMMAND_DOCS["formats"].summary)
    _add_json_option(q)
    q.add_argument("--remote")
    q = sub.add_parser("profiles", help=COMMAND_DOCS["profiles"].summary)
    _add_json_option(q)
    q = sub.add_parser("aliases", help=COMMAND_DOCS["aliases"].summary)
    _add_json_option(q)
    q.add_argument("--missing", action="store_true", help="show only aliases missing from PATH")
    q = sub.add_parser("doctor", help=COMMAND_DOCS["doctor"].summary)
    _add_json_option(q)
    q.add_argument("--fix", action="store_true", help="refresh generated surfaces and editable install, then re-check")
    q.add_argument("--source", type=Path, help="explicit arc-cli source checkout")
    q = sub.add_parser("schema", help=COMMAND_DOCS["schema"].summary)
    q.add_argument("name", nargs="?", choices=list(schema_names()))
    q.add_argument("--list", action="store_true", help="list bundled schema identities")

    q = sub.add_parser("man", help=COMMAND_DOCS["man"].summary)
    q.add_argument("topic", nargs="?", default="arc")
    q.add_argument("--list", action="store_true", dest="list_topics")
    q.add_argument("--plain", action="store_true", help="render bundled manual as plain text")
    q = sub.add_parser("help", help=COMMAND_DOCS["help"].summary)
    q.add_argument("topic", nargs="?", default="arc")
    q.add_argument("--plain", action="store_true", default=True, help=argparse.SUPPRESS)
    q.set_defaults(list_topics=False)
    c = sub.add_parser("completion", help=COMMAND_DOCS["completion"].summary)
    c.add_argument("action", choices=["zsh", "cache", "refresh", "clear-cache"])
    c.add_argument("location", nargs="?")
    _add_json_option(c)
    return p


def _create_format_options(args) -> tuple[str | None, bool, str | None]:
    shortcut = getattr(args, "format_shortcut", None)
    explicit = getattr(args, "format", None)
    if shortcut is not None and explicit:
        raise UsageError("use either -F/--format or a suffix shortcut, not both")
    if shortcut is not None:
        fmt, suffix = shortcut
        return fmt, True, suffix
    return explicit, bool(getattr(args, "add_extension", False)), None


def _resolve_create_target(args, path: str | Path) -> tuple[object, Path]:
    explicit, add_extension, suffix = _create_format_options(args)
    return resolve_create_format(path, explicit, add_extension, extension=suffix)


def _explicit_format(args) -> str | None:
    if getattr(args, "command", None) in {"create", "convert"}:
        explicit, _add, _suffix = _create_format_options(args)
        return explicit
    return getattr(args, "format", None)


def _display_invocation(argv: list[str], args) -> str:
    """Render the Arc argv we actually received, with credentials redacted.

    Shell syntax such as ``~`` expansion or the user's original quote style is
    gone before Arc starts, so this is intentionally the received argv rather
    than a claim that we can reconstruct the literal shell input.
    """
    words = [getattr(args, "_invoked_program", "arc"), *getattr(args, "_display_argv", argv)]
    secret_options = ("password", "source_password", "left_password", "right_password")
    for option in secret_options:
        secret = getattr(args, option, None)
        if secret and secret != "__PROMPT__":
            flag = "--" + option.replace("_", "-")
            words = [
                "***" if word == secret else word.replace(f"{flag}={secret}", f"{flag}=***")
                for word in words
            ]
    return shlex.join(words)


def _show_invocation(argv: list[str], args) -> None:
    if getattr(args, "quiet", False) or getattr(args, "json", False):
        return
    if getattr(args, "command", None) not in {"identify", "list", "extract", "create", "add", "update", "remove", "test", "info", "diff", "convert"}:
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
        "convert": {"inputs"},
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
        destination_policy = policy_from_args(args)
        validate_backup_policy(destination_policy, getattr(args, "backup_existing", None))
        if not stdout_archive and archive.exists() and destination_policy is DestinationPolicy.FAIL:
            raise ConflictError(f"archive already exists: {archive}; choose --destination-policy replace, rename, or skip-identical")
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
    record_decision("format", fmt.canonical, reason="resolved from explicit selector or archive destination")
    record_decision("backend", backend.info.binary, reason=f"compatible backend selected for {operation}/{fmt.canonical}")
    record_decision("publication", "local-atomic-replace" if operation == "create" and not stdout_archive else ("stdout" if stdout_archive else "backend-in-place"), reason="mutation boundary for this operation")
    record_decision("overwrite", bool(getattr(args, "overwrite", False)), reason="explicit destination collision policy")
    mark_mutation(not bool(args.dry_run))
    if not args.dry_run:
        tx_event("preflight", operation=operation, archive=os.fspath(final_archive if 'final_archive' in locals() else archive), format=fmt.canonical, backend=backend.info.binary)

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
    if temp_archive is not None and not args.dry_run:
        tx_cleanup(temp_archive, reason="unpublished create candidate")
        tx_event("staging", candidate=os.fspath(temp_archive), destination=os.fspath(final_archive))

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
        if not args.dry_run:
            tx_event("backend-complete", operation=operation, backend=backend.info.binary)
        if temp_archive and temp_archive.exists():
            if stdout_archive:
                with temp_archive.open("rb") as fh:
                    shutil.copyfileobj(fh, sys.stdout.buffer)
                tx_event("publish", destination="stdout", publication="stream-copy")
            else:
                destination_policy = policy_from_args(args)
                decision = decide_destination(final_archive, destination_policy, candidate=temp_archive)
                if decision.action == "skip-identical":
                    temp_archive.unlink(missing_ok=True)
                    tx_cleanup_done(temp_archive)
                    tx_event("publish", destination=os.fspath(final_archive), publication="skip-identical", destination_policy=destination_policy.value)
                else:
                    if decision.action == "rename" and decision.backup is not None:
                        os.replace(final_archive, decision.backup)
                        tx_event("destination-preserved", destination=os.fspath(final_archive), preserved_as=os.fspath(decision.backup), policy="rename")
                    elif decision.action == "replace":
                        backup = backup_existing(final_archive, getattr(args, "backup_existing", None))
                        if backup is not None:
                            tx_event("destination-preserved", destination=os.fspath(final_archive), preserved_as=os.fspath(backup), policy="backup-existing")
                    _atomic_replace(temp_archive, final_archive)
                    tx_cleanup_done(temp_archive)
                    tx_event("publish", destination=os.fspath(final_archive), publication="local-atomic-replace", destination_policy=destination_policy.value)
                compressed_bytes = final_archive.stat().st_size
        elif operation == "create" and not args.dry_run and not stdout_archive and final_archive.exists():
            compressed_bytes = final_archive.stat().st_size
    finally:
        if temp_archive and temp_archive.exists():
            temp_archive.unlink(missing_ok=True)
            tx_cleanup_done(temp_archive)
            tx_event("cleanup", path=os.fspath(temp_archive))

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
            required_verification=_explicit_verification_requirement(args) if args.command == "test" else None,
            allow_verification_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
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
            skipped_evidence = VerificationEvidence(
                _requested_verification_level(args, backend),
                VerificationLevel.NONE,
                "skipped",
                backend=backend.info.binary,
                reason="no archive members matched filters",
            )
            payload = {"operation": "test", "archive": str(archive_arg), "format": fmt.canonical, "backend": backend.info.binary, "ok": True, "members": 0, "skipped": True, "verification": skipped_evidence.to_dict()}
            if args.json:
                print(json.dumps(payload))
            elif not args.quiet:
                stdout_console.print(f"[green]OK[/] no archive members matched filters: {archive_arg}")
            return 0
        selected_names = [m.name for m in selected_members] if rules else []
        requested = _requested_verification_level(args, backend)

        if args.dry_run:
            cmd, meta = backend.command(
                "test", archive, fmt=fmt, members=selected_names, extra=extra,
                password=password, config=config, dry_run=True, no_fallback=args.no_fallback,
            )
            run_backend(cmd, meta, None, {}, show_command=True, dry_run=True, verbose=args.verbose)
            evidence = VerificationEvidence(requested, VerificationLevel.NONE, "skipped", backend=backend.info.binary, reason="dry-run")
        else:
            def run_full() -> tuple[bool, str | None]:
                cmd, meta = backend.command(
                    "test", archive, fmt=fmt, members=selected_names, extra=extra,
                    password=password, config=config, dry_run=False, no_fallback=args.no_fallback,
                )
                enabled = progress_enabled(args.progress, args.json, args.quiet)
                sizes = _member_sizes(selected_members)
                progress_total = 0 if meta.get("progress_indeterminate") else sum(sizes.values())
                progress_files = 0 if meta.get("progress_indeterminate") else len(sizes)
                with ProgressReporter("Testing", progress_total, progress_files, enabled) as rep:
                    rc = run_backend(cmd, meta, rep, sizes, show_command=args.show_command, dry_run=False, verbose=args.verbose)
                if rc == 0:
                    return True, None
                try:
                    _raise_backend_failure(rc, meta, password=password, operation="test")
                except ArcError as exc:
                    return False, str(exc)
                return False, f"backend status {rc}"

            evidence = verify_with_backend(
                path=archive,
                backend=backend,
                requested=requested,
                allow_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
                list_members=None if fmt.is_stream else (lambda: selected_members),
                run_full=run_full,
            )
        ok = evidence.status != "failed"
        payload = {
            "operation": "test",
            "archive": str(archive_arg),
            "format": fmt.canonical,
            "backend": backend.info.binary,
            "ok": ok,
            "members": len(selected_members),
            "verification": evidence.to_dict(),
        }
        if not ok:
            raise CorruptArchive(_verification_failure_detail(evidence) or "archive verification failed")
        if args.json:
            print(json.dumps(payload))
        elif not args.quiet:
            if evidence.status == "skipped":
                stdout_console.print(f"[yellow]SKIPPED[/] verification: {archive_arg} ({evidence.reason or 'no proof requested'})")
            else:
                label = evidence.achieved.value + (" (downgraded)" if evidence.downgraded else "")
                stdout_console.print(f"[green]OK[/] archive verification passed: {archive_arg} · {label}")
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
    record_decision("format", fmt.canonical, reason="detected archive format")
    record_decision("backend", backend.info.binary, reason=f"compatible backend selected for remove/{fmt.canonical}")
    record_decision("publication", "backend-in-place", reason="remove mutates the existing archive")
    mark_mutation(not bool(args.dry_run))
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
    if not args.dry_run:
        tx_event("backend-complete", operation="remove", archive=os.fspath(archive), backend=backend.info.binary, members=len(members))
        tx_event("publish", destination=os.fspath(archive), publication="backend-in-place")
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
    # Remote-native execution delegates archive work to another Arc process.
    # Forward command display so the remote Arc prints the archive backend it
    # actually launches; do not mislabel the local SSH delegation as that
    # backend command.  Explicit --show-command remains effective for non-TTY
    # callers, while ordinary automatic display remains interactive-only.
    if getattr(args, "show_command", False) or (
        console.is_terminal
        and not bool(getattr(args, "quiet", False))
        and not bool(getattr(args, "json", False))
    ):
        out.append("--show-command")
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
    stage = record_stage("ssh-remote-exec", argv, description=description)
    # The SSH command is a transport/delegation boundary, not the archive
    # backend.  Show it only when command display was explicitly requested or
    # during dry-run, and label it truthfully.  On a real run the delegated Arc
    # receives --show-command and emits its own native backend command.
    if show_command or dry_run:
        emit_command(stage, force=True, label="Remote")
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


def _resolve_source_password(args) -> str | None:
    names = ("source_password", "source_password_file", "source_password_env")
    if sum(getattr(args, name, None) is not None for name in names) > 1:
        raise UsageError("use only one of --source-password, --source-password-file, or --source-password-env")
    if getattr(args, "source_password_file", None):
        try:
            return Path(args.source_password_file).expanduser().read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError) as exc:
            raise UsageError(f"cannot read source password file: {exc}") from exc
    if getattr(args, "source_password_env", None):
        value = os.environ.get(args.source_password_env)
        if value is None:
            raise UsageError(f"source password environment variable is not set: {args.source_password_env}")
        return value
    value = getattr(args, "source_password", None)
    if value == "__PROMPT__":
        if not sys.stdin.isatty():
            raise UsageError("--source-password without a value requires an interactive TTY")
        return getpass.getpass("Source archive password: ")
    return value


def _stream_original_size(path: Path, fmt) -> int | None:
    # gzip stores the uncompressed size modulo 2**32 in the trailer. It is a
    # cheap, truthful hint for normal-size files; other stream formats do not
    # expose one uniformly without performing decompression.
    if fmt.canonical != "gzip":
        return None
    try:
        if path.stat().st_size < 4:
            return None
        with path.open("rb") as fh:
            fh.seek(-4, os.SEEK_END)
            return int.from_bytes(fh.read(4), "little")
    except OSError:
        return None


def _gzip_original_filename(path: Path) -> str | None:
    """Return gzip's optional original filename without decompressing data."""
    try:
        with path.open("rb") as fh:
            header = fh.read(10)
            if len(header) != 10 or header[:2] != b"\x1f\x8b":
                return None
            flags = header[3]
            if flags & 0x04:  # FEXTRA
                raw = fh.read(2)
                if len(raw) != 2:
                    return None
                fh.seek(int.from_bytes(raw, "little"), os.SEEK_CUR)
            if flags & 0x08:  # FNAME
                name = bytearray()
                while len(name) < 4096:
                    ch = fh.read(1)
                    if not ch or ch == b"\0":
                        break
                    name.extend(ch)
                return os.fsdecode(bytes(name)) if name else None
    except OSError:
        return None
    return None


def _record_info_probe(backend, path: Path, password: str | None, args, *, description: str) -> None:
    """Record/show one exact native info probe when the backend uses one."""
    name = Path(backend.info.path).name
    cmd: list[str] | None = None
    if name in {"7z", "7zz"}:
        cmd = [backend.info.path, "l", "-slt", f"-p{password}" if password else "-p-", "--", str(path)]
    elif name in {"rar", "unrar"}:
        cmd = [backend.info.path, "lt", f"-p{password}" if password else "-p-", "--", str(path)]
    if not cmd:
        return
    redact = [password] if password else []
    stage = record_stage("metadata-index", cmd, description=description, redact=redact)
    emit_command(stage, force=bool(getattr(args, "show_command", False)))


def _member_time_range(path: Path, fmt, members: list[Member] | None = None) -> tuple[str | None, str | None]:
    # Prefer the normalized member model so 7z/RAR listings participate just
    # like ZIP/TAR. Backends may expose fractional seconds; ISO-like native
    # values remain lexically ordered by time.
    values = sorted(member.mtime for member in (members or []) if member.mtime)
    if values:
        return values[0], values[-1]
    try:
        if fmt.container == "zip":
            with zipfile.ZipFile(path) as zf:
                stamps = [info.date_time for info in zf.infolist()]
            if not stamps:
                return None, None
            rendered = [f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}" for y, m, d, hh, mm, ss in stamps]
            return min(rendered), max(rendered)
        if fmt.container == "tar":
            mode = "r:*" if fmt.compression else "r:"
            with tarfile.open(path, mode) as tf:
                stamps = [member.mtime for member in tf.getmembers() if member.mtime is not None]
            if not stamps:
                return None, None
            import datetime as _dt
            rendered = [_dt.datetime.fromtimestamp(value).isoformat(sep=" ", timespec="seconds") for value in stamps]
            return min(rendered), max(rendered)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError):
        pass
    return None, None


def _technical_archive_metadata(path: Path, fmt, backend, password: str | None) -> dict:
    data: dict[str, object] = {"encrypted": None, "header_encrypted": None, "solid": None, "volumes": None}
    try:
        if fmt.container == "zip":
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                data["encrypted"] = any(bool(info.flag_bits & 0x1) for info in infos)
                data["header_encrypted"] = False
                data["zip64"] = any(info.file_size > 0xFFFFFFFF or info.compress_size > 0xFFFFFFFF for info in infos)
                methods = sorted({str(info.compress_type) for info in infos})
                if methods:
                    data["methods"] = methods
                if zf.comment:
                    data["comment"] = zf.comment.decode("utf-8", errors="replace")
            return data
        name = Path(backend.info.path).name
        if name in {"7z", "7zz"}:
            cmd = [backend.info.path, "l", "-slt", "-p-" if not password else f"-p{password}", "--", str(path)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            encrypted_values = [line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("Encrypted =")]
            if encrypted_values:
                data["encrypted"] = any(value == "+" for value in encrypted_values)
            solid = next((line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("Solid =")), None)
            if solid is not None:
                data["solid"] = solid == "+"
            method = next((line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("Method =")), None)
            if method:
                data["method"] = method
            blocks = next((line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("Blocks =")), None)
            if blocks and blocks.isdigit():
                data["blocks"] = int(blocks)
            volumes = next((line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith("Volumes =")), None)
            if volumes and volumes.isdigit():
                data["volumes"] = int(volumes)
            return data
        if name in {"rar", "unrar"}:
            cmd = [backend.info.path, "lt", f"-p{password}" if password else "-p-", "--", str(path)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            low = text.lower()
            if "encrypted" in low:
                data["encrypted"] = any(marker in low for marker in ("encrypted: +", "encrypted = +", "encrypted yes"))
            if "solid" in low:
                data["solid"] = any(marker in low for marker in ("solid: +", "solid = +", "solid yes"))
            volume_lines = [line for line in text.splitlines() if line.strip().lower().startswith(("volume:", "volumes:"))]
            if volume_lines:
                raw = volume_lines[0].split(":", 1)[1].strip()
                if raw.isdigit():
                    data["volumes"] = int(raw)
            return data
    except (OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        pass
    return data


def _conversion_targets(config: dict) -> list[str]:
    targets: list[str] = []
    for name in FORMATS:
        try:
            fmt = parse_format(name)
            resolve_backend(fmt, "create", config)
        except ArcError:
            continue
        targets.append(name)
    return targets


def _verification_failure_detail(evidence: VerificationEvidence) -> str | None:
    for check in reversed(evidence.checks):
        if check.status == "failed":
            return check.detail or check.name
    return evidence.reason if evidence.status == "failed" else None


def _coerce_verification_evidence(value, args=None) -> VerificationEvidence:
    if isinstance(value, VerificationEvidence):
        return value
    # Compatibility for tests/plugins written against the pre-R08 private
    # helper contract: (verified, backend, error).
    if isinstance(value, tuple) and len(value) == 3:
        verified, backend, error = value
        requested = VerificationLevel.parse(getattr(args, "verify_level", None) or "full")
        return VerificationEvidence(
            requested=requested,
            achieved=VerificationLevel.FULL if verified else VerificationLevel.NONE,
            status="passed" if verified else "failed",
            backend=backend,
            reason=error,
        )
    raise TypeError(f"unsupported verification evidence value: {type(value).__name__}")


def _explicit_verification_requirement(args) -> VerificationLevel | None:
    value = getattr(args, "verify_level", None)
    if not value or value == VerificationLevel.NONE.value:
        return None
    return VerificationLevel.parse(value)


def _requested_verification_level(args, backend) -> VerificationLevel:
    explicit = getattr(args, "verify_level", None)
    if explicit:
        return VerificationLevel.parse(explicit)
    profile = getattr(backend.info, "capability_profile", None)
    if profile is not None:
        return profile.maximum_verification
    caps = set(getattr(backend.info, "capabilities", set()) or set())
    if "test" in caps:
        return VerificationLevel.FULL
    if "list" in caps or "safe-index" in caps:
        return VerificationLevel.MEMBERS
    return VerificationLevel.NONE


def _verify_archive_path(path: Path, fmt, password: str | None, args, config: dict) -> VerificationEvidence:
    if getattr(args, "verify_level", None) == VerificationLevel.NONE.value:
        return VerificationEvidence(
            VerificationLevel.NONE,
            VerificationLevel.NONE,
            "skipped",
            backend=None,
            reason="verification explicitly disabled",
        )
    backend = resolve_backend(
        fmt,
        "test",
        config,
        getattr(args, "backend", None),
        required_capabilities=_backend_requirements(args, fmt, "test", password),
        required_verification=_explicit_verification_requirement(args),
        allow_verification_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
        no_fallback=bool(getattr(args, "no_fallback", False)),
    )
    requested = _requested_verification_level(args, backend)

    def list_members():
        return backend.list_members(path, password=password)

    def run_full() -> tuple[bool, str | None]:
        cmd, meta = backend.command(
            "test",
            path,
            fmt=fmt,
            members=[],
            extra=[],
            config=config,
            password=password,
            dry_run=False,
            no_fallback=bool(getattr(args, "no_fallback", False)),
        )
        rc = run_backend(
            cmd,
            meta,
            None,
            {},
            show_command=bool(getattr(args, "show_command", False)),
            dry_run=False,
            verbose=int(getattr(args, "verbose", 0) or 0),
        )
        if rc == 0:
            return True, None
        try:
            _raise_backend_failure(rc, meta, password=password, operation="test")
        except ArcError as exc:
            return False, str(exc)
        return False, f"backend status {rc}"

    evidence = verify_with_backend(
        path=path,
        backend=backend,
        requested=requested,
        allow_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
        list_members=None if fmt.is_stream else list_members,
        run_full=run_full,
    )
    return evidence


def _resolve_scoped_password(args, prefix: str, fallback: str | None = None) -> str | None:
    password = getattr(args, f"{prefix}_password", None)
    password_file = getattr(args, f"{prefix}_password_file", None)
    password_env = getattr(args, f"{prefix}_password_env", None)
    if password is None and password_file is None and password_env is None:
        return fallback
    scoped = argparse.Namespace(password=password, password_file=password_file, password_env=password_env)
    return _resolve_password(scoped)


def _fingerprint_archive_local(
    path: Path,
    display: str,
    args,
    config: dict,
    password: str | None,
    *,
    fmt=None,
    members: list[Member] | None = None,
) -> dict[str, object]:
    fmt = fmt or detect(path, None)
    extract_backend = resolve_backend(
        fmt,
        "extract",
        config,
        getattr(args, "backend", None),
        required_capabilities=_backend_requirements(args, fmt, "extract", password),
        no_fallback=bool(getattr(args, "no_fallback", False)),
    )
    if fmt.is_stream:
        indexed_members = [Member("@stream", 0, "file")]
    else:
        if members is None:
            list_backend = resolve_backend(
                fmt,
                "list",
                config,
                getattr(args, "backend", None),
                required_capabilities=_backend_requirements(args, fmt, "list", password),
                no_fallback=bool(getattr(args, "no_fallback", False)),
            )
            indexed_members = list_backend.list_members(path, password=password)
        else:
            indexed_members = list(members)
        validate_members(indexed_members)
        try:
            validate_logical_member_set(indexed_members)
        except ValueError as exc:
            raise UnsafeArchive(str(exc)) from exc

    with tempfile.TemporaryDirectory(prefix="arc-fingerprint-") as td:
        root = Path(td)
        extract_args = _clone_args(
            args,
            command="extract",
            archive=str(path),
            members=[],
            output=str(root),
            format=fmt.canonical,
            password=password,
            password_file=None,
            password_env=None,
            filter_rules=[],
            overwrite=True,
            skip_existing=False,
            rename_existing=False,
            unsafe_paths=False,
            stdout=False,
            preserve_owner=False,
            preserve_acls=False,
            preserve_xattrs=False,
            yazi=None,
            quiet=True,
            json=False,
            dry_run=False,
            progress="never",
        )
        _extract(extract_args, [], config)
        if fmt.is_stream:
            stream_path = root / _stream_output_name(path)
            records = [member_record(indexed_members[0], root, stream_path=stream_path)]
        else:
            materialized = materialized_member_map(root)
            records = [member_record(member, root, materialized=materialized) for member in indexed_members]
        return build_fingerprint(
            path,
            format_name=fmt.canonical,
            backend=extract_backend.info.binary,
            records=records,
            source_label=display,
            include_members=True,
        )


def _stage_archive_for_read(raw: str, args, config: dict) -> tuple[Path, str, Path | None, str]:
    remote = parse_remote(str(raw), config, probe_rclone=True)
    if remote is not None:
        path, cleanup = stage_remote_for_read(
            remote,
            config,
            progress=progress_enabled(getattr(args, "progress", "auto"), getattr(args, "json", False), getattr(args, "quiet", False)),
        )
        return path, remote.raw, cleanup, remote.kind
    path = Path(raw).expanduser()
    return path, str(path), None, "local"


def _print_diff_result(payload: dict[str, object]) -> None:
    equivalence = payload["equivalence"]
    changes = payload["changes"]
    counts = changes["counts"]
    left = payload["left"]
    right = payload["right"]
    t = Table(title="Arc archive diff", show_header=False, box=None, pad_edge=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    left_locality = " · staged locally" if left.get("staged") else ""
    right_locality = " · staged locally" if right.get("staged") else ""
    t.add_row("Left", f"{left['archive']['path']} [{left['archive']['format']}] · {left.get('transport', 'local')}{left_locality}")
    t.add_row("Right", f"{right['archive']['path']} [{right['archive']['format']}] · {right.get('transport', 'local')}{right_locality}")
    t.add_row("Logical", "equivalent" if equivalence["logical"] else "different")
    t.add_row("Metadata", "equivalent" if equivalence["metadata"] else "different")
    t.add_row("Bytes", "identical" if equivalence["byte_identical"] else "different")
    if equivalence["format_changed"]:
        t.add_row("Container", "different encoding/container")
    if equivalence["encoding_only"]:
        t.add_row("Interpretation", "same logical content; encoded bytes differ")
    t.add_row(
        "Changes",
        ", ".join(f"{name}={counts[name]}" for name in ("added", "removed", "type_changed", "content_changed", "metadata_changed")),
    )
    stdout_console.print(t)
    for title, key in (
        ("Added", "added"),
        ("Removed", "removed"),
        ("Type changed", "type_changed"),
        ("Content changed", "content_changed"),
        ("Metadata changed", "metadata_changed"),
    ):
        rows = changes[key]
        if not rows:
            continue
        stdout_console.print(f"[bold]{title}[/bold]")
        for row in rows:
            path = row.get("path") if isinstance(row, dict) else None
            stdout_console.print(f"  {path or row}")


def _diff_command(args, config: dict) -> int:
    record_decision(
        "comparison",
        "logical+metadata+archive-bytes",
        reason="R09A compares normalized member content separately from selected metadata and encoded archive bytes",
    )
    record_decision(
        "fingerprint_normalization",
        FINGERPRINT_NORMALIZATION,
        reason="both inputs use the same format-independent logical member normalization",
    )
    common_password = _resolve_password(args)
    left_password = _resolve_scoped_password(args, "left", common_password)
    right_password = _resolve_scoped_password(args, "right", common_password)
    left_path = right_path = None
    left_cleanup = right_cleanup = None
    try:
        left_path, left_display, left_cleanup, left_transport = _stage_archive_for_read(args.left, args, config)
        right_path, right_display, right_cleanup, right_transport = _stage_archive_for_read(args.right, args, config)
        left = _fingerprint_archive_local(left_path, left_display, args, config, left_password)
        right = _fingerprint_archive_local(right_path, right_display, args, config, right_password)
        left["transport"] = left_transport
        right["transport"] = right_transport
        payload = compare_fingerprints(left, right)
        # Transport is provenance context, not part of the logical digest.
        payload["left"]["transport"] = left_transport
        payload["right"]["transport"] = right_transport
        payload["left"]["staged"] = left_transport != "local"
        payload["right"]["staged"] = right_transport != "local"
        record_decision(
            "locality",
            "local-normalized-comparison",
            reason="remote inputs are read through existing staging; remote capability negotiation remains R09B",
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        elif not args.quiet:
            _print_diff_result(payload)
        return 0
    finally:
        if left_cleanup:
            left_cleanup.unlink(missing_ok=True)
        if right_cleanup:
            right_cleanup.unlink(missing_ok=True)


def _archive_info_local(path: Path, display: str, args, config: dict, password: str | None) -> tuple[dict, bool]:
    fmt = detect(path, getattr(args, "format", None))
    display_hint = display.split(":", 1)[-1] if ":" in display and not Path(display).exists() else display
    hint = infer_from_name(Path(display_hint))
    physical = path.stat().st_size
    members: list[Member] = []
    backend_name: str | None = None
    technical: dict[str, object] = {"encrypted": None, "header_encrypted": None, "solid": None, "volumes": None}
    unsafe = False
    metadata_limited_reason: str | None = None
    stream_filename: str | None = None
    oldest = newest = None
    original: int | None = None

    if fmt.is_stream:
        backend = resolve_backend(fmt, "test", config, getattr(args, "backend", None), no_fallback=bool(getattr(args, "no_fallback", False)))
        backend_name = backend.info.binary
        original = _stream_original_size(path, fmt)
        stream_filename = _gzip_original_filename(path) if fmt.canonical == "gzip" else None
    else:
        backend = resolve_backend(fmt, "list", config, getattr(args, "backend", None), no_fallback=bool(getattr(args, "no_fallback", False)))
        backend_name = backend.info.binary
        _record_info_probe(backend, path, password, args, description="archive technical metadata probe")
        technical = _technical_archive_metadata(path, fmt, backend, password)
        _record_info_probe(backend, path, password, args, description="archive member index probe")
        try:
            members = backend.list_members(path, password=password)
        except PasswordError:
            # Header-encrypted archives still have useful outer metadata. `info`
            # is an inspection command, so lack of a password limits the member
            # view instead of making the whole command fail. Verification, when
            # explicitly requested, still fails truthfully below.
            metadata_limited_reason = "password-required"
            technical["encrypted"] = True
            technical["header_encrypted"] = True
        else:
            try:
                validate_members(members)
            except UnsafeArchive:
                unsafe = True
            original = sum(member.size for member in members if member.kind == "file")
            oldest, newest = _member_time_range(path, fmt, members)

    ratio = (original / physical) if original is not None and physical > 0 else None
    reduction = ((1.0 - physical / original) * 100.0) if original not in {None, 0} else None
    counts = {kind: sum(1 for member in members if member.kind == kind) for kind in ("file", "dir", "symlink", "hardlink", "special", "unknown")}
    largest = max((member for member in members if member.kind == "file"), key=lambda member: member.size, default=None)
    verified: bool | None = None
    verify_error: str | None = None
    verify_backend: str | None = None
    verification: dict[str, object] | None = None
    failed = False
    if getattr(args, "verify", False) or getattr(args, "verify_level", None):
        evidence = _coerce_verification_evidence(_verify_archive_path(path, fmt, password, args, config), args)
        verification = evidence.to_dict()
        verify_backend = evidence.backend
        verify_error = _verification_failure_detail(evidence)
        verified = True if evidence.status == "passed" and evidence.achieved is not VerificationLevel.NONE else (False if evidence.status == "failed" else None)
        failed = evidence.status == "failed"

    fingerprint = None
    if getattr(args, "fingerprint", False):
        if metadata_limited_reason is not None:
            raise PasswordError("logical fingerprint requires readable archive member contents")
        fingerprint_full = _fingerprint_archive_local(path, display, args, config, password, fmt=fmt, members=members if not fmt.is_stream else None)
        fingerprint = fingerprint_summary(fingerprint_full)

    # A mismatch means content detection beat the filename hint. A matching
    # hint is still reported separately so callers do not confuse filename
    # agreement with proof of integrity.
    confidence = "explicit" if getattr(args, "format", None) else ("high" if hint is None or hint.canonical != fmt.canonical else "content+extension")
    result = {
        "path": display,
        "format": fmt.canonical,
        "container": fmt.container,
        "compression": fmt.compression,
        "physical_bytes": physical,
        "compressed_bytes": physical,
        "original_bytes": original,
        "reduction_percent": reduction,
        "ratio": ratio,
        "stream_filename": stream_filename,
        "extension_format": hint.canonical if hint else None,
        "format_mismatch": bool(hint and hint.canonical != fmt.canonical),
        "format_confidence": confidence,
        "backend": backend_name,
        "members": {
            "total": len(members) if not fmt.is_stream and metadata_limited_reason is None else None,
            "files": counts["file"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "directories": counts["dir"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "symlinks": counts["symlink"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "hardlinks": counts["hardlink"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "special": counts["special"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "unknown": counts["unknown"] if not fmt.is_stream and metadata_limited_reason is None else None,
            "largest": {"name": largest.name, "bytes": largest.size} if largest else None,
        },
        "metadata_limited_reason": metadata_limited_reason,
        "oldest": oldest,
        "newest": newest,
        "encrypted": technical.get("encrypted"),
        "header_encrypted": technical.get("header_encrypted"),
        "solid": technical.get("solid"),
        "volumes": technical.get("volumes"),
        "comment": technical.get("comment"),
        "safe_paths": None if fmt.is_stream or metadata_limited_reason else not unsafe,
        "verified": verified,
        "verify_backend": verify_backend,
        "verify_error": verify_error,
        "verification": verification,
        "fingerprint": fingerprint,
        "technical": technical if getattr(args, "technical", False) else None,
        "convert_targets": _conversion_targets(config),
    }
    return result, failed


def _print_info_result(result: dict, *, members: bool = False, technical: bool = False) -> None:
    title = "Compressed stream" if result["container"] is None else "Archive"
    t = Table(title=f"{title}: {result['path']}", show_header=False, box=None, pad_edge=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    t.add_row("Format", str(result["format"]))
    t.add_row("Confidence", str(result.get("format_confidence") or "unknown"))
    t.add_row("Size", decimal(int(result["physical_bytes"])))
    if result["original_bytes"] is not None:
        t.add_row("Original", decimal(int(result["original_bytes"])))
    if result["reduction_percent"] is not None:
        value = float(result["reduction_percent"])
        label = "Reduction" if value >= 0 else "Change"
        shown = f"{value:.1f}%" if value >= 0 else f"+{-value:.1f}%"
        t.add_row(label, shown)
    if result["ratio"] is not None:
        t.add_row("Ratio", f"{float(result['ratio']):.2f}:1")
    if result.get("stream_filename"):
        t.add_row("Filename", str(result["stream_filename"]))
    member_data = result["members"]
    if member_data["total"] is not None:
        t.add_row("Members", f"{member_data['total']} ({member_data['files']} files, {member_data['directories']} directories)")
        if members:
            if member_data["largest"]:
                largest = member_data["largest"]
                t.add_row("Largest", f"{largest['name']} — {decimal(int(largest['bytes']))}")
            kinds = []
            for key in ("symlinks", "hardlinks", "special", "unknown"):
                if member_data[key]:
                    kinds.append(f"{key}={member_data[key]}")
            if kinds:
                t.add_row("Other kinds", ", ".join(kinds))
    if result.get("metadata_limited_reason"):
        t.add_row("Contents", f"unavailable — {result['metadata_limited_reason']}")
    if result["encrypted"] is not None:
        t.add_row("Encrypted", "yes" if result["encrypted"] else "no")
    if result.get("header_encrypted") is not None:
        t.add_row("Headers encrypted", "yes" if result["header_encrypted"] else "no")
    if result.get("comment"):
        t.add_row("Comment", str(result["comment"]))
    if result["solid"] is not None:
        t.add_row("Solid", "yes" if result["solid"] else "no")
    if result.get("volumes") is not None:
        t.add_row("Volumes", str(result["volumes"]))
    if result.get("staged"):
        t.add_row("Transport", f"{result.get('transport') or 'remote'} (staged locally for inspection)")
    if result["oldest"]:
        t.add_row("Oldest", str(result["oldest"]))
    if result["newest"]:
        t.add_row("Newest", str(result["newest"]))
    if result["format_mismatch"]:
        t.add_row("Extension", f"{result['extension_format']} (mismatch with content)")
    elif result["extension_format"]:
        t.add_row("Extension", str(result["extension_format"]))
    if result["safe_paths"] is False:
        t.add_row("Safety", "unsafe member paths detected")
    t.add_row("Backend", str(result["backend"] or "unavailable"))
    if result["verified"] is True:
        evidence = result.get("verification") or {}
        achieved = evidence.get("achieved")
        suffix = f" · {achieved}" if achieved else ""
        t.add_row("Integrity", f"verified ({result['verify_backend']}){suffix}")
    elif result["verified"] is False:
        t.add_row("Integrity", f"FAILED — {result['verify_error']}")
    elif result.get("verification") and result["verification"].get("status") == "skipped":
        t.add_row("Integrity", "not verified (verification level none)")
    else:
        t.add_row("Integrity", "not checked")
    if result.get("fingerprint"):
        fp = result["fingerprint"]
        t.add_row("Logical fingerprint", str(fp.get("digest")))
        t.add_row("Metadata fingerprint", str(fp.get("metadata_digest")))
        archive_fp = fp.get("archive") or {}
        if archive_fp.get("byte_sha256"):
            t.add_row("Archive SHA-256", str(archive_fp.get("byte_sha256")))
    targets = result.get("convert_targets") or []
    if targets:
        t.add_row("Convert targets", " ".join(targets))
    if technical and result.get("technical"):
        for key, value in result["technical"].items():
            if value is not None and key not in {"encrypted", "header_encrypted", "solid"}:
                t.add_row(f"Technical/{key}", str(value))
    stdout_console.print(t)


def _info(args, config: dict) -> int:
    password = _resolve_password(args)
    results: list[dict] = []
    failed = False
    for raw in args.archives:
        remote = parse_remote(str(raw), config, probe_rclone=True)
        cleanup: Path | None = None
        if remote is not None:
            path, cleanup = stage_remote_for_read(
                remote,
                config,
                progress=progress_enabled(getattr(args, "progress", "auto"), getattr(args, "json", False), getattr(args, "quiet", False)),
            )
            display = remote.raw
        else:
            path = Path(raw).expanduser()
            display = str(path)
        try:
            result, item_failed = _archive_info_local(path, display, args, config, password)
            if remote is not None:
                result["transport"] = remote.kind
                result["staged"] = True
            else:
                result["transport"] = "local"
                result["staged"] = False
            results.append(result)
            failed = failed or item_failed
        finally:
            if cleanup:
                cleanup.unlink(missing_ok=True)
    if args.json:
        payload: object = results[0] if len(results) == 1 else results
        print(json.dumps(payload, ensure_ascii=False))
    elif not args.quiet:
        for result in results:
            _print_info_result(result, members=args.members, technical=args.technical)
    return 1 if failed else 0


def _suffix_for_convert(args, fmt) -> str:
    shortcut = getattr(args, "format_shortcut", None)
    if shortcut is not None:
        return shortcut[1]
    return extension_for(fmt)


def _render_default_destination(source: str, fmt, args, config: dict) -> str:
    suffix = _suffix_for_convert(args, fmt)
    remote = parse_remote(source, config, probe_rclone=not bool(getattr(args, "dry_run", False)))
    if remote is not None:
        base = strip_archive_suffix(remote.basename)
        new_name = base + suffix
        parent = remote.parent
        new_path = f"{parent.rstrip('/')}/{new_name}" if parent else new_name
        return remote.render(new_path)
    path = Path(source).expanduser()
    return str(path.with_name(strip_archive_suffix(path.name) + suffix))


def _convert_destination(source: str, destination: str | None, args, config: dict) -> tuple[object, str]:
    explicit, add_extension, selected_suffix = _create_format_options(args)
    if explicit:
        fmt = parse_format(explicit)
        if destination is None:
            destination = _render_default_destination(source, fmt, args, config)
        else:
            remote = parse_remote(destination, config, probe_rclone=not bool(getattr(args, "dry_run", False)))
            name_for_hint = remote.path if remote else destination
            # Match create semantics: a short selector carries its own suffix and
            # appends it to an extensionless destination.  Plain -F/--format is
            # authoritative for the encoding but only rewrites an extensionless
            # path when --add-extension is explicit.  A conflicting recognized
            # suffix is always preserved as the caller-supplied filename.
            if infer_from_name(name_for_hint) is None and (selected_suffix is not None or add_extension):
                destination += selected_suffix or extension_for(fmt)
        return fmt, destination
    if destination is None:
        raise UsageError("convert without DESTINATION requires -F/--format or a format selector such as -tzst")
    remote = parse_remote(destination, config, probe_rclone=not bool(getattr(args, "dry_run", False)))
    hint_path = remote.path if remote else destination
    fmt = infer_from_name(hint_path)
    if not fmt:
        raise UnsupportedFormat(f"cannot infer conversion format from {destination!r}; use -F/--format or a format selector")
    return fmt, destination


def _looks_existing_source(raw: str, config: dict, *, dry_run: bool = False) -> bool:
    remote = parse_remote(raw, config, probe_rclone=not dry_run)
    if remote is not None:
        if dry_run:
            return infer_from_name(remote.path) is not None
        try:
            return remote_exists(remote, config)
        except ArcError:
            return False
    return Path(raw).expanduser().exists()


def _resolve_convert_jobs(args, config: dict) -> list[dict]:
    paths = list(args.paths)
    explicit = _explicit_format(args)
    batch = bool(getattr(args, "batch", False))
    if len(paths) == 1:
        sources, destination = paths, None
    elif len(paths) == 2 and not batch:
        # SOURCE DESTINATION remains the default. A shell-expanded two-source
        # batch is recognized only when the second operand does not look like
        # the explicitly selected target representation. For the truly
        # ambiguous same-format case, --batch makes intent explicit.
        second_hint = infer_from_name(paths[1])
        explicit_fmt = parse_format(explicit) if explicit else None
        looks_like_destination = bool(explicit_fmt and second_hint and second_hint.canonical == explicit_fmt.canonical)
        if (
            explicit
            and not looks_like_destination
            and all(_looks_existing_source(value, config, dry_run=bool(args.dry_run)) for value in paths)
        ):
            sources, destination = paths, None
        else:
            sources, destination = [paths[0]], paths[1]
    else:
        if not explicit:
            raise UsageError("batch conversion requires an explicit target format")
        sources, destination = paths, None
    if batch and not explicit:
        raise UsageError("--batch requires an explicit target format")
    jobs: list[dict] = []
    for source in sources:
        fmt, dest = _convert_destination(source, destination, args, config)
        jobs.append({"source": source, "destination": dest, "target_format": fmt})
    identities: set[str] = set()
    destinations: set[str] = set()
    for job in jobs:
        src_remote = parse_remote(job["source"], config, probe_rclone=not bool(args.dry_run))
        dst_remote = parse_remote(job["destination"], config, probe_rclone=not bool(args.dry_run))
        src_id = src_remote.raw if src_remote else os.fspath(Path(job["source"]).expanduser().resolve())
        dst_id = dst_remote.raw if dst_remote else os.fspath(Path(job["destination"]).expanduser().resolve())
        if src_id == dst_id:
            raise ConflictError(f"conversion source and destination are the same: {job['source']}")
        if dst_id in destinations:
            raise ConflictError(f"multiple conversions resolve to the same destination: {job['destination']}")
        identities.add(src_id)
        destinations.add(dst_id)
    for job in jobs:
        destination_policy = policy_from_args(args)
        validate_backup_policy(destination_policy, getattr(args, "backup_existing", None))
        if args.dry_run or getattr(args, "resume", False):
            continue
        dst_remote = parse_remote(job["destination"], config, probe_rclone=True)
        exists = remote_exists(dst_remote, config) if dst_remote else Path(job["destination"]).expanduser().exists()
        if exists and destination_policy is DestinationPolicy.FAIL:
            raise ConflictError(f"conversion destination already exists: {job['destination']}; choose --destination-policy replace, rename, or skip-identical")
        if dst_remote and exists and destination_policy in {DestinationPolicy.RENAME, DestinationPolicy.SKIP_IDENTICAL}:
            raise UsageError(f"--destination-policy {destination_policy.value} requires local destination evidence for convert")
    return jobs


def _convert_backend_summary(source_fmt, target_fmt, args, config: dict) -> str:
    source_op = "extract" if source_fmt.is_stream else "list"
    source_backend = resolve_backend(source_fmt, source_op, config, args.backend, no_fallback=args.no_fallback).info.binary
    target_backend = resolve_backend(target_fmt, "create", config, args.backend, no_fallback=args.no_fallback).info.binary
    try:
        if source_fmt.compression:
            decompressor = Path(_decompression_command(source_fmt.compression, config, Path("<source>"), no_fallback=args.no_fallback)[0]).name
            if decompressor != source_backend:
                source_backend = f"{source_backend}+{decompressor}"
    except ArcError:
        pass
    try:
        if target_fmt.compression:
            compressor = Path(_compression_command(target_fmt.compression, config, args.level, args.threads, no_fallback=args.no_fallback)[0]).name
            if compressor != target_backend:
                target_backend = f"{target_backend}+{compressor}"
    except ArcError:
        pass
    return f"{source_backend} -> {target_backend}"


def _verification_plan(target_fmt, args, config: dict) -> dict[str, object]:
    explicit = getattr(args, "verify_level", None)
    if explicit == VerificationLevel.NONE.value:
        return {"requested": "none", "selected": "none", "backend": None, "downgraded": False, "reason": "verification explicitly disabled"}
    backend = resolve_backend(
        target_fmt,
        "test",
        config,
        getattr(args, "backend", None),
        required_verification=_explicit_verification_requirement(args),
        allow_verification_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
        no_fallback=bool(getattr(args, "no_fallback", False)),
    )
    profile = backend.info.capability_profile or backend_capability_profile(backend.info.binary)
    requested = VerificationLevel.parse(explicit) if explicit else profile.maximum_verification
    selected, downgraded, reason = choose_level(
        requested,
        set(profile.verification_levels),
        allow_downgrade=bool(getattr(args, "allow_verification_downgrade", False)),
    )
    return {
        "requested": requested.value,
        "selected": selected.value,
        "backend": backend.info.binary,
        "downgraded": downgraded,
        "reason": reason,
    }


def _show_convert_plan(source: str, destination: str, source_fmt, target_fmt, strategy: str, args, config: dict) -> None:
    destination_remote = parse_remote(
        destination, config, probe_rclone=not bool(getattr(args, "dry_run", False))
    )
    verification_plan = _verification_plan(target_fmt, args, config)
    verification_enabled = verification_plan["selected"] != VerificationLevel.NONE.value
    equivalence_enabled = bool(getattr(args, "prove_equivalent", False))
    publication_guarantee = (
        {
            "strategy": "same-parent-temporary-file+os.replace",
            "scope": "local-filesystem",
            "atomicity": "same-filesystem-replace",
            "guaranteed_atomic": True,
            "replace_semantics": "replace",
        }
        if destination_remote is None
        else remote_publication_guarantee(destination_remote, config, allow_probe=False)
    )
    if destination_remote is None:
        publication = "local-same-filesystem-atomic-replace"
    elif verification_enabled and equivalence_enabled:
        publication = "transport-dependent-remote-publish-with-verification-and-equivalence-reread"
    elif verification_enabled:
        publication = "transport-dependent-remote-publish-with-verification-reread"
    elif equivalence_enabled:
        publication = "transport-dependent-remote-publish-with-equivalence-reread"
    else:
        publication = "transport-dependent-remote-publish-unverified"
    record_decision("source_format", source_fmt.canonical, reason="detected from source bytes/name during planning")
    record_decision("destination_format", target_fmt.canonical, reason="explicit selector or destination suffix")
    record_decision("backend_chain", _convert_backend_summary(source_fmt, target_fmt, args, config), reason="compatible source/target backend resolution")
    record_decision("strategy", strategy, reason="selected from source/target representation and filters")
    record_decision("publication", publication, reason="locality determines publication sequence")
    record_decision(
        "publication_guarantee",
        publication_guarantee,
        reason="typed R09B transport evidence; cache is consumed without adding a dry-run/network preflight",
    )
    record_decision("verification", "verify-unpublished-candidate-then-published-remote-reread" if destination_remote and verification_enabled else ("verification-disabled" if not verification_enabled else "verify-unpublished-candidate-before-publish"), reason="publication verification sequence")
    record_decision("verification_policy", verification_plan, reason="typed verification policy negotiated against the selected test backend")
    record_decision(
        "logical_equivalence",
        "prove-source-vs-destination" if getattr(args, "prove_equivalent", False) else "not-requested",
        reason="--prove-equivalent uses the R09A logical fingerprint/diff authority and blocks local publication on mismatch",
    )
    record_decision("source_removal", "after-verified-publish" if args.replace_source else "keep", reason="--replace-source policy")
    mark_mutation(not bool(getattr(args, "dry_run", False)))
    if args.quiet or args.json:
        return
    t = Table(title="Convert", show_header=False, box=None, pad_edge=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    t.add_row("Source", source)
    t.add_row("Detected", source_fmt.canonical)
    t.add_row("Destination", destination)
    t.add_row("Format", target_fmt.canonical + (" (explicit)" if _explicit_format(args) else ""))
    t.add_row("Backend", _convert_backend_summary(source_fmt, target_fmt, args, config))
    t.add_row("Strategy", strategy)
    guarantee_label = str(publication_guarantee.get("atomicity", "unproven"))
    if destination_remote is None:
        publication_label = guarantee_label + "; local same-filesystem publish"
    elif verification_enabled and equivalence_enabled:
        publication_label = "transport-dependent; remote re-read verified and logical equivalence proven; atomicity=" + guarantee_label
    elif verification_enabled:
        publication_label = "transport-dependent; remote re-read verified; atomicity=" + guarantee_label
    elif equivalence_enabled:
        publication_label = "transport-dependent; remote re-read logical equivalence proven; atomicity=" + guarantee_label
    else:
        publication_label = "transport-dependent; verification disabled; atomicity=" + guarantee_label
    t.add_row("Publication", publication_label)
    verify_label = str(verification_plan["selected"])
    if verification_plan.get("downgraded"):
        verify_label += " (downgraded)"
    t.add_row("Verification", verify_label)
    t.add_row("Source kept", "no, after verified publish" if args.replace_source else "yes")
    stdout_console.print(t)


def _atomic_pipeline_convert(source: Path, destination: Path, source_fmt, target_fmt, args, config: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destination.name}.arc-convert-", suffix=extension_for(target_fmt), dir=destination.parent)
    os.close(fd)
    temp = Path(tmp_name)
    temp.unlink(missing_ok=True)
    tx_cleanup(temp, reason="conversion pipeline temporary output")
    tx_event("staging", candidate=os.fspath(temp), destination=os.fspath(destination))
    try:
        if source_fmt.container == "tar" and source_fmt.compression:
            producer = _decompression_command(source_fmt.compression, config, source, no_fallback=args.no_fallback)
        elif source_fmt.is_stream:
            producer = _decompression_command(source_fmt.compression, config, source, no_fallback=args.no_fallback)
        else:
            producer = [shutil.which("cat") or "cat", "--", str(source)]
        if target_fmt.container == "tar" and target_fmt.compression:
            consumer = _compression_command(target_fmt.compression, config, args.level, args.threads, no_fallback=args.no_fallback)
        elif target_fmt.is_stream:
            consumer = _compression_command(target_fmt.compression, config, args.level, args.threads, no_fallback=args.no_fallback)
        else:
            consumer = None
        meta: dict = {"stdout_file": temp, "cleanup": [], "progress_indeterminate": True}
        if consumer:
            meta["pipeline"] = consumer
        with ProgressReporter("Converting", 0, 0, progress_enabled(args.progress, args.json, args.quiet)) as rep:
            rc = run_backend(producer, meta, rep, {}, show_command=args.show_command, dry_run=False, verbose=args.verbose)
        if rc != 0:
            _raise_backend_failure(rc, meta, operation="convert")
        _atomic_replace(temp, destination)
        tx_cleanup_done(temp)
        tx_event("pipeline-publish", destination=os.fspath(destination), publication="local-atomic-replace")
    finally:
        temp.unlink(missing_ok=True)
        tx_cleanup_done(temp)



def _convert_selected_members(members: list[Member], args) -> list[Member]:
    rules = _filter_rules(args)
    if not rules:
        return list(members)
    # For conversion, an include-only expression is naturally a selection
    # request (especially when narrowing a container to one stream member).
    # Preserve Arc's ordered last-match-wins matcher while seeding the
    # conversion selection as excluded-by-default.
    if any(rule.action == "include" for rule in rules) and not any(rule.action == "exclude" for rule in rules):
        rules = [FilterRule("exclude", "*"), *rules]
    return filter_members(members, rules)


def _convert_stage_tree(source: Path, destination: Path, source_fmt, target_fmt, source_password: str | None, destination_password: str | None, args, config: dict) -> int:
    list_backend = None
    selected: list[Member] = []
    if not source_fmt.is_stream:
        list_backend = resolve_backend(
            source_fmt, "list", config, args.backend,
            required_capabilities=_backend_requirements(args, source_fmt, "list", source_password),
            no_fallback=args.no_fallback,
        )
        selected = list_backend.list_members(source, password=source_password)
        selected = _convert_selected_members(selected, args)
        validate_members(selected)
        if not selected:
            raise UsageError("no source members remain after conversion filters")
        if target_fmt.is_stream:
            regular = [member for member in selected if member.kind == "file"]
            if len(regular) != 1 or len([member for member in selected if member.kind != "dir"]) != 1:
                raise UnsupportedFormat(
                    f"cannot convert {len(selected)} selected archive members to single-stream {target_fmt.canonical}; select exactly one regular file or use a tar+compression target"
                )
            selected = regular
    with tempfile.TemporaryDirectory(prefix="arc-convert-tree-") as td:
        root = Path(td)
        extract_args = _clone_args(
            args,
            command="extract",
            archive=str(source),
            members=[member.name for member in selected] if selected else [],
            output=str(root),
            format=source_fmt.canonical,
            format_shortcut=None,
            password=source_password,
            password_file=None,
            password_env=None,
            filter_rules=[],
            overwrite=True,
            skip_existing=False,
            rename_existing=False,
            unsafe_paths=False,
            stdout=False,
            preserve_owner=False,
            preserve_acls=False,
            preserve_xattrs=False,
            yazi=None,
            quiet=True,
            json=False,
            dry_run=False,
        )
        stage_progress = progress_enabled(args.progress, args.json, args.quiet)
        with ProgressReporter("Reading", 0, len(selected), stage_progress) as reading:
            _extract(extract_args, [], config)
            for member in selected:
                reading.member_done(member.name, member.size)
            reading.complete()
        if target_fmt.is_stream:
            if source_fmt.is_stream:
                candidates = [path for path in root.rglob("*") if path.is_file()]
                if len(candidates) != 1:
                    raise UnsupportedFormat("stream conversion staging did not produce exactly one regular file")
                chosen = candidates[0]
            else:
                member = selected[0]
                chosen = root / member.name
            create_cwd = chosen.parent
            inputs = [chosen.name]
        else:
            create_cwd = root
            inputs = [path.name for path in sorted(root.iterdir(), key=lambda path: path.name)]
        if not inputs:
            raise UsageError("conversion produced no inputs for the destination")
        destination = destination.absolute()
        create_args = _clone_args(
            args,
            command="create",
            archive=str(destination),
            inputs=inputs,
            format=target_fmt.canonical,
            format_shortcut=None,
            password=destination_password,
            password_file=None,
            password_env=None,
            add_extension=False,
            overwrite=True,
            filter_rules=[],
            follow_symlinks=False,
            one_file_system=False,
            preserve_owner=False,
            preserve_acls=False,
            preserve_xattrs=False,
            yazi=None,
            quiet=True,
            json=False,
            dry_run=False,
        )
        previous_cwd = Path.cwd()
        try:
            os.chdir(create_cwd)
            with ProgressReporter("Writing", 0, 0, stage_progress) as writing:
                _create_like_local(create_args, [], config)
                writing.complete()
        finally:
            os.chdir(previous_cwd)
        return len(selected) if selected else 1


def _remove_conversion_source(raw: str, config: dict) -> None:
    remote = parse_remote(raw, config, probe_rclone=True)
    if remote is not None:
        delete_remote(remote, config)
        return
    path = Path(raw).expanduser()
    if path.is_dir():
        raise ConflictError(f"conversion source unexpectedly resolved to a directory: {path}")
    path.unlink()


def _conversion_strategy(source_fmt, target_fmt, args, *, transport_staged: bool = False) -> str:
    if source_fmt.canonical == target_fmt.canonical and not _filter_rules(args):
        strategy = "identity re-encode"
    elif source_fmt.is_stream and target_fmt.is_stream:
        strategy = "stream pipeline"
    elif source_fmt.container == "tar" and target_fmt.container == "tar" and not _filter_rules(args):
        strategy = "tar stream recompression"
    else:
        strategy = "isolated safe member pipeline"
    return f"transport-staged {strategy}" if transport_staged else strategy


def _local_conversion_candidate(destination: Path, target_fmt) -> Path:
    """Allocate a same-filesystem unpublished conversion candidate path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.arc-convert-candidate-",
        suffix=extension_for(target_fmt),
        dir=destination.parent,
    )
    os.close(fd)
    candidate = Path(tmp_name)
    # Creation backends expect to create/replace their output themselves.  The
    # reserved name prevents collisions while keeping the eventual os.replace
    # on the destination filesystem.
    candidate.unlink(missing_ok=True)
    return candidate


def _convert_one(job: dict, args, config: dict, source_password: str | None, destination_password: str | None, semantic_progress: SemanticProgress | None = None) -> dict:
    started = time.monotonic()
    if semantic_progress is not None:
        semantic_progress.phase("scan")
    source_raw = str(job["source"])
    destination_raw = str(job["destination"])
    target_fmt = job["target_format"]
    if args.replace_source and getattr(args, "verify_level", None) == VerificationLevel.NONE.value:
        raise UsageError("--replace-source requires verification; --verify-level none cannot authorize source deletion")
    source_remote = parse_remote(source_raw, config, probe_rclone=True)
    destination_remote = parse_remote(destination_raw, config, probe_rclone=True)
    transfer_progress = progress_enabled(args.progress, args.json, args.quiet)
    publication_guarantee: dict | None = None
    unpublished_candidate: Path | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="arc-convert-") as td:
            work = Path(td)
            if source_remote:
                local_source = work / (source_remote.basename or "source.arc")
                tx_event("remote-transfer", direction="download", source=source_raw, staging=os.fspath(local_source))
                download_remote(source_remote, local_source, config, progress=transfer_progress)
                tx_event("remote-transfer-complete", direction="download", source=source_raw)
            else:
                local_source = Path(source_raw).expanduser().absolute()
            source_fmt = detect(local_source)
            strategy = _conversion_strategy(
                source_fmt,
                target_fmt,
                args,
                transport_staged=bool(source_remote or destination_remote),
            )
            _show_convert_plan(source_raw, destination_raw, source_fmt, target_fmt, strategy, args, config)
            source_logical_fingerprint = None
            equivalence_proof = None
            if getattr(args, "prove_equivalent", False):
                tx_event("equivalence", status="source-fingerprint", source=source_raw)
                source_logical_fingerprint = _fingerprint_archive_local(
                    local_source, source_raw, args, config, source_password, fmt=source_fmt
                )

            final_local_destination: Path | None = None
            if destination_remote:
                local_destination = work / (destination_remote.basename or ("converted" + extension_for(target_fmt)))
            else:
                final_local_destination = Path(destination_raw).expanduser().absolute()
                unpublished_candidate = _local_conversion_candidate(final_local_destination, target_fmt)
                tx_cleanup(unpublished_candidate, reason="unpublished verified conversion candidate")
                tx_event("staging", candidate=os.fspath(unpublished_candidate), destination=os.fspath(final_local_destination))
                local_destination = unpublished_candidate

            source_size = local_source.stat().st_size
            tx_event("conversion-start", source=source_raw, destination=destination_raw, source_format=source_fmt.canonical, destination_format=target_fmt.canonical, strategy=strategy)
            member_count: int | None = 1 if source_fmt.is_stream else None
            if source_fmt.container == "tar" and target_fmt.container == "tar" and not _filter_rules(args):
                inspect_backend = resolve_backend(source_fmt, "list", config, args.backend, no_fallback=args.no_fallback)
                inspected = inspect_backend.list_members(local_source, password=source_password)
                validate_members(inspected)
                member_count = len(inspected)

            if semantic_progress is not None:
                semantic_progress.phase("encode", input_bytes=source_size)

            if source_fmt.is_stream and target_fmt.is_stream:
                if source_password or destination_password:
                    raise UnsupportedFormat("single-stream compression formats do not support archive passwords")
                _atomic_pipeline_convert(local_source, local_destination, source_fmt, target_fmt, args, config)
            elif source_fmt.container == "tar" and target_fmt.container == "tar" and not _filter_rules(args) and not source_password and not destination_password:
                _atomic_pipeline_convert(local_source, local_destination, source_fmt, target_fmt, args, config)
            else:
                member_count = _convert_stage_tree(
                    local_source,
                    local_destination,
                    source_fmt,
                    target_fmt,
                    source_password,
                    destination_password,
                    args,
                    config,
                )

            if semantic_progress is not None:
                semantic_progress.phase("verify", input_bytes=source_size, output_bytes=local_destination.stat().st_size if local_destination.exists() else 0)

            # Verification happens against the unpublished local candidate.  A
            # failed test therefore cannot leave a new/corrupt final local path
            # or clobber an existing --force destination.
            with ProgressReporter("Verifying", 0, 0, progress_enabled(args.progress, args.json, args.quiet)) as verification_progress:
                verification_evidence = _coerce_verification_evidence(
                    _verify_archive_path(local_destination, target_fmt, destination_password, args, config),
                    args,
                )
                verification_progress.complete()
            verify_backend = verification_evidence.backend
            verify_error = _verification_failure_detail(verification_evidence)
            if verification_evidence.status == "failed":
                tx_event("verify", status="failed", destination=destination_raw, detail=verify_error, evidence=verification_evidence.to_dict())
                raise CorruptArchive(f"converted destination failed verification: {verify_error}")
            tx_event(
                "verify",
                status=verification_evidence.status,
                destination=destination_raw,
                backend=verify_backend,
                scope="unpublished-candidate",
                evidence=verification_evidence.to_dict(),
            )

            if source_logical_fingerprint is not None:
                destination_fingerprint = _fingerprint_archive_local(
                    local_destination, destination_raw, args, config, destination_password, fmt=target_fmt
                )
                equivalence_proof = compare_fingerprints(source_logical_fingerprint, destination_fingerprint)
                if not equivalence_proof["equivalence"]["logical"]:
                    tx_event("equivalence", status="failed", destination=destination_raw, scope="unpublished-candidate", evidence=equivalence_proof)
                    raise CorruptArchive("converted destination is not logically equivalent to the source")
                tx_event("equivalence", status="passed", destination=destination_raw, scope="unpublished-candidate", evidence=equivalence_proof)

            if semantic_progress is not None:
                semantic_progress.phase("publish", input_bytes=source_size, output_bytes=local_destination.stat().st_size if local_destination.exists() else 0)

            if destination_remote:
                tx_event("remote-transfer", direction="upload", destination=destination_raw)
                publication_guarantee = upload_remote(local_destination, destination_remote, config, progress=transfer_progress)
                tx_event("remote-transfer-complete", direction="upload", destination=destination_raw)
                # Verification and equivalence proof both require evidence from
                # the published remote object rather than merely the local
                # pre-upload candidate.  A normal verification level of none
                # skips the re-read only when logical equivalence was not requested.
                need_remote_reread = (
                    verification_evidence.achieved is not VerificationLevel.NONE
                    or source_logical_fingerprint is not None
                )
                if need_remote_reread:
                    published = work / ("verify-" + (destination_remote.basename or "destination.arc"))
                    download_remote(destination_remote, published, config, progress=transfer_progress)
                    if verification_evidence.achieved is not VerificationLevel.NONE:
                        with ProgressReporter(
                            "Verifying remote",
                            0,
                            0,
                            progress_enabled(args.progress, args.json, args.quiet),
                        ) as remote_verification_progress:
                            remote_evidence = _coerce_verification_evidence(
                                _verify_archive_path(published, target_fmt, destination_password, args, config),
                                args,
                            )
                            remote_verification_progress.complete()
                        verify_backend = remote_evidence.backend
                        verify_error = _verification_failure_detail(remote_evidence)
                        if remote_evidence.status == "failed":
                            tx_event("verify", status="failed", destination=destination_raw, detail=verify_error, scope="published-remote-reread", evidence=remote_evidence.to_dict())
                            raise CorruptArchive(f"published remote destination failed verification: {verify_error}")
                        verification_evidence = remote_evidence
                        tx_event("verify", status=remote_evidence.status, destination=destination_raw, backend=verify_backend, scope="published-remote-reread", evidence=remote_evidence.to_dict())
                    if source_logical_fingerprint is not None:
                        published_fingerprint = _fingerprint_archive_local(
                            published, destination_raw, args, config, destination_password, fmt=target_fmt
                        )
                        equivalence_proof = compare_fingerprints(source_logical_fingerprint, published_fingerprint)
                        if not equivalence_proof["equivalence"]["logical"]:
                            tx_event("equivalence", status="failed", destination=destination_raw, scope="published-remote-reread", evidence=equivalence_proof)
                            raise CorruptArchive("published remote destination is not logically equivalent to the source")
                        tx_event("equivalence", status="passed", destination=destination_raw, scope="published-remote-reread", evidence=equivalence_proof)
                    publication = (
                        "transport-dependent-verified-reread"
                        if verification_evidence.achieved is not VerificationLevel.NONE
                        else "transport-dependent-equivalence-reread"
                    )
                    tx_event("publish", destination=destination_raw, publication=publication, publication_guarantee=publication_guarantee)
                    converted_size = published.stat().st_size
                else:
                    tx_event(
                        "publish",
                        destination=destination_raw,
                        publication="transport-dependent-unverified",
                        publication_guarantee=publication_guarantee,
                    )
                    converted_size = local_destination.stat().st_size
            else:
                assert final_local_destination is not None
                converted_size = local_destination.stat().st_size
                destination_policy = policy_from_args(args, legacy_replace=bool(getattr(args, "resume", False)))
                decision = decide_destination(final_local_destination, destination_policy, candidate=local_destination)
                if decision.action == "skip-identical":
                    local_destination.unlink(missing_ok=True)
                    tx_cleanup_done(local_destination)
                    publication_guarantee = {"strategy": "skip-identical", "scope": "local-filesystem", "atomicity": "not-required", "guaranteed_atomic": True, "replace_semantics": "skip-identical"}
                    tx_event("publish", destination=os.fspath(final_local_destination), publication="skip-identical", publication_guarantee=publication_guarantee, destination_policy=destination_policy.value)
                    unpublished_candidate = None
                    if args.replace_source:
                        tx_event("source-removal", status="started", source=source_raw)
                        _remove_conversion_source(source_raw, config)
                        tx_event("source-removal", status="done", source=source_raw)
                    return {"operation":"convert","source":source_raw,"destination":destination_raw,"source_format":source_fmt.canonical,"destination_format":target_fmt.canonical,"strategy":strategy,"original_bytes":source_size,"converted_bytes":converted_size,"size_change_bytes":converted_size-source_size,"ratio":(source_size/converted_size) if converted_size else None,"publication_guarantee":publication_guarantee,"skipped_identical":True}
                if decision.action == "rename" and decision.backup is not None:
                    os.replace(final_local_destination, decision.backup)
                    tx_event("destination-preserved", destination=os.fspath(final_local_destination), preserved_as=os.fspath(decision.backup), policy="rename")
                elif decision.action == "replace":
                    backup = backup_existing(final_local_destination, getattr(args, "backup_existing", None))
                    if backup is not None:
                        tx_event("destination-preserved", destination=os.fspath(final_local_destination), preserved_as=os.fspath(backup), policy="backup-existing")
                _atomic_replace(local_destination, final_local_destination)
                tx_cleanup_done(local_destination)
                publication_guarantee = {
                    "strategy": "same-parent-temporary-file+os.replace",
                    "scope": "local-filesystem",
                    "atomicity": "same-filesystem-replace",
                    "guaranteed_atomic": True,
                    "replace_semantics": "replace",
                }
                tx_event(
                    "publish",
                    destination=os.fspath(final_local_destination),
                    publication="local-same-filesystem-atomic-replace",
                    publication_guarantee=publication_guarantee,
                )
                unpublished_candidate = None

            if args.replace_source:
                tx_event("source-removal", status="started", source=source_raw)
                _remove_conversion_source(source_raw, config)
                tx_event("source-removal", status="done", source=source_raw)

            size_change = ((converted_size - source_size) / source_size * 100.0) if source_size else None
            ratio = (source_size / converted_size) if converted_size else None
            return {
                "operation": "convert",
                "source": source_raw,
                "destination": destination_raw,
                "source_format": source_fmt.canonical,
                "destination_format": target_fmt.canonical,
                "strategy": strategy,
                "original_bytes": source_size,
                "converted_bytes": converted_size,
                "size_change_bytes": converted_size - source_size,
                "size_change_percent": size_change,
                "ratio": ratio,
                "members": member_count,
                "verified": True if verification_evidence.status == "passed" and verification_evidence.achieved is not VerificationLevel.NONE else None,
                "verify_backend": verify_backend,
                "verification": verification_evidence.to_dict(),
                "equivalence": equivalence_proof,
                "publication_guarantee": publication_guarantee,
                "source_removed": bool(args.replace_source),
                "transport": destination_remote.kind if destination_remote else (source_remote.kind if source_remote else "local"),
                "duration_seconds": time.monotonic() - started,
            }
    finally:
        if unpublished_candidate is not None:
            unpublished_candidate.unlink(missing_ok=True)
            tx_cleanup_done(unpublished_candidate)
            tx_event("cleanup", path=os.fspath(unpublished_candidate))



def _batch_policy(args, jobs: list[dict]) -> dict:
    rules = [list(item) for item in (getattr(args, "filter_rules", []) or [])]
    return {
        "target_formats": [job["target_format"].canonical for job in jobs],
        "backend": getattr(args, "backend", None),
        "no_fallback": bool(getattr(args, "no_fallback", False)),
        "level": getattr(args, "level", None),
        "threads": getattr(args, "threads", None),
        "filters": rules,
        "replace_source": bool(getattr(args, "replace_source", False)),
        "verify_level": getattr(args, "verify_level", None),
        "allow_verification_downgrade": bool(getattr(args, "allow_verification_downgrade", False)),
        "prove_equivalent": bool(getattr(args, "prove_equivalent", False)),
        "execution": getattr(args, "execution", None),
    }


def _resume_source_fingerprint(raw: str, config: dict) -> dict | None:
    remote = parse_remote(raw, config, probe_rclone=False)
    if remote is not None:
        return {"kind": "remote-unproven", "path": remote.raw, "reusable": False}
    path = Path(raw).expanduser()
    if not path.is_file():
        return None
    return file_fingerprint(path)


def _resume_destination_fingerprint(raw: str, config: dict) -> dict | None:
    remote = parse_remote(raw, config, probe_rclone=False)
    if remote is not None:
        return {"kind": "remote-unproven", "path": remote.raw, "reusable": False}
    path = Path(raw).expanduser()
    if not path.is_file():
        return None
    return file_fingerprint(path)


def _resume_entry_valid(entry: dict | None, job: dict, config: dict) -> bool:
    if not entry or entry.get("status") != "completed":
        return False
    if not bool((entry.get("result") or {}).get("verified")):
        return False
    prior_destination = entry.get("destination_fingerprint")
    current_destination = _resume_destination_fingerprint(str(job["destination"]), config)
    if not same_fingerprint(prior_destination, current_destination):
        return False
    current_source = _resume_source_fingerprint(str(job["source"]), config)
    prior_source = entry.get("source_fingerprint")
    if current_source is None and entry.get("source_removed") is True:
        # The original source was deliberately removed only after verified
        # publication. The still-matching destination fingerprint is the
        # durable proof that this completed item can be reused.
        return True
    if not current_source or current_source.get("reusable") is False:
        return False
    return same_fingerprint(prior_source, current_source)


def _resumed_result(entry: dict, *, batch_id: str) -> dict:
    result = dict(entry.get("result") or {})
    result["resumed"] = True
    result["reused_evidence"] = True
    result["batch_id"] = batch_id
    return result

def _print_convert_success(result: dict) -> None:
    t = Table(title="Conversion complete", show_header=False, box=None, pad_edge=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    t.add_row("Original", decimal(int(result["original_bytes"])))
    t.add_row("Converted", decimal(int(result["converted_bytes"])))
    change = result.get("size_change_percent")
    if change is not None:
        if change <= 0:
            t.add_row("Reduction", f"{-float(change):.1f}%")
        else:
            t.add_row("Change", f"+{float(change):.1f}%")
    if result.get("ratio") is not None:
        t.add_row("Ratio", f"{float(result['ratio']):.2f}:1")
    if result.get("members") is not None:
        t.add_row("Members", str(result["members"]))
    t.add_row("Output", str(result["destination"]))
    evidence = result.get("verification") or {}
    if result.get("verified"):
        label = str(evidence.get("achieved") or "verified")
        if evidence.get("downgraded"):
            label += " (downgraded)"
        t.add_row("Verified", f"yes ({result['verify_backend']}) · {label}")
    else:
        t.add_row("Verified", "no (verification disabled)")
    if result.get("duration_seconds") is not None:
        t.add_row("Duration", f"{float(result['duration_seconds']):.2f}s")
    if result.get("equivalence"):
        eq = result["equivalence"].get("equivalence", {})
        label = "yes" if eq.get("logical") else "no"
        if eq.get("logical") and not eq.get("metadata"):
            label += " (metadata differs)"
        t.add_row("Equivalent", label)
    if result.get("source_removed"):
        t.add_row("Source", "removed after verification")
    stdout_console.print(t)


def _convert(args, config: dict) -> int:
    if args.replace_source and getattr(args, "verify_level", None) == VerificationLevel.NONE.value:
        raise UsageError("--replace-source requires verification; --verify-level none cannot authorize source deletion")
    jobs = _resolve_convert_jobs(args, config)
    source_password = _resolve_source_password(args)
    destination_password = _resolve_password(args)
    if args.dry_run:
        plans = []
        for job in jobs:
            source = job["source"]
            remote = parse_remote(source, config, probe_rclone=False)
            if remote is not None:
                source_fmt = infer_from_name(remote.path)
            else:
                source_path = Path(source).expanduser()
                source_fmt = detect(source_path) if source_path.exists() else infer_from_name(source_path)
            if source_fmt is None:
                raise UnsupportedFormat(f"cannot identify source format during dry-run: {source}")
            destination_remote = parse_remote(job["destination"], config, probe_rclone=False)
            strategy = _conversion_strategy(
                source_fmt,
                job["target_format"],
                args,
                transport_staged=bool(remote or destination_remote),
            )
            _show_convert_plan(source, job["destination"], source_fmt, job["target_format"], strategy, args, config)
            plans.append({
                "source": source,
                "destination": job["destination"],
                "source_format": source_fmt.canonical,
                "destination_format": job["target_format"].canonical,
                "strategy": strategy,
                "publication": (
                    "transport-dependent-remote-publish-with-verification-and-equivalence-reread"
                    if destination_remote and _verification_plan(job["target_format"], args, config)["selected"] != "none" and args.prove_equivalent
                    else "transport-dependent-remote-publish-with-verification-reread"
                    if destination_remote and _verification_plan(job["target_format"], args, config)["selected"] != "none"
                    else "transport-dependent-remote-publish-with-equivalence-reread"
                    if destination_remote and args.prove_equivalent
                    else "transport-dependent-remote-publish-unverified"
                    if destination_remote
                    else "local-same-filesystem-atomic-replace"
                ),
                "verification": _verification_plan(job["target_format"], args, config),
                "equivalence": "prove-source-vs-destination" if args.prove_equivalent else "not-requested",
                "source_removal": "after-verified-publish" if args.replace_source else "keep",
                "dry_run": True,
            })
        plan = plan_dict()
        for item in plans:
            item["plan"] = plan
        if args.json:
            print(json.dumps(plans[0] if len(plans) == 1 else plans, ensure_ascii=False))
        return 0

    is_batch = bool(args.batch or len(jobs) > 1)
    if args.resume and not is_batch:
        raise UsageError("--resume requires batch conversion")
    policy = _batch_policy(args, jobs)
    batch_id = args.batch_id or batch_policy_key(jobs, policy) if is_batch else None
    try:
        batch = BatchManifest.open(batch_id, policy=policy, reset=not args.resume) if batch_id else None
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    metadata = {
        "batch_id": batch_id,
        "jobs": len(jobs),
        "resume": bool(args.resume),
        "replace_source": bool(args.replace_source),
    }
    results: list[dict] = []
    with transaction_scope("convert-batch" if is_batch else "convert", metadata=metadata) as transaction:
        for job in jobs:
            source = str(job["source"])
            entry = batch.item(source) if batch else None
            if batch and args.resume and _resume_entry_valid(entry, job, config):
                result = _resumed_result(entry, batch_id=batch_id or "")
                tx_event("resume-reuse", source=source, destination=str(job["destination"]), batch_id=batch_id)
                results.append(result)
                continue
            if batch and args.resume:
                destination = str(job["destination"])
                destination_remote = parse_remote(destination, config, probe_rclone=False)
                destination_exists = False
                if destination_remote is None:
                    destination_exists = Path(destination).expanduser().exists()
                if destination_remote is not None and not args.force:
                    raise ConflictError(
                        f"resume cannot prove remote destination identity for {destination}; use --force to authorize a fresh remote publish"
                    )
                if destination_exists and not args.force:
                    if not entry or entry.get("destination") != destination:
                        raise ConflictError(
                            f"resume destination exists but is not owned by batch {batch_id}: {destination}; use --force to replace it"
                        )
                    prior_destination = entry.get("destination_fingerprint")
                    current_destination = _resume_destination_fingerprint(destination, config)
                    if prior_destination and current_destination and not same_fingerprint(prior_destination, current_destination):
                        raise ConflictError(
                            f"resume destination changed since the last verified batch result: {destination}; use --force to replace user-modified output"
                        )
            source_fingerprint = _resume_source_fingerprint(source, config) if batch else None
            if batch:
                batch.update_item(source, {
                    "status": "running",
                    "source": source,
                    "destination": str(job["destination"]),
                    "source_fingerprint": source_fingerprint,
                    "started_at": time.time(),
                })
            try:
                semantic_enabled = progress_enabled(args.progress, args.json, args.quiet)
                with SemanticProgress(semantic_enabled, "convert", batch_index=len(results) + 1, batch_total=len(jobs)) as semantic:
                    result = _convert_one(job, args, config, source_password, destination_password, semantic)
                    semantic.complete(input_bytes=int(result.get("original_bytes") or 0), output_bytes=int(result.get("converted_bytes") or 0))
                if batch_id:
                    result["batch_id"] = batch_id
                    result["resumed"] = False
                destination_fingerprint = _resume_destination_fingerprint(str(job["destination"]), config) if batch else None
                if batch:
                    batch.update_item(source, {
                        "status": "completed",
                        "source": source,
                        "destination": str(job["destination"]),
                        "source_fingerprint": source_fingerprint,
                        "destination_fingerprint": destination_fingerprint,
                        "source_removed": bool(result.get("source_removed")),
                        "verified": bool(result.get("verified")),
                        "verify_backend": result.get("verify_backend"),
                        "result": result,
                        "completed_at": time.time(),
                    })
                results.append(result)
            except BaseException as exc:
                if batch:
                    batch.update_item(source, {
                        "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                        "source": source,
                        "destination": str(job["destination"]),
                        "source_fingerprint": source_fingerprint,
                        "error": str(exc),
                        "failed_at": time.time(),
                    })
                raise
        transaction.complete({
            "batch": batch.to_summary() if batch else None,
            "items": len(results),
            "reused": sum(1 for result in results if result.get("resumed")),
        })
    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False))
    elif not args.quiet:
        for result in results:
            if result.get("resumed"):
                stdout_console.print(f"[green]REUSED[/] {result['destination']} (verified batch evidence)")
            else:
                _print_convert_success(result)
        if batch:
            stdout_console.print(f"[dim]Batch {batch_id} · manifest {batch.path}[/]")
    return 0


def _show_profiles(config: dict, *, json_mode: bool = False) -> int:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    if json_mode:
        print(json.dumps(profiles, ensure_ascii=False, sort_keys=True))
        return 0
    t = Table(title="Arc profiles")
    t.add_column("Profile")
    t.add_column("Options")
    for name in sorted(profiles):
        value = profiles[name]
        summary = ", ".join(sorted(value)) if isinstance(value, dict) else type(value).__name__
        t.add_row(str(name), summary)
    if not profiles:
        t.add_row("—", "no configured profiles")
    stdout_console.print(t)
    return 0


def _man_command(args) -> int:
    if args.list_topics:
        for topic in available_topics():
            print(topic)
        return 0
    return show_manpage(args.topic, plain=args.plain)

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


def _schema_command(args) -> int:
    if args.list or not args.name:
        print(json.dumps({"schema_version": 1, "schemas": list(schema_names())}, ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps(load_schema(args.name), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _parse_machine_result(text: str):
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        values = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                return {"raw": stripped}
        return values


def _show_backends(config: dict, json_mode: bool = False, remote: str | None = None, verbose: bool = False, refresh: bool = False) -> int:
    if remote:
        location = _remote_name_location(remote, config)
        data = remote_capabilities(location, config, refresh=refresh)
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
    if verbose:
        t.add_column("Verify")
        t.add_column("I/O")
        t.add_column("Remote")
    for row in rows:
        for index, candidate in enumerate(row["candidates"]):
            values = [
                row["role"] if index == 0 else "",
                candidate["binary"],
                candidate["path"] or "missing",
                " ".join(candidate["capabilities"]),
            ]
            if verbose:
                profile = candidate["capability_profile"]
                values.extend([
                    str(profile["verification"]["maximum"]),
                    "/".join(name for name, enabled in profile["streams"].items() if enabled) or "file",
                    str(profile["remote_suitability"]),
                ])
            t.add_row(*values)
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


def _aliases_command(args) -> int:
    rows = alias_status_rows()
    if args.missing:
        rows = [row for row in rows if not row["available"]]
    if args.json:
        print(json.dumps({"schema_version": 1, "aliases": rows}, sort_keys=True))
        return 0
    t = Table(title="Arc executable aliases")
    t.add_column("Executable")
    t.add_column("Command")
    t.add_column("Installed")
    t.add_column("Path")
    for row in rows:
        installed = "yes" if row["available"] else "no"
        t.add_row(str(row["executable"]), str(row["command"]), installed, str(row["path"] or "—"))
    stdout_console.print(t)
    return 0


def _doctor_command(args) -> int:
    if args.fix:
        report, _result = fix_and_recheck(source=args.source)
    else:
        report = collect_doctor_report(source=args.source)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        t = Table(title="Arc doctor")
        t.add_column("Status")
        t.add_column("Check")
        t.add_column("Detail")
        for check in report["checks"]:
            t.add_row(str(check["status"]).upper(), str(check["summary"]), str(check["detail"]))
        stdout_console.print(t)
        summary = report["summary"]
        stdout_console.print(
            f"[bold]Summary:[/bold] {summary['pass']} pass · {summary['warn']} warn · {summary['fail']} fail"
        )
        if summary["warn"] or summary["fail"]:
            stdout_console.print("Run [bold]arc doctor --fix[/bold] to refresh generated surfaces and the editable install when a source checkout is available.")
    return 1 if report["summary"]["fail"] else 0



def _explain_command(args, config: dict) -> int:
    target_argv = list(args.argv)
    if target_argv and target_argv[0] == "--":
        target_argv = target_argv[1:]
    if not target_argv:
        raise UsageError("arc explain requires an Arc command to plan")
    wrapper_argv, extra = split_passthrough(target_argv)
    inner = parser().parse_args(wrapper_argv)
    if inner.command in {"explain", "recover", "doctor", "aliases", "man", "help", "completion"}:
        raise UsageError(f"arc explain does not plan the {inner.command!r} command")
    if not hasattr(inner, "dry_run"):
        raise UsageError(f"arc explain requires an operation with dry-run support; {inner.command!r} is read-only or informational")
    inner._invoked_program = "arc"
    inner._display_argv = target_argv
    _apply_profile(inner, config)
    _validate_yazi_context(inner)
    _apply_defaults(inner, config)
    if inner.command in {"create", "convert"}:
        _create_format_options(inner)
    inner.dry_run = True
    inner.json = True
    inner.quiet = True
    inner.progress = "never"
    inner.show_native = None
    inner.show_command = False
    begin_plan(inner.command, mode=None, style=getattr(inner, "native_style", None) or "reproducible")
    captured = io.StringIO()
    with redirect_stdout(captured):
        rc = _dispatch_command(inner, extra, config)
    raw_result = captured.getvalue().strip()
    result = None
    if raw_result:
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            result = {"raw": raw_result}
    plan = plan_dict()
    payload = {
        "schema_version": 1,
        "operation": inner.command,
        "dry_run": True,
        "mutation": False,
        "result": result,
        "plan": plan,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return rc
    t = Table(title=f"Arc explain · {inner.command}", show_header=False, box=None, pad_edge=False)
    t.add_column("Field", style="bold")
    t.add_column("Value")
    t.add_row("Mutation", "no")
    for decision in plan.get("decisions", []):
        reason = decision.get("reason")
        value = str(decision.get("value"))
        t.add_row(str(decision.get("name")), value + (f" · {reason}" if reason else ""))
    stdout_console.print(t)
    if plan.get("stages"):
        stdout_console.print("[bold]Native stages[/]")
        for stage in plan["stages"]:
            stdout_console.print(f"  {stage.get('reproducible') or stage.get('display')}")
    return rc


def _recover_command(args) -> int:
    if args.transaction_id:
        try:
            journal = TransactionJournal.load(args.transaction_id)
        except KeyError as exc:
            raise UsageError(f"unknown transaction: {args.transaction_id}") from exc
        except ValueError as exc:
            raise UsageError(str(exc)) from exc
        cleanup = journal.cleanup() if args.cleanup else None
        payload = dict(journal.data)
        if cleanup is not None:
            payload["cleanup_result"] = cleanup
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        t = Table(title=f"Arc transaction {journal.id}", show_header=False, box=None, pad_edge=False)
        t.add_column("Field", style="bold")
        t.add_column("Value")
        t.add_row("Operation", str(payload.get("operation")))
        t.add_row("Status", str(payload.get("status")))
        t.add_row("Updated", str(payload.get("updated_at")))
        t.add_row("Events", str(len(payload.get("events", []))))
        pending = [item for item in payload.get("cleanup_paths", []) if not item.get("cleaned")]
        t.add_row("Pending cleanup", str(len(pending)))
        stdout_console.print(t)
        if cleanup is None and pending:
            stdout_console.print(f"Run [bold]arc recover {journal.id} --cleanup[/bold] to remove only registered transaction-owned temporary paths.")
        return 0

    rows = list_transactions(include_completed=args.all)
    if args.cleanup:
        for row in rows:
            try:
                TransactionJournal.load(str(row.get("id"))).cleanup()
            except (KeyError, ValueError):
                continue
        rows = list_transactions(include_completed=args.all)
    if args.json:
        print(json.dumps({"schema_version": 1, "transactions": rows}, ensure_ascii=False, sort_keys=True))
        return 0
    t = Table(title="Arc transactions")
    t.add_column("ID")
    t.add_column("Operation")
    t.add_column("Status")
    t.add_column("Updated")
    for row in rows:
        t.add_row(str(row.get("id")), str(row.get("operation")), str(row.get("status")), str(row.get("updated_at")))
    if not rows:
        t.add_row("—", "—", "no recoverable transactions", "—")
    stdout_console.print(t)
    return 0

def _preflight_batch_request(request) -> None:
    """Parse every nested argv before the first operation is allowed to run.

    This catches malformed options/operands transactionally at the batch
    boundary and also forbids prompt-style password flags, which would violate
    the non-interactive machine contract.  Help-only argv (argparse exit 0) are
    valid operations and remain capture-only at execution time.
    """
    prompt_attrs = ("password", "source_password", "left_password", "right_password")
    for operation in request.operations:
        parse_out = io.StringIO()
        parse_err = io.StringIO()
        parsed = None
        try:
            with redirect_stdout(parse_out), redirect_stderr(parse_err):
                parsed = parser().parse_args(_normalize_json_argv(list(operation.argv)))
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 2
            if code != 0:
                detail = _parse_error_message(parse_err.getvalue())
                raise UsageError(f"batch operation {operation.id!r} has invalid Arc argv: {detail}") from None
        if parsed is not None and any(getattr(parsed, name, None) == "__PROMPT__" for name in prompt_attrs):
            raise UsageError(
                f"batch operation {operation.id!r} requests an interactive password prompt; "
                "machine batches must provide a value, --password-file, or --password-env"
            )


def _batch_command(args) -> int:
    request = load_batch_input(args.input)
    _preflight_batch_request(request)
    if args.validate_only:
        payload = {
            "schema": "arc.batch-validation/v1",
            "schema_version": 1,
            "status": "valid",
            "operations": len(request.operations),
            "on_error": request.on_error,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            stdout_console.print(f"[green]VALID[/] {len(request.operations)} operation(s) · on_error={request.on_error}")
        return 0

    def runner(argv: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = contextvars.copy_context().run(main, argv)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 2
        return int(code), out.getvalue(), err.getvalue()

    payload = execute_batch(request, runner)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        table = Table(title="Arc batch")
        table.add_column("ID")
        table.add_column("Status")
        table.add_column("Exit", justify="right")
        table.add_column("Command")
        for item in payload["operations"]:
            table.add_row(str(item["id"]), str(item["status"]), str(item["exit_code"]), shlex.join(item["argv"]))
        stdout_console.print(table)
        stdout_console.print(f"Executed {payload['executed']}/{payload['requested']} · failed {payload['failed']}")
    if payload["status"] == "interrupted":
        return 130
    return 0 if payload["status"] == "ok" else 1


def _dispatch_command(args, extra: list[str], config: dict) -> int:
    if args.command == "identify":
        return _identify(args, config)
    if args.command == "info":
        return _info(args, config)
    if args.command == "diff":
        return _diff_command(args, config)
    if args.command == "explain":
        return _explain_command(args, config)
    if args.command == "recover":
        return _recover_command(args)
    if args.command == "batch":
        return _batch_command(args)
    if args.command == "convert":
        return _convert(args, config)
    if args.command in {"create", "add", "update", "list", "test", "extract", "remove"}:
        def run_archive_command() -> int:
            remote_rc = _dispatch_remote_archive(args, extra, config)
            if remote_rc is not None:
                if args.command in {"create", "add", "update", "remove"} and not args.dry_run:
                    tx_event("remote-operation-complete", operation=args.command)
                return remote_rc
            if args.command in {"create", "add", "update"}:
                return _create_like(args, extra, config)
            if args.command in {"list", "test"}:
                return _list_or_test(args, extra, config)
            if args.command == "extract":
                return _extract(args, extra, config)
            return _remove(args, extra, config)

        if args.command in {"create", "add", "update", "remove"} and not args.dry_run and current_transaction() is None:
            with transaction_scope(
                args.command,
                metadata={"invocation": _display_invocation([], args), "remote_execution": getattr(args, "execution", None)},
            ) as journal:
                rc = run_archive_command()
                journal.complete({"exit_code": rc})
                return rc
        return run_archive_command()
    if args.command == "backends":
        return _show_backends(config, args.json, args.remote, args.verbose, args.refresh)
    if args.command == "formats":
        return _show_formats(args.json, args.remote, config)
    if args.command == "profiles":
        return _show_profiles(config, args.json)
    if args.command == "aliases":
        return _aliases_command(args)
    if args.command == "doctor":
        return _doctor_command(args)
    if args.command == "schema":
        return _schema_command(args)
    if args.command in {"man", "help"}:
        return _man_command(args)
    if args.command == "completion":
        return _completion_command(args, config)
    raise UsageError(f"unknown command: {args.command}")


def _alias_json_identity(args) -> dict[str, str]:
    return {
        "invocation": _display_invocation([], args),
        "resolved_command": str(args.command),
    }


def _decorate_alias_json_output(text: str, args) -> str:
    """Attach literal-vs-canonical identity to alias JSON without schema wrappers."""
    if not text.strip():
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    identity = _alias_json_identity(args)

    def decorate(value):
        if isinstance(value, dict):
            for key, item in identity.items():
                value.setdefault(key, item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for key, identity_value in identity.items():
                        item.setdefault(key, identity_value)
        return value

    return json.dumps(decorate(payload), ensure_ascii=False) + "\n"


def _machine_v1_requested(argv: list[str]) -> bool:
    for token in argv:
        if token == "--":
            break
        if token == "--json=v1":
            return True
    return False


def _parse_error_message(stderr_text: str) -> str:
    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    if not lines:
        return "invalid command line"
    last = lines[-1]
    marker = "error: "
    if marker in last:
        return last.split(marker, 1)[1]
    return last


def _machine_parse_args(invoked_program: str, display_argv: list[str], command: str, message: str):
    return argparse.Namespace(
        _invoked_program=invoked_program,
        _display_argv=list(display_argv),
        command=command,
    )


def main(argv: list[str] | None = None) -> int:
    explicit_argv = argv is not None
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    display_argv = list(raw_argv)
    machine_parse = _machine_v1_requested(display_argv)
    argv = _normalize_json_argv(raw_argv)
    invoked_program = Path(sys.argv[0]).name if not explicit_argv else "arc"
    implied_command = EXECUTABLE_ALIASES.get(invoked_program) if not explicit_argv else None
    if implied_command:
        argv = [implied_command, *argv]
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
    parse_stderr = io.StringIO()
    try:
        if machine_parse:
            with redirect_stderr(parse_stderr):
                args = parser().parse_args(wrapper_argv)
        else:
            args = parser().parse_args(wrapper_argv)
    except SystemExit as exc:
        if machine_parse:
            command = implied_command or next((token for token in wrapper_argv if not token.startswith("-")), "")
            machine_args = _machine_parse_args(
                invoked_program,
                display_argv,
                command,
                _parse_error_message(parse_stderr.getvalue()),
            )
            sys.stdout.write(
                machine_dumps(
                    machine_envelope(
                        machine_args,
                        error=MachineError(
                            "UsageError",
                            _parse_error_message(parse_stderr.getvalue()),
                            int(exc.code) if isinstance(exc.code, int) else 2,
                            "usage",
                        ),
                    )
                )
            )
            return int(exc.code) if isinstance(exc.code, int) else 2
        raise
    args._invoked_program = invoked_program
    args._display_argv = display_argv
    config = load_config()
    try:
        _apply_profile(args, config)
        _validate_yazi_context(args)
        _apply_defaults(args, config)
        if args.command in {"create", "convert"}:
            _create_format_options(args)
        machine_v1 = _json_v1(args)
        native_mode = getattr(args, "show_native", None)
        begin_plan(
            args.command,
            mode=None if machine_v1 else native_mode,
            style=getattr(args, "native_style", None) or "reproducible",
            show_primary_command=(
                console.is_terminal
                and not bool(getattr(args, "quiet", False))
                and not bool(getattr(args, "json", False))
                and args.command in {"list", "test", "extract", "create", "add", "update", "remove", "info", "convert"}
            ),
        )
        if machine_v1:
            captured = io.StringIO()
            with redirect_stdout(captured):
                rc = _dispatch_command(args, extra, config)
            diagnostics = []
            if native_mode in {"before", "after", "both"}:
                diagnostics.append(machine_diagnostic("native_plan", plan_dict()))
            sys.stdout.write(
                machine_dumps(
                    machine_envelope(
                        args,
                        result=_parse_machine_result(captured.getvalue()),
                        diagnostics=diagnostics,
                        status="ok" if rc == 0 else "failed",
                    )
                )
            )
        else:
            capture_alias_json = bool(getattr(args, "json", False) and invoked_program in EXECUTABLE_ALIASES)
            if capture_alias_json:
                captured = io.StringIO()
                with redirect_stdout(captured):
                    rc = _dispatch_command(args, extra, config)
                sys.stdout.write(_decorate_alias_json_output(captured.getvalue(), args))
            else:
                rc = _dispatch_command(args, extra, config)
            if rc == 0:
                emit_after(json_mode=bool(getattr(args, "json", False)))
        return rc
    except KeyboardInterrupt:
        if 'args' in locals() and _json_v1(args):
            sys.stdout.write(machine_dumps(machine_envelope(args, error=MachineError("KeyboardInterrupt", "interrupted", 130, "interrupt"))))
        else:
            console.print("[yellow]Interrupted[/]")
        return 130
    except ArcError as exc:
        if 'args' in locals() and _json_v1(args):
            sys.stdout.write(machine_dumps(machine_envelope(args, error=MachineError(type(exc).__name__, str(exc), exc.exit_code))))
        else:
            console.print(f"[bold red]error:[/] {exc}")
        return exc.exit_code
    except BrokenPipeError:
        return 141


if __name__ == "__main__":
    raise SystemExit(main())
