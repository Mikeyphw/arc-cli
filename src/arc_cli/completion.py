from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import load_config, profile_names
from .formats import CREATE_SUFFIX_SHORTCUTS
from .command_docs import COMMAND_DOCS, EXECUTABLE_ALIASES
from .interactive import filesystem_candidates, rg_files
from .remote import complete_remote, configured_remote_names, parse_remote

OPERATIONS = list(COMMAND_DOCS)
FORMATS = ["tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zstd", "zip", "7z", "rar", "gzip", "bzip2", "xz", "zstd"]
BACKEND_NAMES = ["tar", "bsdtar", "7z", "7zz", "zip", "unzip", "rar", "unrar", "gzip", "pigz", "bzip2", "pbzip2", "xz", "pixz", "zstd", "pzstd"]

VALUE_OPTIONS = {
    "--format", "-F", "--backend", "-o", "--output", "--level", "--threads", "--exclude", "--include",
    "--exclude-from", "--include-from", "--progress", "--password-file", "--password-env", "--profile",
    "--show-native", "--native-style", "--execution", "--source-password-file", "--source-password-env", "--source", "--batch-id",
    "--verify-level",
}
OPTIONAL_VALUE_OPTIONS = {"--password", "--source-password", "--yazi"}

BASE = {
    "--format", "-F", "--backend", "--no-fallback", "--profile", "--dry-run", "--show-command", "-q", "--quiet", "-v", "--verbose",
    "--json", "--progress", "--yazi", "--password", "--password-file", "--password-env",
    "--show-native", "--native-style", "--execution", "--source-password-file", "--source-password-env",
}
FILTERS = {"--exclude", "--include", "--exclude-from", "--include-from"}
CREATE = {"--level", "--threads", "--add-extension", "--follow-symlinks", "--one-file-system", "--preserve-owner", "--preserve-acls", "--preserve-xattrs"}
CREATE_SUFFIX_FLAGS = {flag for flag, _fmt, _suffix in CREATE_SUFFIX_SHORTCUTS}
EXTRACT = {"-o", "--output", "--overwrite", "--skip-existing", "--rename-existing", "--unsafe-paths", "--stdout", "--preserve-owner", "--preserve-acls", "--preserve-xattrs"}
VERIFY = {"--verify-level", "--allow-verification-downgrade"}
CONVERT = CREATE | FILTERS | CREATE_SUFFIX_FLAGS | VERIFY | {"-f", "--force", "--replace-source", "--batch", "--resume", "--batch-id", "--source-password", "--source-password-file", "--source-password-env"}
INFO = {"--format", "-F", "--backend", "--no-fallback", "--profile", "--password", "--password-file", "--password-env", "--members", "--verify", "--technical", "--json", "-q", "--quiet", "-v", "--verbose", "--progress", "--show-command", "--show-native", "--native-style", "--execution"} | VERIFY

COMMAND_OPTIONS: dict[str, set[str]] = {
    "identify": {"--format", "-F", "--json", "--yazi"},
    "list": BASE | FILTERS,
    "test": BASE | FILTERS | VERIFY,
    "extract": BASE | FILTERS | EXTRACT,
    "create": BASE | FILTERS | CREATE | CREATE_SUFFIX_FLAGS | {"--overwrite"},
    "add": BASE | FILTERS | CREATE,
    "update": BASE | FILTERS | CREATE,
    "remove": BASE,
    "info": INFO,
    "convert": BASE | CONVERT,
    "backends": {"--json", "--remote", "--verbose"},
    "formats": {"--json", "--remote"},
    "profiles": {"--json"},
    "aliases": {"--json", "--missing"},
    "doctor": {"--json", "--fix", "--source"},
    "explain": {"--json"},
    "recover": {"--cleanup", "--all", "--json"},
    "schema": {"--list"},
    "man": {"--list", "--plain"},
    "help": set(),
    "completion": set(),
}


def _positionals(tokens: list[str]) -> list[str]:
    out: list[str] = []
    skip = False
    passthrough = False
    for token in tokens:
        if passthrough:
            continue
        if skip:
            skip = False
            continue
        if token == "--":
            passthrough = True
            continue
        if token in VALUE_OPTIONS:
            skip = True
            continue
        if token in OPTIONAL_VALUE_OPTIONS:
            # Optional arguments are normally written --opt=value by completion
            # users. Treat a bare form as a flag so the next positional is not
            # accidentally swallowed.
            continue
        if token.startswith("--") and "=" in token:
            continue
        if token.startswith("-"):
            continue
        out.append(token)
    return out


def _archive_members(path: str) -> list[str]:
    try:
        from .backends import resolve_backend
        from .formats import detect

        p = Path(path).expanduser()
        if not p.is_file():
            return []
        fmt = detect(p)
        if fmt.is_stream:
            return []
        backend = resolve_backend(fmt, "list", load_config())
        return [m.name for m in backend.list_members(p)]
    except Exception:
        return []


def _option_candidates(op: str, prefix: str, prior_tokens: list[str] | None = None) -> list[str]:
    options = set(COMMAND_OPTIONS.get(op, set()))
    prior = prior_tokens or []
    if op in {"create", "convert"}:
        has_shortcut = any(token in CREATE_SUFFIX_FLAGS for token in prior)
        has_format = any(
            token in {"-F", "--format"} or token.startswith("--format=")
            for token in prior
        )
        if has_shortcut:
            options -= CREATE_SUFFIX_FLAGS | {"-F", "--format"}
        elif has_format:
            options -= CREATE_SUFFIX_FLAGS | {"-F", "--format"}
    return sorted(x for x in options if x.startswith(prefix))


def _path_candidates(prefix: str, *, op: str, refresh: bool = False, dirs_only: bool = False) -> list[str]:
    config = load_config()
    remote = parse_remote(prefix, config, probe_rclone=True)
    if remote is not None or prefix.startswith(("ssh://", "rclone://")) or ":" in prefix:
        try:
            return complete_remote(
                prefix,
                config,
                refresh=refresh,
                dirs_only=dirs_only,
                archives_only=op in {"identify", "list", "test", "extract", "remove", "info", "convert"},
            )
        except Exception:
            return []
    local = filesystem_candidates(include_dirs=True)
    if dirs_only:
        local = [x for x in local if x.endswith(os.sep)]
    remotes = [name + ":" for name in configured_remote_names(config) if (name + ":").startswith(prefix)]
    return remotes + [x for x in local if x.startswith(prefix)]


def completion_candidates(words: list[str]) -> list[str]:
    """Return context-aware candidates. Strings are not line-framed here."""
    if not words:
        return OPERATIONS
    if len(words) == 1:
        cur = words[0]
        return [x for x in OPERATIONS if x.startswith(cur)]

    op = words[0]
    if op not in COMMAND_OPTIONS:
        return []
    if op == "explain":
        inner = [word for word in words[1:] if word != "--json" and not word.startswith("--json=")]
        if not inner:
            return [name for name in OPERATIONS if name not in {"explain", "recover"}]
        return completion_candidates(inner)
    cur = words[-1]
    prev = words[-2] if len(words) >= 2 else ""
    before_current = words[1:-1]
    refresh_remote = cur.endswith("**")
    prefix = cur[:-2] if refresh_remote else cur

    # Zsh commonly keeps optional/long option values attached (for example
    # ``--yazi=ou``). Return complete tokens in that form so compadd can
    # replace the current shell word without guessing where the value starts.
    if "=" in prefix and prefix.startswith("--"):
        opt, value_prefix = prefix.split("=", 1)
        attached: list[str] = []
        if opt == "--format":
            attached = [x for x in FORMATS if x.startswith(value_prefix)]
        elif opt == "--backend":
            attached = [x for x in BACKEND_NAMES if shutil.which(x) and x.startswith(value_prefix)]
        elif opt == "--profile":
            attached = [x for x in profile_names(load_config()) if x.startswith(value_prefix)]
        elif opt == "--progress":
            attached = [x for x in ["auto", "always", "never"] if x.startswith(value_prefix)]
        elif opt == "--verify-level":
            attached = [x for x in ["none", "structure", "members", "full"] if x.startswith(value_prefix)]
        elif opt == "--json":
            attached = [x for x in ["legacy", "v1"] if x.startswith(value_prefix)]
        elif opt == "--yazi":
            allowed = {
                "identify": ["auto", "archive"],
                "extract": ["auto", "archive", "output"],
                "create": ["auto", "inputs"],
                "add": ["auto", "inputs"],
                "update": ["auto", "inputs"],
                "list": ["auto", "archive"],
                "test": ["auto", "archive"],
                "remove": ["auto", "archive"],
                "convert": ["auto", "inputs"],
            }.get(op, [])
            attached = [x for x in allowed if x.startswith(value_prefix)]
        elif opt == "--level":
            attached = [str(x) for x in range(10) if str(x).startswith(value_prefix)]
        elif opt in {"--password-env", "--source-password-env"}:
            attached = sorted(k for k in os.environ if k.startswith(value_prefix))
        elif opt == "--show-native":
            attached = [x for x in ["before", "after", "both"] if x.startswith(value_prefix)]
        elif opt == "--native-style":
            attached = [x for x in ["exact", "reproducible"] if x.startswith(value_prefix)]
        elif opt == "--execution":
            attached = [x for x in ["auto", "local", "remote"] if x.startswith(value_prefix)]
        elif opt == "--output":
            attached = _path_candidates(value_prefix, op=op, refresh=refresh_remote, dirs_only=True)
        if attached:
            return [f"{opt}={value}" for value in attached]

    if prev in {"--format", "-F"}:
        return [x for x in FORMATS if x.startswith(prefix)]
    if prev == "--backend":
        return [x for x in BACKEND_NAMES if shutil.which(x) and x.startswith(prefix)]
    if prev == "--profile":
        return [x for x in profile_names(load_config()) if x.startswith(prefix)]
    if prev == "--progress":
        return [x for x in ["auto", "always", "never"] if x.startswith(prefix)]
    if prev == "--verify-level":
        return [x for x in ["none", "structure", "members", "full"] if x.startswith(prefix)]
    if prev == "--yazi":
        allowed = {
            "identify": ["auto", "archive"],
            "extract": ["auto", "archive", "output"],
            "create": ["auto", "inputs"],
            "add": ["auto", "inputs"],
            "update": ["auto", "inputs"],
            "list": ["auto", "archive"],
            "test": ["auto", "archive"],
            "remove": ["auto", "archive"],
        }.get(op, [])
        return [x for x in allowed if x.startswith(prefix)]
    if prev == "--level":
        return [str(x) for x in range(10) if str(x).startswith(prefix)]
    if prev in {"--password-env", "--source-password-env"}:
        return sorted(k for k in os.environ if k.startswith(prefix))
    if prev == "--show-native":
        return [x for x in ["before", "after", "both"] if x.startswith(prefix)]
    if prev == "--native-style":
        return [x for x in ["exact", "reproducible"] if x.startswith(prefix)]
    if prev == "--execution":
        return [x for x in ["auto", "local", "remote"] if x.startswith(prefix)]
    if prev == "--remote":
        config = load_config()
        return [x for x in configured_remote_names(config) if x.startswith(prefix)]
    if prev == "--source":
        return _path_candidates(prefix, op=op, refresh=refresh_remote, dirs_only=True)
    if prev in {"-o", "--output"}:
        return _path_candidates(prefix, op=op, refresh=refresh_remote, dirs_only=True)
    if prev in {"--password-file", "--source-password-file", "--exclude-from", "--include-from"}:
        return [x for x in rg_files() if x.startswith(prefix)]
    if cur.startswith("-"):
        return _option_candidates(op, prefix, before_current)

    pos = _positionals(before_current)
    if op == "schema":
        return [x for x in ["machine-v1", "backend-capability-v1", "verification-evidence-v1"] if x.startswith(prefix)]
    if op in {"extract", "list", "remove"}:
        if not pos:
            return _path_candidates(prefix, op=op, refresh=refresh_remote)
        if parse_remote(pos[0], load_config(), probe_rclone=True):
            return []
        return [x for x in _archive_members(pos[0]) if x.startswith(prefix)]
    if op in {"create", "add", "update", "convert"}:
        if not pos:
            return _path_candidates(prefix, op=op, refresh=refresh_remote)
        return _path_candidates(prefix, op=op, refresh=refresh_remote)
    if op in {"identify", "test", "info"}:
        return _path_candidates(prefix, op=op, refresh=refresh_remote)
    if op == "convert":
        return _path_candidates(prefix, op=op, refresh=refresh_remote)
    if op in {"man", "help"}:
        topics = ["arc", *COMMAND_DOCS, "remote", "config"]
        topics.extend(sorted(EXECUTABLE_ALIASES))
        return [x for x in sorted(set(topics)) if x.startswith(prefix)]
    if op == "completion":
        return [x for x in ["zsh", "cache", "refresh", "clear-cache"] if x.startswith(prefix)]
    return []


def completion_mode(words: list[str]) -> str:
    """Tell shell/fzf completion whether a context naturally accepts many values."""
    if not words or len(words) == 1:
        return "single"
    op = words[0]
    prev = words[-2] if len(words) >= 2 else ""
    if prev in VALUE_OPTIONS or prev in OPTIONAL_VALUE_OPTIONS:
        return "single"
    cur = words[-1]
    if cur.startswith("-"):
        return "single"
    pos = _positionals(words[1:-1])
    if op in {"identify", "info"}:
        return "multi"
    if op in {"create", "add", "update", "convert"}:
        return "multi" if pos else "single"
    if op in {"extract", "list", "remove"}:
        return "multi" if pos else "single"
    return "single"


def encode_candidates_nul(candidates: list[str]) -> bytes:
    if not candidates:
        return b""
    return b"\0".join(os.fsencode(x) for x in candidates) + b"\0"


def zsh_completion() -> str:
    # Keep the shell layer deliberately thin. Python owns the command grammar;
    # Zsh owns presentation and optional fzf selection. Executable aliases are
    # derived from the same metadata used by the dispatcher so completion does
    # not silently treat an alias's first operand as an Arc subcommand.
    aliases = " ".join(["arc", *sorted(EXECUTABLE_ALIASES)])
    cases = "\n".join(
        f"    {alias}) print -r -- {command} ;;"
        for alias, command in sorted(EXECUTABLE_ALIASES.items())
    )
    template = r'''#compdef __ALIASES__
# Generated by: arc completion zsh
# Context-aware native Zsh completion. Dynamic candidate transport is NUL-framed.

_arc_implied_op() {
  case "${words[1]}" in
    arc) print -r -- "${words[2]}" ;;
__ALIAS_CASES__
    *) print -r -- "${words[2]}" ;;
  esac
}

_arc_query_words() {
  local op="$(_arc_implied_op)"
  if [[ "${words[1]}" == arc ]]; then
    reply=("${words[2,-1]}")
  else
    reply=("$op" "${words[2,-1]}")
  fi
}

_arc_dynamic_candidates() {
  reply=()
  local item
  local -a query
  _arc_query_words
  query=("${reply[@]}")
  reply=()
  while IFS= read -r -d '' item; do
    reply+=("$item")
  done < <(arc __complete0 -- "${query[@]}" 2>/dev/null)
}

_arc_dynamic_mode() {
  local -a query
  _arc_query_words
  query=("${reply[@]}")
  arc __complete-mode -- "${query[@]}" 2>/dev/null
}

_arc_fuzzy_complete() {
  local mode item
  local -a selected fzf_args query_words
  mode="$(_arc_dynamic_mode)"
  fzf_args=(--read0 --print0 --height=80% --border --reverse --prompt='arc> ')
  [[ "$mode" == multi ]] && fzf_args+=(--multi)
  _arc_query_words
  query_words=("${reply[@]}")
  while true; do
    selected=()
    while IFS= read -r -d '' item; do
      selected+=("$item")
    done < <(arc __complete0 -- "${query_words[@]}" 2>/dev/null | fzf "${fzf_args[@]}")
    if [[ "$mode" == single && ${#selected} -eq 1 && "${selected[1]}" == */ ]]; then
      query_words[-1]="${selected[1]}**"
      continue
    fi
    break
  done
  (( ${#selected} )) && compadd -Q -- "${selected[@]}"
}

_arc() {
  local cur="${words[CURRENT]}"
  local prev="${words[CURRENT-1]}"
  local op="$(_arc_implied_op)"
  local -a reply

  if [[ "$cur" == *'**' ]] && (( $+commands[fzf] )); then
    _arc_fuzzy_complete
    return
  fi

  if [[ "${words[1]}" == arc && CURRENT == 2 ]]; then
    _values 'arc operation' identify list extract create info test convert add update remove backends formats profiles man help completion
    return
  fi

  case "$prev" in
    --format|-F) _values 'archive format' tar tar.gz tar.bz2 tar.xz tar.zstd zip 7z rar gzip bzip2 xz zstd; return ;;
    --backend|--profile|--progress|--yazi|--level|--password-env|--source-password-env|--show-native|--native-style|--execution|--remote)
      _arc_dynamic_candidates; compadd -Q -a reply; return ;;
    -o|--output) _arc_dynamic_candidates; compadd -Q -a reply; return ;;
    --password-file|--source-password-file|--exclude-from|--include-from) _files; return ;;
  esac

  if [[ "$cur" == -* ]]; then
    _arc_dynamic_candidates
    compadd -Q -a reply
    return
  fi

  _arc_dynamic_candidates
  if (( ${#reply} )); then
    compadd -Q -a reply
  else
    _files
  fi
}

compdef _arc __ALIASES__
'''
    return template.replace("__ALIASES__", aliases).replace("__ALIAS_CASES__", cases)

