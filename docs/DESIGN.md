# arc-cli design contract

## Purpose

`arc` is a normalized archive frontend. It deliberately does not reimplement compression codecs. Python owns predictable cross-backend semantics; installed native tools perform archive encoding, compression, decompression, testing, and mutation.

```text
normalized CLI -> format detection -> manifest/safety layer -> backend resolver -> native binary
```

Arguments before `--` belong to `arc`. Arguments after the first `--` are appended unchanged to the selected primary native backend.

## Format policy

For existing files, an explicit `--format` wins. Otherwise content signatures are authoritative and the extension is only a fallback/hint. gzip, bzip2, xz, and zstd streams are probed to distinguish a compressed TAR from a single compressed stream. Standard Zstandard frames and pzstd/skippable-frame prefixes are recognized.

For creation, an explicit format selector wins; otherwise the longest known output suffix determines the format. `-F/--format` keeps exact-path semantics unless `--add-extension` is requested. Create-only single-dash suffix selectors such as `-zip`, `-tgz`, `-tarzst`, and `-gzip` are authoritative and append the selector's exact suffix only when the requested destination has no recognized archive suffix. The special stdout destination `-` is never rewritten. An unknown/no suffix without an explicit selector is an error.

Internally TAR container and compression are represented separately, e.g. `container=tar`, `compression=zstd`.

## Normalized operations

- `identify`
- `list`
- `extract`
- `create`
- `add`
- `update`
- `remove`
- `test`
- `backends`
- `formats`
- `completion zsh`

Single-stream formats (`gzip`, `bzip2`, `xz`, `zstd`) support create/extract/test and intentionally do not pretend to have archive members.

## File selection and exclusions

Creation walks inputs in Python and creates an explicit manifest before invoking a backend. This prevents backend-specific recursive wildcard semantics from changing the selected file set.

Rules are ordered. The last matching `--exclude`, `--include`, `--exclude-from`, or `--include-from` rule wins. Patterns operate on normalized archive member names using `/` separators.

Native archivers receive explicit manifests with recursion disabled where necessary. This preserves empty directories while preventing a directory argument from reintroducing excluded descendants.

## Extraction safety

Safe extraction is the default. Member paths are checked for absolute paths, drive/UNC paths, parent traversal, unsafe symlink/hardlink targets, special objects, and descendants routed through symlink members. Existing destination symlink parents are rejected as well.

ZIP and TAR families are inspected with Python metadata readers; zstd-compressed TAR metadata is streamed through a zstd decoder when the Python runtime lacks native zstd support. 7-Zip structured listing is used for 7z-compatible containers, including symbolic/hard-link metadata when exposed.

`--unsafe-paths` is the explicit escape hatch.

## Backends

Backends are selected from operation + format + configured preference + installed executables. Environment overrides use `ARC_BACKEND_<ROLE>`, followed by TOML configuration and built-in defaults.

Known families:

- `tar` / `bsdtar`
- `zip` / `unzip`
- `7z` / `7zz`
- `rar` / `unrar`
- `gzip` / `pigz`
- `bzip2` / `pbzip2`
- `xz` / `pixz`
- `zstd` / `pzstd`

Compressed TAR creation uses a pipe (`tar -> compressor`) so normalized compression level/thread settings do not depend on a particular tar implementation's compression flags. Reading compressed TAR uses the inverse pipe where appropriate.

## Progress/UI

Rich is the primary UI dependency. Human-facing status and progress go to stderr; data and JSON stay on stdout. Interactive archive operations echo the Arc argv received by the process before work starts; archive-password values are redacted. The invocation is emitted as one logical soft-wrapped line so narrow terminals do not inject copy-breaking continuation lines. The UI does not claim to reconstruct shell quoting or pre-expansion `~`/glob syntax that no longer exists after the shell has built argv.

Creation shows a live scan status before native archiving plus a resolved create header with destination, selected size, format, and backend chain. Create/extract/test/add/update/remove operations use member/byte-aware Rich progress where backend telemetry permits it. Progress columns are width-aware: narrow terminals omit secondary speed/current-member/ETA columns, and ultra-narrow terminals also omit elapsed time before allowing core progress to wrap. Scan status uses a compact width-aware form. Native percentages/member lines are consumed when available; the wrapper does not invent an in-progress percentage when a backend cannot report one. Completion is set to 100% only after a successful child exit.

After a successful local create, Arc reports source payload bytes, final archive bytes, final size as a percentage of the original, percentage saved (or larger), and original-to-archive ratio on separate summary rows. JSON create output exposes the same metrics numerically.

`--json` disables animated progress. `--progress=auto|always|never` controls progress explicitly.

## Interactive selection

There is intentionally no `--fzf` switch. When a required chooser-like operand is missing on a TTY, `arc` automatically uses fzf if available:

- missing archive for extract/list/test/remove
- missing create inputs
- missing remove member list
- identify with no files

Filesystem candidates use `rg --files --no-config --hidden` when ripgrep exists, with Python fallback. Directory candidates include empty directories.

Yazi is explicit through `--yazi`, `--yazi=archive`, `--yazi=inputs`, and `--yazi=output`, using Yazi's chooser-file interface.

## Zsh completion

`arc completion zsh` generates a native `#compdef arc` completion function. It understands commands, normalized options, formats, installed backends, files/directories, and archive member names through the hidden `arc __complete` protocol.

When the current token ends in `**` and fzf is installed, the generated Zsh completion sends context-aware candidates through fzf multi-selection. Filesystem candidate generation uses ripgrep through the Python completion engine where available.

## Atomicity and interruption

Fresh archive creation is written to a temporary file on the destination filesystem. On success it is fsynced and atomically renamed into place. Existing destinations require `--overwrite`.

Child processes are terminated on interruption/error, temporary manifests/archives are cleaned, and `Ctrl+C` maps to exit code 130. `--rename-existing` extraction renames are restored if extraction subsequently fails.

## Metadata

Ordinary archive metadata and symlinks are preserved where the backend naturally supports them. Following symlinks is opt-in with `--follow-symlinks`. `--one-file-system` constrains Python traversal.

Ownership, ACL and xattr preservation is explicit and currently normalized only for TAR/bsdtar-compatible backends:

- `--preserve-owner`
- `--preserve-acls`
- `--preserve-xattrs`

Special source objects such as FIFOs/devices are rejected by default.

## Passwords

Password sources are mutually exclusive:

- `--password VALUE`
- `--password` with a secure TTY prompt
- `--password-file FILE`
- `--password-env NAME`

Secrets are redacted from `--show-command`. Backend error text is inspected to map recognizable wrong-password failures to exit code 6.

## Streams

`create - --format ...` writes an archive/compressed stream to stdout through a temporary validated output. Reading archive data from stdin requires `--format`. `extract ... MEMBER --stdout` emits one member to stdout. Stream-compressed stdin without an intrinsic filename requires `--stdout`.

## Exit codes

- 0 success
- 1 generic/backend failure
- 2 usage error
- 3 unsupported/unknown format
- 4 backend unavailable
- 5 corrupt archive/test failure
- 6 password failure
- 7 unsafe archive
- 8 conflict
- 9 partial failure (reserved)
- 130 interrupted
- 141 broken pipe
