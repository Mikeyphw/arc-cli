# arc-cli

`arc` is a normalized Python archive CLI over native `tar`, `zip`/`unzip`, `7z`/`7zz`, `rar`/`unrar`, `gzip`, `bzip2`, `xz`, and `zstd`-family binaries.

The wrapper owns format detection, safe extraction, consistent filtering, overwrite policy, backend resolution, Rich progress, fzf/Yazi selection, JSON output, and shell completion. The native programs perform the actual archive/compression work.

Everything after the first `--` is passed unchanged to the selected primary backend.

### Merge archives and streams

```sh
arc merge a.tar b.tar -o combined.tar
arc merge one.gz two.gz -o combined.gz
arc merge old.zip new.7z -F tar.zstd --member-conflict rename
```

`--strategy=auto` concatenates only compatible same-format single compressed streams. Container archives and compound TAR+compression formats are logically repacked.

### Split and join exact byte volumes

```sh
arc split backup.tar.zst --size 100MiB
arc split image.iso --parts 4 --output-dir ./parts
arc join backup.tar.zst.part001
```

`arc split` is format-agnostic exact byte splitting, not native 7z/RAR multipart creation. It emits `arc.split-manifest/v1` by default with whole-file and per-part checksums. `arc join` discovers that sibling manifest from any generated part, validates the ordered set, reconstructs through a transaction-owned candidate, proves the whole-file checksum, and only then publishes.

## Install

```bash
python -m pip install -e .
```

Or run the development launcher directly:

```bash
python arc.py --help
```

Python 3.11+ and `rich` are required. Native backends are discovered at runtime. `fzf`, `rg`, and `yazi` are optional integrations.

## Common commands

```bash
arc identify mystery.bin
arc list backup.tar.zst
arc extract backup.zip -o restored/
arc info backup.zip
arc convert backup.zip -tzst
arc create backup.tar.zst src/ docs/ --exclude '.git/' --exclude '*.pyc' --level 8 --threads 4
arc add files.zip new-file.txt
arc update files.7z changed/
arc remove plain.tar old/path.txt
arc test backup.zip
arc backends
arc formats
```

Backend-specific arguments belong after `--`:

```bash
arc extract backup.tar.gz -o out -- --no-same-permissions
arc create photos.7z Photos/ -- --m0=lzma2
```

## Automatic format detection

Existing archives are detected from content first. Extensions are only hints/fallbacks, so renamed archives still work:

```bash
mv archive.7z mystery.data
arc identify mystery.data
```

Compressed streams are probed to distinguish a single compressed file from compressed TAR, including standard Zstandard and pzstd/skippable-frame files.

Creation infers format from the full output suffix:

```bash
arc create backup.tar.gz src/
arc create backup.tar.zst src/
arc create backup.7z src/
```

Without a known suffix, specify the format:

```bash
arc create backup --format zip src/
```

This creates exactly `backup`. Add `--add-extension` if you want `arc` to append the canonical suffix.

For interactive create commands, single-dash suffix shortcuts select the format explicitly and append that exact suffix when the archive name has no recognized archive suffix:

```bash
arc create backup src/ -zip          # -> backup.zip
arc create backup src/ -tarzst       # -> backup.tar.zst
arc create backup src/ -tgz          # -> backup.tgz
arc create payload file.bin -zst     # -> payload.zst
arc create payload file.bin -gzip    # -> payload.gzip
```

The stdout sentinel stays special, so `arc create - src/ -zip` selects ZIP without rewriting `-` to a filename. The selector is authoritative. For example, `arc create odd.zip src/ -tarzst` creates a tar+zstd archive named `odd.zip` and emits the usual extension-mismatch warning instead of inferring ZIP from the filename. `-F/--format` and a suffix shortcut are mutually exclusive. Run `arc create --help` for the full shortcut set.


## Archive information

`arc info` is the fast summary/metadata surface; `arc list` enumerates members and `arc test` performs integrity verification. `info` never turns "not checked" into a failed or successful integrity claim:

```bash
arc info backup.7z
arc info backup.zip --members
arc info backup.zip --technical
arc info backup.zip --verify
arc info backup.zip --fingerprint
arc info a.zip b.tar.zst --json
```

The JSON form reports `verified: null` unless verification actually runs. `--verify-level none|structure|members|full` selects the requested proof; `--allow-verification-downgrade` is required before Arc may accept a weaker backend proof, and the result records both requested and achieved levels. Content detection is compared with the filename extension, so a ZIP renamed to `.rar` is reported as ZIP with an extension mismatch. Header-encrypted archives retain whatever outer metadata Arc can prove even when member metadata requires a password. `--fingerprint` adds a format-independent logical content digest, a separate selected-metadata digest, and the encoded archive byte SHA-256. Remote info uses the same SSH/rclone transport layer and reports whether random-access inspection required local staging.

## Logical fingerprints and archive diff

`arc diff` compares normalized archive meaning rather than compressed bytes:

```bash
arc diff old.zip new.7z
arcdiff backup.zip backup.tar.zst --json
arc diff private-a.7z private-b.zip \
  --left-password-env OLD_PASS \
  --right-password-env NEW_PASS --json=v1
```

The logical digest includes normalized NFC/member paths, member kind, regular-file size/content SHA-256, link targets, and meaningful empty-directory presence. Safe internal parent traversal is collapsed to its filesystem-equivalent path before hashing, and archive entries that collide after normalization are rejected before extraction. Timestamps are intentionally kept out of that digest and compared separately as selected metadata, so repacking with different timestamps can remain logically equivalent. Non-empty directory entries are normalized away because some formats store parent directories explicitly while others imply them from child paths. Single-stream compressors use the synthetic `@stream` member, allowing equivalent decompressed bytes to compare across gzip/bzip2/xz/zstd encodings.

Diff output classifies `added`, `removed`, `type_changed`, `content_changed`, and `metadata_changed` members. It distinguishes logical equivalence, selected-metadata equivalence, archive-byte identity, container/format changes, and `encoding_only`, which is true only when both logical content and selected metadata match while encoded bytes differ. Remote inputs use Arc's existing read-side transport and are reported as staged for local normalized comparison; native remote comparison/capability negotiation remains R09B. A successful comparison returns success even when archives differ; scripts should inspect the equivalence fields rather than treating a normal difference as an execution failure.

## Conversion

`arc convert` transforms one archive/compressed stream into another without modifying the source by default:

```bash
arc convert archive.zip archive.tar.zst
arc convert archive.zip -tzst
arc convert database.sql.gz -zst
arc convert backup.zip backup.tar.zst --prove-equivalent
arc convert archive.zip -zst --include video.mp4
```

The same authoritative suffix selectors used by `create` are accepted by `convert`. With no destination, Arc removes one recognized source archive suffix and appends the selected target suffix. With an explicit selector, a conflicting destination extension is only a filename; the selector still determines the bytes written.

Compatible local single-stream and TAR recompression paths avoid an unnecessary extracted tree. Other container conversions use an isolated safe member pipeline. Local output is built at an unpublished same-filesystem candidate and evaluated under the selected verification policy before publication. `--verify-level none` deliberately publishes without proof and cannot be combined with `--replace-source`; stronger levels are negotiated against the selected backend's typed capability profile. A weaker proof is accepted only with explicit `--allow-verification-downgrade`. Failed requested verification cannot leave a new corrupt final path or clobber an existing `--force` destination. `--replace-source` removes the source only after verified publication.

`--prove-equivalent` additionally fingerprints the readable source and unpublished destination with the same R09A logical-content engine used by `arc diff`; a logical mismatch blocks local publication. For remote destinations Arc proves the local candidate first and then re-reads/fingerprints the published remote object. Intentional semantic transforms such as member filtering are therefore expected to fail this assertion.

Remote conversion intentionally reports `transport-staged ...` rather than pretending transport staging is a direct stream. A remote source is staged so Arc can retain content-first format classification (including the stream-vs-compressed-TAR distinction), normalized safety checks, and exact physical-size evidence. A remote destination is first produced and verified as a local candidate, then uploaded through Arc's temporary-target/finalize transport path, re-read, and verified again before source deletion is permitted; final rename/moveto atomicity depends on the transport/provider. The SSH/rclone transport layer can stream in other operations, but conversion does not trade those invariants for a lower-staging path.

Source and destination credentials are independent:

```bash
arc convert private.rar rotated.7z -7z \
  --source-password-env OLD_PASS \
  --password-env NEW_PASS
```

Multiple sources with an explicit target are independent batch conversions, not a merged create operation:

```bash
arc convert a.zip b.zip c.zip -tzst
arc convert a.zip b.zip -tzst --batch   # explicit disambiguation when needed
```

Arc preflights batch destination collisions before starting work. `--dry-run` shows the resolved conversion plan without writes/deletes. Human completion output reports Original, Converted, Reduction/Change, Ratio, member count when known, verification backend, and output; `--json` exposes the same physical size/change evidence numerically.

## Installed command aliases

Arc installs real dispatcher commands rather than requiring shell aliases. They preserve the executable name in Arc's displayed invocation while resolving to the same parser/implementation:

```text
arcmk / arc-create / arcpack       -> arc create
arcx  / arc-extract / arcunpack    -> arc extract
arcls / arc-list                   -> arc list
arci  / arc-info                   -> arc info
arcdiff / arc-diff                 -> arc diff
arct  / arc-test / arccheck        -> arc test
arccv / arc-convert / arcconvert   -> arc convert
arca  / arc-add                    -> arc add
arcu  / arc-update                 -> arc update
arcrm / arc-remove                 -> arc remove
arcbe / arc-backends               -> arc backends
arc-formats                        -> arc formats
arcp  / arc-profiles               -> arc profiles
```

`arcc` is intentionally not installed because `c` would be ambiguous between create and convert. Generated Zsh completion is alias-aware and maps each executable back to its canonical Arc command before asking the Python completion engine for candidates. Bare `--json` keeps the pre-R08 compatibility shape (including alias `invocation`/`resolved_command` fields). Explicit `--json=v1` uses the stable machine envelope and preserves the redacted received executable/argv plus canonical command identity.

The alias list is now a typed registry rather than duplicated package metadata. Inspect registry/runtime agreement with:

```bash
arc aliases
arc aliases --missing
arc aliases --json
```

## Runtime and installation health

`arc doctor` distinguishes source truth from installed-runtime truth. In particular, it detects the case where an alias is declared by the repository/package contract but the currently active editable installation has not created that executable on `PATH`:

```bash
arc doctor
arc doctor --json
arc doctor --fix --source ~/Code/arc-cli
```

`--fix` is bounded: it synchronizes the canonical alias registry into package/Devtool metadata, regenerates command docs/completion, checks those surfaces, and refreshes the selected source checkout with `pip install --no-build-isolation --no-deps -e`. It does not silently install native archive backends or rewrite user configuration. Repository development can invoke the same lifecycle through `./devtoolw refresh-install`.

## Manual pages and detailed help

Arc ships Unix manual pages plus an in-package fallback for Termux/minimal systems:

```bash
man arc
man arc-create
man arc-info
man arc-convert

arc man
arc man convert
arc help convert
arc convert --help
```

`--help` stays compact. `arc man`/`arc help` render the detailed product documentation, including semantics, safety, edge cases and examples. Reference pages include `arc-formats(7)`, `arc-backends(7)`, `arc-remote(7)`, `arc-config(5)`, and `arc-profiles(5)`. Command names/aliases/summaries are centralized in `arc_cli.command_docs`; the structured manual model in `arc_cli.manual` consumes that identity model and generates both committed roff pages and `docs/COMMAND_REFERENCE.md`. Regenerate with `python3 scripts/generate_command_docs.py` and use `--check` in validation to fail on drift. Argparse help, alias dispatch, completion, manual-topic resolution, packaging and generated docs therefore share one command identity contract.

## Normalized exclusions and includes

```bash
arc create source.tar.zst project/ \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'build/**' \
  --include 'build/releases/**'
```

Rules are evaluated in command order; the last matching rule wins. Pattern files are supported:

```bash
arc create backup.zip project/ --exclude-from ~/.config/arc/backup.ignore
```

A line beginning with `!` in a rule file re-includes a path. Selection happens in Python before the backend runs, and native backend recursion is disabled for explicit manifests so exclusions are consistent across TAR, ZIP, 7z, and RAR backends.

## Rich progress and create summary

Rich is used for human-facing status and detailed progress on stderr. Interactive commands echo the copyable Arc invocation before work starts as one logical line; the terminal may soft-wrap it visually without Arc inserting continuation newlines. Creation then shows the resolved destination/format/backend and a live scan status that compacts itself on narrow terminals. Progress density adapts to terminal width so phone-sized Termux sessions keep the essential bar, percentage, and file count, adding elapsed time and secondary telemetry only when space permits.

```bash
arc create big.tar.zst data/ --progress always
arc extract big.zip -o out --progress never
```

Successful local creates end with original size, compressed/archive size, percentage of the original size, space saved, and compression ratio. `--json` disables animated progress, keeps stdout machine-readable, and exposes the same create metrics as numeric fields.

## Remote capability and publication truth

`arc backends --remote NAME --json` exposes typed `arc.remote-capability/v1` evidence for SSH/rclone locality, staging requirements, publication semantics, and capability-probe provenance. Fresh cached evidence identifies itself as cache-derived with age/TTL/provider-generation metadata; add `--refresh` to force a live capability probe.

Arc does not treat every remote finalizer as equivalent to local `os.replace`. SSH uploads use a same-parent temporary file and `mv`, and Arc only claims same-filesystem rename atomicity when the required tools were actually proven. rclone uploads use a temporary object plus `moveto`, but publication remains `provider-dependent` even when rclone reports server-side `Move=true`.

## Automatic fzf integration

There is intentionally no `--fzf` switch. When an operand naturally needs choosing and the command is interactive, `arc` automatically invokes fzf if installed:

```bash
arc extract                 # choose archive
arc create backup.7z        # multi-select inputs
arc remove backup.zip       # multi-select members
arc identify                # choose files
```

Filesystem candidates use `rg --files --no-config --hidden --null` when ripgrep exists, with Python fallback. Arc-owned candidate streams are NUL-framed, so spaces, tabs, and newlines in filenames are preserved. Directory choices also include empty directories.

fzf selection is context-aware: archive/backend/format/output choices are single-select, while create inputs and member-oriented operations use multi-select only when the command position accepts multiple values.

## Yazi chooser

Yazi is explicit:

```bash
arc extract --yazi
arc create backup.tar.zst --yazi
arc extract backup.zip --yazi=output
```

Supported chooser roles are `archive`, `inputs`, and `output`. Invalid command/role combinations fail before Yazi launches, and temporary chooser files are removed on success or cancellation. The integration uses Yazi's chooser-file interface. Note that Yazi's external chooser-file protocol is newline-framed; Arc's own fzf/ripgrep/completion protocols remain NUL-framed.

## Full Zsh completion

Generate the native completion function:

```bash
mkdir -p ~/.zfunc
arc completion zsh > ~/.zfunc/_arc
```

Then in `.zshrc`:

```zsh
fpath=(~/.zfunc $fpath)
autoload -Uz compinit
compinit
```

The generated completion has a command-specific option grammar and dynamically completes formats, installed compatible backends, named profiles, files/directories, archive member names, and environment-variable names for `--password-env`. When a token ends in `**` and fzf is installed, candidates are exchanged with fzf using `--read0`/`--print0`; `--multi` is enabled only for multi-value command positions. Filesystem candidates are ripgrep-backed when available.

A generated copy is also included at `completions/_arc`.

### Named profiles

Reusable normalized options can be grouped under `[profiles.<name>]` in `~/.config/arc/config.toml` or the configured Arc TOML file:

```toml
[profiles.backup]
level = 8
threads = 0
exclude = [".git/", "__pycache__/", "*.pyc"]

[profiles.fast]
level = 2
threads = 0
```

Use them with any supported command option surface, for example:

```bash
arc create backup.tar.zst src/ --profile backup
```

Precedence is built-in defaults → config → profile → explicit CLI. Profile filter rules are applied before command-line rules so the CLI remains the final last-match-wins authority.

## SSH and rclone transports

Arc treats SSH and rclone as transport providers around the same normalized archive semantics used locally. Configure SSH aliases in `~/.config/arc/config.toml`:

```toml
[remotes.tablet]
type = "ssh"
host = "192.168.1.20"
user = "u0_a123"
port = 8022
identity_file = "~/.ssh/tablet_ed25519"
```

Then use either the explicit or configured shorthand form:

```bash
arc list ssh://tablet/home/u0_a123/backups/code.7z
arc extract tablet:/home/u0_a123/backups/code.tar.zst -o restored/
arc create tablet:/home/u0_a123/backups/code.tar.zst ~/Code
```

rclone remotes use their normal syntax directly:

```bash
arc list gdrive:Backups/code.zip
arc extract s3:bucket/archive.7z -o restored/
arc create b2:archives/project.tar.zst project/
```

Random-access archive work uses atomic local staging by default. Transfers themselves use `ssh`/`cat` or rclone `cat`/`rcat`; successful SSH and rclone uploads finalize through a temporary remote destination before rename/move.

`--execution=remote` is an explicit SSH-native mode when `arc` is installed on the remote host. It supports `identify`, `list`, `test`, `remove`, same-host `create`/`add`/`update`, remote-to-remote `extract`, and single-member `extract --stdout`. For mutation inputs, every input must resolve to the same SSH endpoint as the archive; for extraction without `--stdout`, `-o/--output` must resolve to that same endpoint. Local paths, cross-host paths, local filter files, and archive credentials are rejected before execution instead of being silently transferred. `auto` remains conservative and uses the staging/streaming model.

Remote files and directories can also be used as create/add/update inputs under the staging model. rclone directory inputs use `rclone copy`; SSH directories stream tar over SSH into local staging.

### Remote completion and fzf cache

Normal `<TAB>` and fuzzy `**<TAB>` completion understand configured SSH remotes and discovered rclone remotes:

```text
tablet:/home/u0_a123/Co**<TAB>
gdrive:Backups/2026/**<TAB>
```

Listings are cached per remote directory. TTL expiry, remote configuration changes, and successful Arc mutations invalidate cache entries automatically. In a single-value fuzzy context, selecting a directory descends inside the same fzf session.

```bash
arc completion cache
arc completion cache --json
arc completion refresh gdrive:Backups/
arc completion clear-cache tablet
```

SSH completion prefers remote Python metadata and falls back to NUL-delimited `find`; rclone uses `lsjson`. Arc-owned candidate transport stays NUL framed.

## Native-command learning

Interactive `Command` output shows the exact redacted native backend stage Arc executes; it never echoes the Arc wrapper invocation as though that were the backend command. `--show-command` remains the explicit immediate backend diagnostic. `--show-native` records the complete execution plan and can display it before, after, or both:

```bash
arc create backup.7z src --level 7 --show-native
arc create gdrive:Backups/project.tar.zst project --show-native=both
arc test tablet:/backups/project.7z --show-native --native-style exact
```

Persistent defaults can be set with:

```toml
[ui]
show_native = "after"
native_command_style = "reproducible"
```

`exact` shows the argv/staging operations Arc really used. `reproducible` hides temporary manifest/staging names so the output is easier to learn from. Password values are always redacted. In legacy bare `--json` mode the native plan remains a separate structured diagnostic on stderr. With `--json=v1`, the same plan is carried inside the envelope's `diagnostics` array so stdout is one self-contained machine record.

## Safe extraction and conflicts

TAR/ZIP members are inspected before extraction. The wrapper rejects path traversal, absolute paths, unsafe links, special objects, archive members descending through symlink entries, and existing destination symlink parents. 7-Zip structured metadata is used for 7z-compatible containers when available.

Use `--unsafe-paths` only for trusted archives when you intentionally need to bypass the wrapper preflight.

Existing files are an error by default:

```bash
--overwrite
--skip-existing
--rename-existing
```

`--rename-existing` renames old targets to `.old.N`; if extraction then fails, those renames are restored.

## Atomic creation

Fresh archive creation is written to a temporary file on the destination filesystem. On success it is fsynced and atomically renamed into place. Existing archive destinations require `--overwrite`.

## Compression level and threads

The wrapper exposes normalized levels `0..9`:

```bash
arc create a.zip src/ --level 9
arc create a.tar.zst src/ --level 8 --threads 4
```

TAR compression is piped through the selected compressor, which lets `arc` apply normalized compressor settings independently of a particular tar implementation. Zstd levels are mapped to its practical native range; RAR levels are mapped to its native `0..5` range.

`--threads 0` means backend automatic/core-count behavior where supported.

## Passwords

Use one password source:

```bash
arc create private.7z docs/ --password
arc extract private.zip --password-file ~/.secrets/archive-pass
arc test private.7z --password-env ARCHIVE_PASSWORD
```

`--password` without a value prompts securely on a TTY. Passwords are redacted from `--show-command`. Recognizable backend wrong-password failures use exit code `6`.

## Metadata and symlinks

Symlinks are preserved as links by default; use `--follow-symlinks` to dereference them. `--one-file-system` prevents traversal across filesystem devices.

Explicit TAR/bsdtar metadata controls:

```bash
--preserve-owner
--preserve-acls
--preserve-xattrs
```

Special source objects such as FIFOs and device nodes are rejected by default.

## stdin/stdout

Create an archive on stdout:

```bash
arc create - --format tar.gz src/ > backup.tar.gz
```

Read an archive from stdin (format required):

```bash
cat backup.tar.gz | arc list - --format tar.gz
```

Extract one member to stdout:

```bash
arc extract backup.zip README.md --stdout > README.md
```

A single compressed stream from stdin has no filename to derive, so extraction from stream-compressed stdin requires `--stdout`.

## JSON and stable machine output

Bare `--json` is the compatibility surface and keeps each command's historical JSON shape:

```bash
arc identify file.bin --json
arc list backup.zip --json
arc test backup.zip --json
arc backends --json
```

Use explicit `--json=v1` when building new automation. It emits one `arc.machine/v1` envelope containing `schema_version`, typed result/error fields, canonical/literal invocation identity, and structured diagnostics:

```bash
arc info backup.zip --verify-level full --json=v1
arc backends --json=v1
arc explain --json=v1 convert backup.zip -tzst
```

The public schemas are bundled with the package:

```bash
arc schema --list
arc schema machine-v1
arc schema backend-capability-v1
arc schema verification-evidence-v1
```

Password values are redacted from invocation/error evidence. Parser failures requested with `--json=v1` are machine errors rather than human argparse output. Animated progress stays disabled in JSON modes.

Verification levels have explicit evidence meanings:

- `none` — perform no archive-integrity proof; the result is recorded as skipped.
- `structure` — prove that the container/member index is readable. For single compressed streams, which have no member index, Arc runs the native stream-integrity test and records the stronger `full` proof instead.
- `members` — prove a readable member index **and** validate Arc's normalized member-safety rules (paths/types). This is not a per-member content checksum.
- `full` — run the selected backend's integrity test that consumes archive/compressed data. It proves the backend accepted the encoded data; it is not a cryptographic proof that two logical archives are equivalent.

An explicit level never silently degrades. If no eligible installed backend can satisfy it, Arc fails before mutation unless `--allow-verification-downgrade` was explicitly supplied; the requested/achieved levels and downgrade reason are then preserved in verification evidence.

## Configuration and environment

Optional TOML config:

```text
$XDG_CONFIG_HOME/arc/config.toml
~/.config/arc/config.toml
```

Example:

```toml
[ui]
progress = "auto"

[create]
level = 6
threads = 0

[backends]
tar = ["bsdtar", "tar"]
zip_extract = ["7zz", "7z", "unzip"]
zip_create = ["7zz", "7z", "zip"]
gzip = ["pigz", "gzip"]
zstd = ["pzstd", "zstd"]
```

Configuration resolution is inspectable and provenance-aware:

```bash
arc config show --effective
arc config explain ui.progress
arc config explain create.level --profile backup --cli level=9
arc config profile backup --json
```

The effective precedence is built-in defaults → TOML → supported environment overrides → selected profile → explicit CLI. `arc config explain KEY` shows every participating layer and the selected source. `arc config show` retains invalid TOML/type/unknown-key diagnostics instead of silently presenting `{}`; `arc doctor` consumes the same diagnostic model. Supported environment overrides include `ARC_PROGRESS`, `ARC_LEVEL`, `ARC_THREADS`, and per-role backend variables such as `ARC_BACKEND_TAR` or `ARC_BACKEND_ZSTD`. Invalid supported environment values are typed configuration failures even when no TOML file exists, while TOML/profile integer fields reject float/boolean coercion instead of silently truncating or converting values. The machine shape is `arc.config-inspection/v1`, available with `arc schema config-inspection-v1`; `--json=v1` wraps it in `arc.machine/v1`.

Backend selection is capability-aware. R08 makes that authority typed: every backend profile describes normalized operations, stdin/stdout, encryption read/write, solid/multipart support, metadata, mutation, random access, thread support, safe indexing, remote suitability, and the verification levels Arc can prove. Legacy capability strings are generated from that profile for compatibility. If the first configured backend cannot satisfy a requested normalized feature (for example `--threads`), Arc tries the next compatible installed backend. Use `--no-fallback` to restrict selection to the first configured preference, or `--backend NAME` for a strict explicit backend.

TAR mutation is capability-dependent: BSD tar is accepted for create/add/update but does not advertise member removal. For `arc remove` on TAR, arc probes concrete `tar` executables for `--delete` support and falls through to a compatible backend when available; otherwise removal is reported as unsupported rather than invoking an invalid BSD-tar command.

Inspect the complete candidate/capability inventory with:

```bash
arc backends --verbose
arc backends --json
arc backends --json=v1
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 1 | generic/backend failure |
| 2 | invalid usage |
| 3 | unsupported/unknown format |
| 4 | backend unavailable |
| 5 | corrupt archive / failed integrity test |
| 6 | password failure |
| 7 | unsafe archive/path |
| 8 | destination/archive conflict |
| 9 | partial failure (reserved) |
| 130 | interrupted |
| 141 | broken pipe |


## ARC-R03 qualification evidence

The repository includes a capability-driven qualification runner for backend/runtime evidence:

```bash
python scripts/run_r03_qualification.py
```

By default it writes machine-readable ledgers under `.devtool/evidence/arc-r03/`:

- `capability-matrix.json` — installed backend candidates, capabilities, and backend × operation resolver coverage.
- `backend-matrix.json` — selected-backend format round trips and mutation coverage.
- `safety-matrix.json` — adversarial extraction checks.
- `resilience-matrix.json` — corruption, password, and large-manifest qualification.
- `summary.json` and `qualification.json` — aggregate status.

Unavailable binaries are reported explicitly as `SKIPPED_BACKEND_UNAVAILABLE` or `SKIPPED_CAPABILITY_UNSUPPORTED`; they are never counted as passing runtime qualification. The ARC final gate consumes these ledgers together with the complete R01–R12 behavioral and qualification suites.

## Advisory formats, benchmarking, and support bundles

R12 adds advisory evidence without silently making user choices. `arc formats recommend PATH` reports the compatibility and tradeoffs of every normalized format—including installed backend chain, metadata preservation, streaming, encryption, multipart, and random-access behavior—and deliberately leaves `selection` null.

```bash
arc formats recommend ./project --json
arc benchmark --format tar.zstd --format 7z --iterations 3
arc benchmark ./sample-corpus --json=v1
arc diagnostics bundle support.zip --recent 50
```

`arc benchmark` uses either a deterministic generated corpus (`--seed`, `--size-mib`) or a user-selected regular file/directory corpus. Directory corpus identity covers normalized file paths/content, meaningful empty directories, and symlink targets; benchmark creation uses a portable corpus-root basename rather than embedding the host's absolute source prefix, and round-trip proof must reproduce that logical corpus identity. Cross-format smoke qualification uses ordinary directory content because metadata fidelity such as symlink preservation can vary by format/backend; a fidelity mismatch remains visible as `verified_roundtrip=false` rather than being hidden. Symlink corpus roots are rejected rather than silently followed. Evidence is explicitly host-specific and records encode/decode throughput, ratio, verified round trip, capability/backend identity, and peak process-tree RSS where `/proc` makes that measurable.

`arc diagnostics bundle` is offline-by-default: it never performs a live remote probe and never includes archive contents by default. The ZIP contains JSON-only runtime/config provenance, backend versions/capabilities, alias/install health, doctor output, recent transaction diagnostics, cached remote capability evidence, and a manifest hashing every evidence member. Password-like fields and sensitive environment values are redacted. Public machine contracts are queryable as `format-recommendation-v1`, `benchmark-v1`, and `diagnostics-bundle-v1`.

The cumulative R12 qualification is available as `./devtoolw r12-gate`. It requalifies the R09B→R12 transport/recovery/mutation/config/advisory chain and adds manifest-integrity, no-network, cached-remote-redaction, machine-wrapping, and real benchmark evidence probes. The final content seal remains a separate post-gate artifact.

## Tests

```bash
python -m pip install -e '.[test]'
pytest
```

The suite includes format/filter/safety/completion tests plus native backend integration tests. ARC-R01 adds regression coverage for normalized read-side filters, stream conflict handling, zero-write dry runs, native `list -- ...` passthrough, add/update separation, password classification, and compressed-stream detection ambiguity.

See `docs/DESIGN.md` for the implementation contract and architecture.

## Devtool development workflow

This repository includes a first-class Devtool contract and generated `devtoolw` launchers. On Termux, the `arc` target explicitly uses **native host Python** (not the configured chroot), with Devtool managing an isolated host-side venv under `.devtool/`.

```bash
./devtoolw setup
./devtoolw test --profile quick
./devtoolw check
./devtoolw quality
./devtoolw release
```

See [`docs/WRAPPER.md`](docs/WRAPPER.md) for the target-local EXO DAGs, safe scheduler, affected-file ownership, package/artifact verification, SBOM/release evidence, wrapper options, and inspection/seal commands.

### Native-Termux Devtool quality policy

`arc-cli` uses host Python for Devtool execution. `.devtool.toml` is created separately from integration overlays, but it must be committed before an overlay is applied because Devtool validates candidates in an isolated Git transaction worktree. Ruff is intentionally **not** installed into the Devtool venv: the `arc` workflows call the Termux `ruff` executable already installed on the host (`pkg install ruff`) through target-local command jobs. `./devtoolw lint` therefore uses native Termux Ruff without triggering a pip/maturin Rust build, while pytest and package tooling remain isolated in Devtool's managed host-Python venv.

### Remote execution and native command learning

ARC-R04 supports SSH and rclone archive locations, cached remote completion/fzf browsing, and `--show-native[=before|after|both]`. Streamable creates (TAR/compressed TAR and standalone compressors) can flow directly to atomic remote sinks; standalone stream extraction/test can flow directly from the remote. ZIP/7z/RAR and safety-sensitive archive reads intentionally use local staging so Arc can preserve its normalized safe-inspection and conflict guarantees. Completion caches are per-directory and invalidate on TTL, provider/config generation changes, and successful Arc mutations.

ARC-R05 extends explicit SSH-native execution beyond list/test. Remote Arc version metadata participates in capability negotiation, same-endpoint locality is enforced before network probes, and native remote exit codes are preserved. `auto` deliberately remains staging-first.

The repository exposes focused Devtool profiles as `./devtoolw test --profile r04` and `./devtoolw test --profile r05`.

### R04 targeted validation

The repository defines the `r04` test profile and the `arc.r04-tests` command job. ARC-R04 overlays use Devtool schema-v2 `validation.tests` to run `python3 scripts/run_r04_tests.py` as the only validation action. The test launcher runs only `tests/test_r04_execution_plan.py` and `tests/test_r04_remote_transport.py`, reusing host pytest/rich when available or a small cached test-only venv. It does not invoke Devtool restore/build/source-audit/SBOM/dependency/CLI/package/release phases.

### R05 targeted validation

The `r05` profile and `arc.r05-tests` job run only `tests/test_r05_remote_native.py` through `python3 scripts/run_r05_tests.py`. ARC-R05 overlay validation uses that single tests-only action and does not request Devtool restore/build/source-audit/SBOM/dependency/CLI/package/release phases.

### ARC final gate and content seal

The final qualification after ARC-R01 through ARC-R12 is wrapper-first and intentionally comprehensive. The static seal contract binds the qualified R12 Gate commit prefix `95981f7`; the live ledger then binds the exact final transaction-tree bytes. The repository exposes two authoritative entry points:

```bash
./devtoolw gate
./devtoolw seal
```

`gate` runs the cumulative EXO qualification: syntax/source hygiene, completion and repository contracts, the complete pytest tree, full installed-native-backend R03 qualification (including the large-manifest case), package build verification, metadata/CLI coherence, and campaign promise checks. `seal` repeats that gate, runs Devtool's wrapper contract seal; launcher-template byte drift caused solely by a newer applying Devtool is recorded but does not invalidate ARC when the launchers remain present, executable, marker-bearing, and every non-template wrapper check passes, then computes and verifies a byte-exact content ledger from the actual post-gate transaction worktree. The committed `release/ARC-FINAL-SEAL.json` is a static seal contract that defines the scope and evidence locations; the live content root is written to `.devtool/evidence/arc-final-gate/candidate-seal.json` and the final `SEALED` verdict. A read-only `before_commit` finalizer re-verifies that exact candidate ledger immediately before Devtool commits.

The static schema-7 campaign contract is `release/ARC-FINAL-SEAL.json`; it binds the qualified R12 Gate prefix `95981f7`, while the live candidate ledger binds the exact transaction-tree bytes. See [`docs/ARC-FINAL-GATE-SEAL.md`](docs/ARC-FINAL-GATE-SEAL.md) for every required gate and its evidence mapping.

### Explain, recover, and resume

Arc can explain a mutating operation through its real dry-run planner without publishing data or creating transaction state:

```bash
arc explain convert archive.zip -tzst
arc explain --json create backup.tar src/
```

Mutating create/add/update/remove/convert operations keep durable journals under the Arc state directory. Inspect or conservatively clean interrupted temporary state with:

```bash
arc recover
arc recover TRANSACTION_ID --cleanup
```

Batch conversion writes a durable per-item manifest. Resume reuses only unchanged source/output pairs backed by prior verification evidence; changed inputs or tampered outputs rerun independently:

```bash
arc convert a.zip b.zip c.zip -tzst --batch
arc convert a.zip b.zip c.zip -tzst --batch --resume
```


### Machine batch execution

`arc batch` is a generic automation surface distinct from `arc convert --batch`. It consumes a versioned JSON document whose operations contain argv arrays rather than shell strings. Inspect the public input contract with `arc schema batch-input-v1`. Use `--validate-only` to check a document without executing it and `--json=v1` for the stable machine envelope.

```json
{
  "schema": "arc.batch-input/v1",
  "on_error": "stop",
  "operations": [
    {"id": "inventory", "argv": ["formats", "--json"]},
    {"id": "verify", "argv": ["test", "backup.7z", "--json=v1"]}
  ]
}
```

## Post-seal composition development

Arc `0.2.0` is the composition wave. COMP-X01 centralizes version truth and naming/format resolution, COMP-X02 delivers logical merge with automatic safe stream concat, and COMP-X03 delivers the exact split/join volume protocol. All three implementations are present; the separate COMP-G1 qualification gate remains the next roadmap boundary. See `docs/ARC-NEXT-ROADMAP.md`.
