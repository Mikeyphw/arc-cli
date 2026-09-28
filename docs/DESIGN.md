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

## Remote capability authority

Remote transport policy is typed independently from archive-backend capability policy. `arc.remote-capability/v1` records SSH/rclone locality, whether reads/writes stream or stage, publication/finalization semantics, and probe provenance. SSH capability discovery includes the R08 typed remote Arc backend inventory in the same probe round trip. Capability cache records are generation-scoped and expose source, age, and TTL. Dry-run planning does not perform a network probe merely to strengthen a guarantee.

Publication terminology is evidence-bound: local publication uses same-filesystem `os.replace`; SSH uses a same-parent temporary file plus `mv` and claims atomic rename only after probing its required tools; rclone uses a temporary object plus `moveto` but remains provider-dependent because server-side move support does not establish atomic replacement semantics.

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


## Convert, info, executable aliases, and manual ownership

`convert` is a normalized Arc operation, not a shell macro around extract/create. It resolves source and target formats up front, preflights all batch destinations, validates archive member paths before materializing or TAR-stream recompression, isolates container transformations, and only then permits `--replace-source`. Source and destination passwords are separate typed inputs. Compatible local single-stream and TAR transformations use pipelines where that preserves the same safety contract; container conversion uses Arc's existing extraction/create semantics instead of backend-specific shortcuts. Local output is always an unpublished same-filesystem candidate until integrity verification succeeds, then `os.replace` publishes it atomically. Remote conversion intentionally stages: source staging preserves content-first classification/safety/physical-size evidence, destination staging permits pre-upload verification, and the published remote object is re-read and verified before source removal. Diagnostics label this `transport-staged` rather than claiming a direct remote pipeline.

`info` is intentionally distinct from `list` and `test`: it summarizes metadata, member counts, physical/logical size evidence, format-vs-extension agreement, technical properties and available conversion targets. `verified` is tri-state and remains `null` until an integrity check actually executes. Header-encrypted metadata failure is treated as a limited-information state unless verification was requested.

Installed executable aliases are entry points into the one CLI dispatcher. `command_docs.py` is the shared command/alias identity model used by parser help, dispatch, completion, man-topic resolution and package parity tests. The invocation UI retains the actual executable name received in `argv[0]`; alias `--json` responses additionally expose the redacted literal `invocation` and canonical `resolved_command` without changing direct `arc ... --json` schemas.

`manual.py` is the structured detailed-documentation model layered on that command identity. `scripts/generate_command_docs.py` renders its closed manpage inventory plus `docs/COMMAND_REFERENCE.md`, and `--check` makes stale or unexpected generated pages a validation failure. Manual pages are shipped both as package data (for `arc man` / `arc help` fallback) and standard `share/man` data files; the sdist retains the generator, Markdown reference, and man sources so rebuilding from source does not lose the documentation workflow. `--help` remains concise while the manuals own detailed behavior, edge cases, safety and examples. Zsh completion is generated from the same executable-alias map, so invoking completion through `arci`, `arccv`, etc. first restores the implied canonical operation before asking the Python completion engine for dynamic candidates.

## R06 runtime, alias, and installation truth

Arc now treats repository/package/runtime agreement as an explicit product boundary. `command_docs.py` owns a typed executable-alias registry; the package `[project.scripts]` table and Devtool `python.package.console_scripts` list are generated/checked consumers of that registry rather than independent alias authorities. Dispatch, completion, generated manuals, packaging tests, `arc aliases`, and `arc doctor` consume the same canonical identity.

`arc doctor` deliberately distinguishes three truths that can drift independently: the source checkout, installed distribution metadata, and executables actually available on `PATH`. A console alias can therefore be reported as declared-but-missing instead of being assumed active because it exists in `pyproject.toml`. Configuration parsing failures are surfaced by doctor rather than being conflated with an absent config, while ordinary command compatibility keeps the tolerant `load_config()` behavior.

Development installation refresh is a lifecycle action, not test orchestration. `scripts/refresh_dev_install.py` can regenerate/check derived metadata surfaces and refresh an editable install with the current Python interpreter. Devtool exposes that through a target-local `refresh-install` job/workflow; R06 qualification itself remains a first-class test/workflow and does not mutate the caller's Python installation. Artifact apply may invoke the refresh lifecycle after source promotion so the persistent editable install points at the stable checkout instead of a disposable transaction worktree.


## R07 explainable execution and durable recovery

`arc explain` executes the selected command through the same parser/default/profile/backend resolution and dry-run path used by the real command. The execution plan now carries typed decisions alongside native stages: format/backend selection, strategy, publication locality, verification scope, overwrite policy, and source-removal ordering. Explain never creates a transaction journal and must not publish local or remote data.

Mutating create/add/update/remove/convert operations create schema-versioned JSON journals under `$ARC_STATE_HOME/transactions`, `$XDG_STATE_HOME/arc/transactions`, or `~/.local/state/arc/transactions`. Journals are append-like phase evidence around the existing mutation path; they do not pretend Arc can automatically reverse arbitrary backend in-place mutations. `arc recover --cleanup` therefore removes only exact temporary paths registered as Arc-owned by the transaction and never rolls back an already-published destination automatically.

Batch conversion stores a separate durable manifest under the Arc state directory. A deterministic batch identity is derived from source/destination topology and conversion policy. `--resume` reuses an item only when the prior item completed verification, the current local source fingerprint still matches (or the source was deliberately removed after verified publication), and the destination fingerprint still matches. A changed source, missing/tampered output, or unproven remote identity invalidates only that item.
## R08 machine, capability, and verification authority

Arc now has one typed authority for three surfaces that previously drifted independently. `BackendCapabilityProfile` describes the normalized behavior Arc is willing to rely on; planners and runtime verification consume it, while legacy string capabilities are only a compatibility projection. `VerificationEvidence` records requested/achieved proof, downgrade status, backend and concrete checks.

Bare `--json` remains intentionally command-specific for backward compatibility. New automation should request `--json=v1`, which wraps command results in `arc.machine/v1` with a redacted received invocation, canonical command identity, typed errors and structured diagnostics. Native-plan diagnostics are embedded in that envelope rather than emitted as a second machine record. The public schemas are package resources and are verified through wheel -> sdist -> rebuilt-wheel packaging tests. R09A extends the registry additively with logical-fingerprint and archive-diff schemas without changing the R08 machine envelope.

Verification policy is fail-closed: an unavailable requested level fails unless the caller explicitly authorizes downgrade. `none` means no proof and is never accepted as evidence for source deletion or resumable batch reuse. Stronger backend proof may satisfy a weaker request and is recorded as the achieved level.


## R09A logical provenance and equivalence authority

`arc info --fingerprint`, `arc diff`, and `arc convert --prove-equivalent` consume one provenance model rather than implementing format-specific comparisons. `arc-logical-members-v1` normalizes Unicode member paths, separators, safe internal parent traversal, member kinds, file sizes/content SHA-256, link targets, and meaningful empty directories. Non-empty parent-directory records are representation details and are omitted when child paths imply them. Normalized path collisions fail before extraction so two archive entries cannot materialize to one filesystem path before provenance is evaluated.

The logical digest deliberately excludes timestamp metadata. A separate metadata digest tracks the currently selected metadata surface, while each archive retains its encoded byte SHA-256, size, format, backend, and source label. `arc.archive-diff/v1` therefore distinguishes logical equivalence, metadata equivalence, byte identity, format changes, and encoding-only differences without conflating those claims. `encoding_only` requires both logical and selected metadata equivalence.

Conversion equivalence proof reuses this exact authority. For local conversion, the readable source and unpublished same-filesystem destination candidate are fingerprinted before `os.replace`; a mismatch prevents publication. Remote output is additionally re-read and fingerprinted after transport before Arc reports the proof. This is an assertion of semantic preservation, so intentional content-changing filters correctly fail `--prove-equivalent` rather than being silently treated as equivalent. Remote transport capability/atomicity negotiation remains owned by R09B.
