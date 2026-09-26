# ARC-R04 — execution plans, remote transports, remote completion, and native-command learning

ARC-R04 adds a transport layer around the existing normalized archive/backend model. Local archive semantics remain authoritative; SSH and rclone are transports, not archive backends.

## Delivered behavior

- Typed execution plans record backend, compression, SSH, rclone, staging, and finalization stages.
- `--show-native[=before|after|both]` teaches the native commands Arc actually invokes. `--native-style exact|reproducible` controls whether implementation staging/manifest paths are shown literally or normalized for reuse.
- Password values recorded by backend metadata are redacted from native-plan output and structured plan data.
- `[ui] show_native` and `native_command_style` provide persistent defaults.
- SSH remotes are configured under `[remotes.<name>] type="ssh"` and support `ssh://name/path` plus configured `name:/path` shorthand.
- rclone remotes support native `remote:path` syntax and `rclone://remote/path` URI syntax.
- Streamable remote creates (TAR/compressed TAR and standalone compressors) can write directly to an atomic SSH/rclone sink; random-access formats and mutation operations retain local staging where required.
- Standalone remote stream extraction/test can consume SSH/rclone streams directly. ZIP/7z/RAR and safety-sensitive TAR list/extract/test/identify retain local staging so normalized member inspection remains authoritative.
- `--execution=remote` is a separate explicit mode for SSH list/test when the remote host has `arc` installed. Operations that require local inputs/output or credential/filter transfer stay on staging.
- SSH uploads finalize through a remote temporary file + rename. rclone uploads use `rcat` to a temporary object followed by `moveto`, with cleanup on finalization failure.
- Remote create inputs can be files or directories. rclone directories use `rclone copy`; SSH directories stream tar over SSH into local tar extraction.
- Remote dry-run records planned transport stages but performs no network I/O and creates no staging archive/input tree.
- `arc backends --remote NAME` and `arc formats --remote NAME` expose cached remote/provider capabilities.
- Capability cache keys include the remote configuration generation; changing SSH/rclone config automatically selects a new cache generation.
- Normal Zsh completion and `**<TAB>` fzf completion understand SSH aliases, rclone remotes, and remote paths.
- rclone completion uses `lsjson`. SSH completion prefers a remote Python `scandir` JSON protocol and falls back to NUL-delimited one-level `find` when Python is unavailable.
- Remote completion cache entries are per remote directory, TTL-bound, configuration-generation-scoped, and invalidated after Arc mutations.
- `arc completion cache`, `arc completion refresh LOCATION`, and `arc completion clear-cache [REMOTE]` expose cache controls.
- In single-value fuzzy completion, selecting a remote directory descends within the same fzf session. Multi-value create-input completion keeps directories as selectable inputs.

## Execution modes

`--execution=auto` and `--execution=local` use local normalized archive semantics with remote staging. This is the default because random-access archive formats and safety inspection require reliable local access.

`--execution=remote` currently applies to SSH `list` and `test` only, and requires `arc` on the remote host. Password forwarding and local filter-file transfer are intentionally rejected rather than silently changing semantics.

## Cache invalidation

Remote directory cache keys include provider generation plus directory identity. A cache entry expires after `completion.remote_ttl_seconds` (default 60 seconds). Successful Arc remote mutations invalidate the affected parent directory immediately.

SSH provider generation includes the configured host/user/port/identity/jump/extra-argument object. rclone generation includes Arc's remote mapping plus the effective rclone config path/mtime, including rclone's normal default config when `RCLONE_CONFIG` is unset.

Capability records have a separate TTL (default 300 seconds) and use the same provider generation, so changing remote configuration invalidates both completion and capability state automatically.

## Safety / quoting

Remote user paths are never concatenated into unquoted archive commands. SSH upload and directory-staging helpers pass paths as quoted positional parameters to fixed shell scripts. rclone uses argv fields directly. Arc-owned completion candidate streams remain NUL-framed.

## Qualification

R04's overlay validation intentionally runs only targeted pytest selections. Repository-wide source audit, Ruff, SBOM, package, release, and extra contract scripts are not part of the R04 apply validation path.

The local audit loop also runs the complete test suite before packaging so regressions are caught before the artifact is handed off; that full-suite run is not embedded into the overlay.

## Promise audit v2

A post-implementation comparison against the complete ARC-R04 roadmap found and corrected the following v1 discrepancies:

- backend/preprocess/compressor plans were recorded as separate placeholder stages; they now render as real copyable shell-equivalent pipelines while retaining structured argv/pipeline arrays;
- streamable remote creates were unnecessarily staged as complete local archives; TAR, compressed TAR, and standalone compressor creates now stream directly into atomic SSH/rclone sinks;
- standalone remote stream extraction/test now streams directly from SSH/rclone; archive formats that require safe member inspection or random access continue to stage intentionally;
- configured rclone aliases are preserved separately from the provider remote name;
- rclone's default XDG config file participates in cache/capability generation invalidation even when `RCLONE_CONFIG` is unset;
- cache status uses the entry's configured TTL and reports `stale-config` when the current provider generation differs;
- SSH capability evidence distinguishes Python scandir, `find -print0`, and `stat`;
- configured rclone remotes fail capability probing when the rclone binary is absent rather than advertising unavailable features;
- remote `--add-extension` changes the real destination path, not merely a local staging filename;
- transport progress is byte-based only for known local upload size and indeterminate for unknown stream sizes;
- additional failure qualification covers partial read cleanup, interrupted/failed rclone upload cleanup, SSH atomic cleanup traps, hostile shell characters, and newline-containing remote filenames.

Random-access ZIP/7z/RAR and safety-sensitive TAR reads still stage locally by design. This is not a fallback omission: local stable access is required for the normalized safe-member inspection and conflict semantics inherited from ARC-R01/R02.

R04 overlay validation remains tests-only. `.devtool.toml` defines the `r04` test profile and a direct `r04-tests` command job. Overlay validation uses schema-v2 `validation.tests` to invoke `python3 scripts/run_r04_tests.py`, which runs only the two R04 modules. The bootstrap reuses host pytest/rich when available or a small cached test-only venv; it does not invoke Devtool restore/build/source-audit/SBOM/dependency/CLI/package/release phases.

## Dry-run discovery correction (v5)

The Termux validation run exposed a remaining zero-I/O violation: a configured rclone input was classified through `rclone listremotes` before the create path reached its dry-run guard. Configured rclone aliases are now authoritative and resolve without a probe, and runtime dry-run parsing disables rclone discovery subprocesses entirely. Native rclone `remote:path` syntax remains available in dry-run by reading remote section names directly from the local effective `rclone.conf`, which performs no subprocess/network I/O. The end-to-end dry-run test plus parser-level configured-alias and native-config no-probe regressions cover this contract.

