## ARC final gate/seal v6
- ARC-R08: introduce the stable `arc.machine/v1` envelope behind explicit `--json=v1` while retaining bare-`--json` compatibility; bundle public JSON Schemas, preserve/redact literal+canonical invocation identity, and emit parser/runtime failures as typed machine errors. Replace backend capability string authority with typed profiles covering operations, stream I/O, encryption, solid/multipart, metadata/mutation, random access, remote suitability, threading, safe indexing, and verification depth. Add explicit `none|structure|members|full` verification evidence, fail-closed proof negotiation, opt-in downgrade evidence, and prevent unverified conversion from authorizing source deletion/resume reuse.

- Replace the host-precomputed content root with a live post-gate schema-5 ledger generated on the actual validation worktree.
- Commit `release/ARC-FINAL-SEAL.json` as a static schema-6 seal contract defining scope, wrapper entry points, evidence locations, and R05 ancestry.
- Include the static release contract itself in the live authoritative content root.
- Make the `before_commit` finalizer read-only: it re-verifies the exact candidate ledger emitted by `./devtoolw seal` and blocks commit on any post-gate byte/path drift.
- Preserve explicit `missing`, `changed`, `unsealed_authoritative`, and `sealed_non_authoritative` diagnostics.
- Make the final gate rerunnable after commit by checking ancestry from R05 (`a7028c1`) rather than requiring HEAD to remain exactly at the R05 commit.

## ARC final gate/seal v5

- Build the final content ledger from the exact user-provided post-R05 repository snapshot and ship it as an immutable artifact payload.
- Prove the live validation checkout still starts from R05 commit prefix `a7028c1` while the wrapper-driven gate executes.
- Re-verify the immutable ledger during `./devtoolw seal` and again read-only at `before_commit`; no finalize phase may rewrite artifact-owned repository content.
- Keep exact changed, missing, newly authoritative, and unexpectedly sealed path lists in seal failures, while allowing unrelated runtime/transaction detritus outside the authoritative source set.

## Unreleased
- ARC-R09B gate: qualify the cumulative remote capability/publication promise ledger with a first-class `r09b_gate` Devtool workflow, `arc-r09b-gate` test, `./devtoolw r09b-gate` wrapper entry, deterministic cache lifecycle/expiry/refresh coverage, stale-vs-fresh zero-network dry-run coverage, and an rclone Move true/false/unknown publication matrix. R10 remains separate.
- ARC-R09B: add typed remote locality/staging/publication evidence with explicit capability-cache provenance and refresh semantics.
- Stop describing rclone `moveto` publication as atomically guaranteed; provider moves remain explicitly provider-dependent, while SSH rename atomicity is claimed only from probed tool evidence.
- Fold the R08 typed remote Arc backend inventory into the existing single SSH capability probe and carry publication guarantees into conversion plans/results/transaction evidence.
- ARC-R09A: add format-independent logical archive fingerprints (`arc.logical-fingerprint/v1`), `arc diff`/`arcdiff`/`arc-diff` with stable change/equivalence evidence (`arc.archive-diff/v1`), pre-extraction normalized-path collision rejection, stream equivalence via `@stream`, and `arc convert --prove-equivalent` that blocks local publication on semantic mismatch and re-reads remote output before reporting equivalence. Also harden the post-commit development-install refresh so candidate entry-point metadata is installed from Devtool's transaction checkout and the editable source mapping is rebound to the persistent primary checkout before transaction cleanup; overlays that add aliases therefore activate them immediately without leaving the runtime pointed at a disposable worktree.
- ARC-R07: add `arc explain` over the real zero-mutation dry-run planner, durable mutation journals with conservative `arc recover` cleanup, and resumable batch conversion that reuses only unchanged source/destination pairs backed by prior verification evidence. Mutating create/add/update/remove/convert operations now record staging/publication/verification/source-removal/cleanup phases under Arc state; changed sources or tampered outputs invalidate only affected batch items.
- ARC-R06: add `arc doctor` and `arc aliases`, promote executable aliases into one typed canonical registry, synchronize package/Devtool entry-point metadata from that registry, add generated doctor/aliases manuals and completion coverage, and add a bounded editable-install refresh lifecycle so newly declared console scripts become active after overlay apply rather than existing only in repository metadata.
- Add first-class `arc convert` with authoritative format selectors, batch conversion, safe single-member stream conversion, isolated container staging, TAR/stream pipelines, source/destination password separation, unpublished local candidates with post-verification atomic publish, transactional `--replace-source`, truthful `transport-staged` remote semantics with post-upload re-read verification, dry-run/native-plan surfaces, and truthful size-change JSON/UI.
- Add `arc info`/`arci` with summary metadata, tri-state integrity (`null` until `--verify`), content/extension mismatch reporting, member/time/technical data, gzip filename/size hints, multi-archive JSON, and limited metadata behavior for password-protected headers.
- Install real Arc dispatcher aliases (`arcmk`, `arcx`, `arcls`, `arci`, `arct`, `arccv`, `arca`, `arcu`, `arcrm`, descriptive `arc-*` forms, plus pack/unpack/check/convert convenience forms) and make Zsh completion alias-aware.
- Ship generated detailed command/config/reference manpages plus `docs/COMMAND_REFERENCE.md` and `arc man` / `arc help` fallback rendering for Termux/minimal systems; centralize command identity/alias metadata, preserve alias literal/canonical identity in JSON, retain the generator in sdists, and test package/man/completion parity.
- Fix Info-ZIP integrity testing to pass the resolved password to `unzip -t`, enabling destination-password verification without a second prompt/hang.
- Final seal verification is now scoped to the exact authoritative ARC source/config/docs/test set, so unrelated Devtool transaction/runtime files cannot perturb the content root while authoritative additions or byte changes still fail closed.
- Improve the Termux create UX with a copyable/redacted Arc invocation, width-aware progress columns, a resolved create header, and a compact final size/compression summary.
- Add create-only single-dash suffix selectors such as `-zip`, `-tgz`, `-tarzst`, and `-zst`; selectors override filename inference and append their exact suffix when the destination has no recognized archive suffix.
- Extend create JSON output with original bytes, final archive bytes, percent-of-original, saved percentage, and compression ratio.
- Audit-loop closure: preserve the `-` stdout sentinel when a create suffix selector is used, add the missing `.gzip` selector alias, keep the displayed invocation as one copyable logical line on narrow terminals, compact scan/progress/result rows further at very small widths, and stop completion from suggesting mutually exclusive format selectors after one has already been chosen.
## ARC final gate and content seal

- Add a content-addressed repository seal covering the complete R01–R05 source/config/test/documentation/completion tree.
- Add wrapper-first `./devtoolw gate` and `./devtoolw seal` commands backed by dedicated EXO workflows.
- Run the complete pytest suite, full R03 runtime qualification with large-manifest coverage, wheel/sdist verification, completion parity, metadata/CLI coherence, Devtool contract checks, and promise-ledger checks inside the cumulative gate.
- Require Devtool's own wrapper final seal and the content-addressed repository seal after the gate before emitting a machine-readable `SEALED` verdict.
- Record environment-specific optional backend/transport availability without treating missing optional binaries or private remote credentials as implementation PASS.

## ARC-R05 SSH-native execution convergence

- Expand explicit `--execution=remote` from SSH list/test to same-host create/add/update/remove, remote-to-remote extract, and identify.
- Preserve remote Arc normalized exit codes instead of collapsing them into a local generic failure.
- Require create/add/update inputs and extract destinations to resolve to the same SSH endpoint; local/cross-remote operands fail before any network probe.
- Forward inline include/exclude rules and backend passthrough while rejecting local `--include-from`/`--exclude-from` files and credentials.
- Add remote Arc version discovery and compatibility checks to the cached SSH capability model.
- Keep explicit remote dry-runs zero-network while still recording/printing the planned SSH command.
- Invalidate remote completion caches after successful remote-native mutations/extraction.
- Add remote-directory completion for `-o/--output`, including attached `--output=...` completion.
- Add the tests-only `r05` Devtool profile/job and `scripts/run_r05_tests.py`; overlay validation runs only the R05 pytest module.

## ARC-R04 dry-run remote discovery correction (v5)

- configured rclone aliases are now resolved without invoking `rclone listremotes`;
- runtime `--dry-run` parsing disables rclone discovery probes, preserving the zero-network/zero-subprocess contract for remote inputs and remote archive targets;
- native rclone `remote:path` names are discovered directly from the local rclone config during no-probe paths, so dry-run keeps native syntax without spawning rclone;
- add parser-level/native-config regressions plus the end-to-end remote-input dry-run regression to prevent reintroduction.

## ARC-R04 promise-audit correction (v2)

- native pipeline rendering now emits copyable exact/reproducible pipelines rather than placeholder `<backend>` chains;
- TAR/compressed-TAR and standalone compressor creates can stream directly to atomic SSH/rclone destinations;
- standalone remote stream extract/test uses `ssh/rclone cat` directly while random-access/safety-sensitive archive reads remain staged locally;
- remote transfer progress is truthful (known byte totals for staged uploads, indeterminate for unknown stream sizes);
- configured rclone aliases remain user-visible and cache-scoped while provider names stay separate;
- default rclone config path/mtime/size now participates in provider-generation invalidation even without `RCLONE_CONFIG`;
- completion-cache status uses the entry/config TTL and reports `stale-config` when provider generation changes;
- SSH capability discovery records `find-print0`, Python scandir, and stat support; missing rclone is no longer reported as a valid capability provider;
- remote `--add-extension` updates the actual destination object name;
- qualification now covers hostile/newline remote paths, partial staging cleanup, interrupted rclone cleanup, streaming failure cleanup, and atomic SSH trap construction.

### ARC-R04 execution plans, SSH/rclone transport, and remote completion

- Add typed execution-plan recording for backend, compression, SSH, rclone, staging, and finalization stages.
- Add `--show-native[=before|after|both]` and `--native-style exact|reproducible`, with password redaction and persistent `[ui]` defaults.
- Add configured SSH aliases plus `ssh://`/alias-path parsing and native rclone `remote:path`/`rclone://` locations.
- Add atomic remote staging for create/add/update/remove and local staging for list/test/extract/identify while preserving existing normalized safety semantics.
- Add SSH remote Arc execution for explicit `--execution=remote` list/test requests when the remote supports it.
- Add rclone temp-object + `moveto` finalization and SSH temp-file + rename finalization.
- Add remote file/directory input staging for create/add/update.
- Add SSH/rclone remote path completion, same-session fzf directory descent, per-directory caching, TTL/config-generation invalidation, and mutation-driven invalidation.
- Add `arc completion cache|refresh|clear-cache` and cached remote capability discovery for `backends/formats --remote`.
- Keep R04 overlay validation tests-only; no repository-wide Ruff/source-audit/SBOM/package/release validation is embedded in the artifact.

### ARC-R03 capability and qualification matrix

- Make backend resolution capability-aware and add normalized `--no-fallback` behavior.
- Expose installed backend candidates and capabilities through `arc backends --json`.
- Qualify selected-backend round trips across TAR/compressed TAR/ZIP/single-stream formats with explicit unavailable-backend skips.
- Add TAR/ZIP/7z/RAR mutation qualification where the required native backend is installed.
- Add password, corruption, traversal-safety, interruption/atomicity, temporary cleanup, and large-manifest qualification.
- Emit machine-readable capability, backend, safety, resilience, and summary ledgers for the later ARC-R04 gate/seal.
- Probe concrete TAR executables for `--delete`; BSD tar no longer advertises normalized member removal, and resolver fallback can select a GNU-compatible tar when available.
- Treat mutation qualification as requiring add + update + remove capabilities up front, recording an explicit capability skip instead of a false platform failure.
- Capture expected negative-test stderr inside the qualification harness so traversal/corruption/password PASS probes do not become Devtool diagnostics.

### ARC-R02 interaction, completion, progress, and safety convergence

- Make Zsh completion command-aware and add dynamic completion for profiles, environment-backed passwords, backends, formats, paths, and members.
- Use NUL-framed arc-owned candidate protocols for ripgrep, fzf, and shell completion, including filenames containing newlines.
- Make fzf single- versus multi-select context-sensitive while preserving automatic invocation without an explicit fzf flag.
- Validate Yazi archive/input/output chooser roles and clean chooser files on cancellation and exit.
- Add named configuration profiles with profile-before-CLI precedence and ordered filter rules.
- Make Rich progress determinate only when real byte/file telemetry exists and explicitly indeterminate otherwise.
- Parse technical RAR/7z metadata and fail closed when an entry type cannot be proven safe.
- Preserve odd filenames across TAR manifests and ZIP extraction, including exact newline-containing member names.
- Add targeted ARC-R02 interaction/safety regressions while keeping the full backend matrix deferred to ARC-R03.

### ARC-R01 semantic correctness

- Apply normalized ordered include/exclude rules to `list`, `extract`, and `test` as well as creation/mutation.
- Make empty member selections true no-ops so backends cannot reinterpret them as “all members”.
- Normalize stream extraction conflict policies (`--overwrite`, `--skip-existing`, `--rename-existing`) for gzip/bzip2/xz/zstd.
- Make extraction dry-runs zero-write, including rename-existing, output-directory creation, stdout staging, and backend manifest generation.
- Execute `list` backend passthrough after `--` and emit truthful native commands for `--show-command`/`--dry-run`.
- Make `add` fail on existing member names while `update` retains replacement/update semantics.
- Resolve compressed-stream/TAR ambiguity without promoting zero-filled single streams to TAR; `.tar.*` remains the tie-breaker for genuinely empty compressed TAR archives.
- Strengthen password-vs-corruption classification during backend indexing and testing.
- Add ARC-R01 regression coverage across the exact audit failures.

### Devtool integration v7

- Make overlay qualification use a transaction-safe host-shell validator instead of bootstrapping a second project Python environment.
- Replace the stale `arc_cli.dev_lint` hygiene import with dependency-free AST/source checks.
- Delegate detailed wrapper documentation consistency to `devtool wrapper seal` instead of brittle prose matching.
- Keep build enabled for Devtool validation but stop requiring a `build=passed` expectation when the Python runner legitimately reports it as skipped.


- Made Devtool artifact preflight/finalize hooks transaction-safe by running them directly with Bash instead of project-environment Python, preventing automatic `.venv` creation before hook execution.
- Documented that the separately generated `.devtool.toml` must be committed before applying overlays because Devtool candidate validation runs in an isolated Git transaction worktree.
# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added

- First-class Devtool repository contract for the `arc` Python target.
- Native-Termux host-Python execution policy with a Devtool-managed host venv.
- Generated `devtoolw`/`devtoolw.cmd` launchers and typed wrapper commands.
- Target-local EXO jobs and `fast`, `quality`, and `release` DAG workflows.
- Safe workflow scheduler scopes, affected-file ownership, verified package artifacts, SBOM generation, and release evidence.
- Wrapper/completion/Devtool contract checks plus deterministic native-backend smoke evidence.

### ARC-R03 Termux 7-Zip qualification hotfix

- Create normalized 7z archives in non-solid mode (`-ms=off`) so advertised add/update/remove semantics remain mutation-safe across current 7-Zip builds.
- Keep `-ms=off` on later 7z add/update commands.
- Add a regression for the generated 7z mutation-safe command contract.
- Include exact failing qualification rows in R03 summary/evidence output for faster platform-specific diagnosis.
- Avoid 7-Zip `@listfile` transport whenever a manifest contains directory entries; current POSIX/Termux 7-Zip can emit `No more files` warnings (exit 1) for directory entries read through list files. File-only large manifests still use list-file transport.

## [0.1.0] - 2026-09-25

### Added

- Initial normalized archive CLI implementation.
- Content-based archive detection and compound creation suffix inference.
- Native TAR, ZIP/Unzip, 7-Zip, RAR, gzip, bzip2, xz, and zstd backend adapters.
- Rich terminal output and progress infrastructure.
- Normalized creation filters and manifest generation.
- Safe TAR/ZIP extraction preflight.
- Automatic fzf and explicit Yazi chooser integration.
- Generated Zsh completion with dynamic completion support.
- JSON output, stream support, atomic fresh archive creation, and normalized exit codes.

### Qualification status

The initial suite passes 14 tests. The implementation is functional but not yet considered fully sealed; see `docs/AUDIT-2026-09-25.md` for known contract gaps found during the first full audit.

### Devtool native-Termux validator hotfix

- Use Termux's packaged `ruff` executable directly from target-local EXO jobs instead of installing Ruff into Devtool's venv.
- Removed the temporary `arc_cli.dev_lint` fallback; Devtool's Python lint runner executes venv modules and therefore cannot consume a host-only Ruff package.
- Removed implicit coverage flags so pytest does not require an unavailable `pytest-cov` plugin.
- Kept only the pure-Python `build` frontend as a declared validator dependency.
- Added explicit `lint`, `format`, `check`, `quality`, and `release` workflows around the host Ruff toolchain.

### ARC-R03 v5 validator correction

- Made R03's system-Ruff validation deterministic with `--isolated --select E9,F` instead of inheriting host-global Ruff policy and full formatter enforcement.
- Removed unused imports/variables exposed by the Termux R03 validation run.
- Kept the functional R03 qualification matrix and targeted pytest scope unchanged.
### ARC-R04 validation-profile redo

- Added the repository-native `r04` Devtool test profile for the two ARC-R04 pytest modules.
- Exposed `r04` through wrapper test/validate profile choices.
- Kept overlay apply validation tests-only while allowing `.devtool.toml` to carry the authoritative reusable selection.
- Refreshed ARC-R04 audit documentation so streaming/staging and default-rclone-config invalidation descriptions match the implemented v2 behavior.

### R04 validation hotfix

- Replaced runner-native `validation.tasks=["test"]` dependence on the Devtool-managed project venv with a standalone schema-v2 `validation.tests` command.
- Added `scripts/run_r04_tests.py`, which runs only the two R04 pytest modules and bootstraps only pytest/rich in a reusable host cache when they are not already importable.
- Added the repository-local `r04-tests` command job to `.devtool.toml`; no restore/build/source-audit/SBOM/dependency/CLI/package/release validation phase is required for R04 overlay apply.
