from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import load_config, profile_names
from .interactive import filesystem_candidates, rg_files
from .remote import complete_remote, configured_remote_names, parse_remote

OPERATIONS = ["identify", "list", "extract", "create", "add", "update", "remove", "test", "backends", "formats", "completion"]
FORMATS = ["tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zstd", "zip", "7z", "rar", "gzip", "bzip2", "xz", "zstd"]
BACKEND_NAMES = ["tar", "bsdtar", "7z", "7zz", "zip", "unzip", "rar", "unrar", "gzip", "pigz", "bzip2", "pbzip2", "xz", "pixz", "zstd", "pzstd"]

VALUE_OPTIONS = {
    "--format", "-F", "--backend", "-o", "--output", "--level", "--threads", "--exclude", "--include",
    "--exclude-from", "--include-from", "--progress", "--password-file", "--password-env", "--profile",
    "--show-native", "--native-style", "--execution",
}
OPTIONAL_VALUE_OPTIONS = {"--password", "--yazi"}

BASE = {
    "--format", "-F", "--backend", "--no-fallback", "--profile", "--dry-run", "--show-command", "-q", "--quiet", "-v", "--verbose",
    "--json", "--progress", "--yazi", "--password", "--password-file", "--password-env",
    "--show-native", "--native-style", "--execution",
}
FILTERS = {"--exclude", "--include", "--exclude-from", "--include-from"}
CREATE = {"--level", "--threads", "--add-extension", "--follow-symlinks", "--one-file-system", "--preserve-owner", "--preserve-acls", "--preserve-xattrs"}
EXTRACT = {"-o", "--output", "--overwrite", "--skip-existing", "--rename-existing", "--unsafe-paths", "--stdout", "--preserve-owner", "--preserve-acls", "--preserve-xattrs"}

COMMAND_OPTIONS: dict[str, set[str]] = {
    "identify": {"--format", "-F", "--json", "--yazi"},
    "list": BASE | FILTERS,
    "test": BASE | FILTERS,
    "extract": BASE | FILTERS | EXTRACT,
    "create": BASE | FILTERS | CREATE | {"--overwrite"},
    "add": BASE | FILTERS | CREATE,
    "update": BASE | FILTERS | CREATE,
    "remove": BASE,
    "backends": {"--json", "--remote"},
    "formats": {"--json", "--remote"},
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


def _option_candidates(op: str, prefix: str) -> list[str]:
    return sorted(x for x in COMMAND_OPTIONS.get(op, set()) if x.startswith(prefix))


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
                archives_only=op in {"identify", "list", "test", "extract", "remove"},
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
            }.get(op, [])
            attached = [x for x in allowed if x.startswith(value_prefix)]
        elif opt == "--level":
            attached = [str(x) for x in range(10) if str(x).startswith(value_prefix)]
        elif opt == "--password-env":
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
    if prev == "--password-env":
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
    if prev in {"-o", "--output"}:
        return _path_candidates(prefix, op=op, refresh=refresh_remote, dirs_only=True)
    if prev in {"--password-file", "--exclude-from", "--include-from"}:
        return [x for x in rg_files() if x.startswith(prefix)]
    if cur.startswith("-"):
        return _option_candidates(op, prefix)

    pos = _positionals(before_current)
    if op in {"extract", "list", "remove"}:
        if not pos:
            return _path_candidates(prefix, op=op, refresh=refresh_remote)
        if parse_remote(pos[0], load_config(), probe_rclone=True):
            return []
        return [x for x in _archive_members(pos[0]) if x.startswith(prefix)]
    if op in {"create", "add", "update"}:
        if not pos:
            return _path_candidates(prefix, op=op, refresh=refresh_remote)
        return _path_candidates(prefix, op=op, refresh=refresh_remote)
    if op in {"identify", "test"}:
        return _path_candidates(prefix, op=op, refresh=refresh_remote)
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
    if op == "identify":
        return "multi"
    if op in {"create", "add", "update"}:
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
    # Zsh owns presentation and optional fzf selection.
    return r'''#compdef arc
# Generated by: arc completion zsh
# Context-aware native Zsh completion. Dynamic candidate transport is NUL-framed.

_arc_dynamic_candidates() {
  reply=()
  local item
  while IFS= read -r -d '' item; do
    reply+=("$item")
  done < <(arc __complete0 -- "${words[2,-1]}" 2>/dev/null)
}

_arc_dynamic_mode() {
  arc __complete-mode -- "${words[2,-1]}" 2>/dev/null
}

_arc_fuzzy_complete() {
  local mode item
  local -a selected fzf_args query_words
  mode="$(_arc_dynamic_mode)"
  fzf_args=(--read0 --print0 --height=80% --border --reverse --prompt='arc> ')
  [[ "$mode" == multi ]] && fzf_args+=(--multi)
  query_words=("${words[2,-1]}")
  while true; do
    selected=()
    while IFS= read -r -d '' item; do
      selected+=("$item")
    done < <(arc __complete0 -- "${query_words[@]}" 2>/dev/null | fzf "${fzf_args[@]}")
    # In single-value remote/archive contexts, choosing a directory keeps the
    # same fzf session alive and descends into it. Multi-value create inputs
    # intentionally accept directories as selections instead.
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
  local op="${words[2]}"
  local -a reply

  if [[ "$cur" == *'**' ]] && (( $+commands[fzf] )); then
    _arc_fuzzy_complete
    return
  fi

  if (( CURRENT == 2 )); then
    _values 'arc operation' identify list extract create add update remove test backends formats completion
    return
  fi

  case "$prev" in
    --format|-F) _values 'archive format' tar tar.gz tar.bz2 tar.xz tar.zstd zip 7z rar gzip bzip2 xz zstd; return ;;
    --backend|--profile|--progress|--yazi|--level|--password-env|--show-native|--native-style|--execution|--remote)
      _arc_dynamic_candidates; compadd -Q -a reply; return ;;
    -o|--output) _arc_dynamic_candidates; compadd -Q -a reply; return ;;
    --password-file|--exclude-from|--include-from) _files; return ;;
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

compdef _arc arc
'''
