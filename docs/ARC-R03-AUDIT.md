# ARC-R03 Capability and Qualification Audit

ARC-R03 is the third implementation overlay in the remediation campaign. It closes the remaining backend-resolution and qualification gaps while deliberately leaving the final campaign gate/seal for ARC-R04.

## Promise ledger

| Promise | R03 result |
| --- | --- |
| Capability-aware backend selection | Delivered. Resolver decisions now include operation plus requested normalized capabilities. |
| Explicit fallback policy | Delivered. Normal behavior falls through configured candidates; `--no-fallback` restricts selection to the first configured preference; `--backend` remains strict. |
| Capability inventory | Delivered through `arc backends --json` and qualification ledgers. |
| Format × backend × operation qualification | Delivered as an installed-candidate resolver matrix with explicit PASS / SKIPPED_BACKEND_UNAVAILABLE / SKIPPED_CAPABILITY_UNSUPPORTED states. |
| Selected-backend round trips | Delivered for TAR, compressed TAR, ZIP, and single-stream formats; 7z/RAR run when creation backends are installed. |
| Mutation qualification | Delivered for TAR/ZIP/7z/RAR where update/mutation capabilities exist. |
| Password classification | Delivered for ZIP/7z/RAR when password-capable creation/extraction backends are present. |
| Corruption classification | Delivered for ZIP and TAR test paths. |
| Adversarial extraction | Delivered for ZIP/TAR traversal archives with exit-code and no-escape assertions. |
| Interruption cleanup | Delivered with process-level SIGINT qualification; child termination and atomic-temp cleanup are asserted. |
| Atomic failure behavior | Delivered: failed creation preserves the existing archive and removes partial temporary outputs. |
| Temporary manifest cleanup | Delivered for failing TAR creation. |
| Huge-manifest behavior | Delivered with bounded argv/list transport tests and a real 1,200-member TAR qualification. |
| Evidence ledgers | Delivered by `scripts/run_r03_qualification.py` under `.devtool/evidence/arc-r03/` by default. |
| Final campaign seal | Intentionally deferred to ARC-R04 per campaign plan. |

## Audit-loop discoveries fixed during R03

1. Backend preference order alone was insufficient: an installed first-choice backend could accept the format but silently ignore a normalized feature such as threads. Resolver selection now checks required capabilities before choosing a candidate.
2. `--no-fallback` needed to apply not just to container backends but to external TAR compressors. Threaded TAR compression now selects a thread-capable compressor when fallback is allowed and fails deterministically when fallback is disabled.
3. Backend diagnostics previously summarized only one selected executable per role. `arc backends --json` now exposes every configured candidate, installation status, path, and capability set.
4. Qualification needed to distinguish unavailable tooling from functional success. Runtime ledgers use explicit skip states rather than converting missing 7z/RAR binaries into PASS.
5. Interruption and backend-failure behavior required qualification at the process/filesystem boundary, not only unit-level command construction. R03 asserts child termination, preserved existing archives, temporary-output cleanup, and manifest cleanup.
6. Termux qualification exposed a real 7-Zip mutation contract mismatch: normalized `.7z` archives were created in the backend default solid mode, while mutation support is not reliable for solid archives. Arc-created 7z containers now use `-ms=off`, and add/update keep that mutation-safe layout.
7. Qualification summaries now include explicit failing case records so a platform-specific backend mismatch identifies the exact matrix row instead of surfacing only captured native stderr.
8. Real Termux 7-Zip 26.03 exposed a second POSIX transport mismatch: directory entries supplied through `@listfile` can produce `No more files` scan warnings and exit status 1. SevenZipBackend now switches directory-bearing manifests to direct argv while retaining `@listfile` for file-only large manifests. The R03 smoke assertion also embeds exact failing rows in the assertion payload.
9. Real Termux qualification exposed a BSD-tar capability mismatch: `bsdtar` was incorrectly advertising normalized member removal even though it does not implement GNU tar `--delete`. Arc now probes concrete `tar` executables for `--delete`, never advertises removal for `bsdtar`, lets resolver fallback select a delete-capable tar when installed, and records TAR mutation as `SKIPPED_CAPABILITY_UNSUPPORTED` when remove is genuinely unavailable. The mutation qualifier now requires add/update/remove before execution. Expected negative safety/corruption/password stderr is captured internally so successful rejection probes do not inflate Devtool diagnostics.

## Qualification performed before packaging

- Full Python sanity suite after the Termux BSD-tar capability audit fix: all tests passing, including concrete TAR delete probing, mutation capability preflight, and diagnostic-capture regressions.
- R03 focused suite: capability/fallback resolution, backend inventory, large-manifest transport, interruption cleanup, atomic failure preservation, TAR manifest cleanup, compressor capability fallback, and qualification smoke.
- Runtime qualification on the build host: 18 PASS, 0 FAIL, with unavailable 7z/RAR creation paths explicitly skipped.
- Real 1,200-member TAR creation/list qualification.
- Real password rejection and corruption classification.
- Real adversarial ZIP/TAR traversal rejection.

## Evidence model

`scripts/run_r03_qualification.py` writes:

- `qualification.json`
- `capability-matrix.json`
- `backend-matrix.json`
- `safety-matrix.json`
- `resilience-matrix.json`
- `summary.json`

The later ARC-R04 gate/seal should consume these ledgers rather than rerunning or reinterpreting R03 promises informally.

## v5 targeted lint validation correction

Termux qualification proved the functional R03 surface before the artifact validator reached Ruff: targeted pytest passed and the runtime qualification matrix reported `status=PASS`. The remaining failure came from inheriting the user's global Ruff policy plus `ruff format --check` across implementation files that include pre-existing R01/R02 formatting debt.

R03 validation is therefore made deterministic and scope-correct: system Ruff is still required when present, but it runs with `--isolated --select E9,F` against the R03-touched Python surface. This preserves syntax/undefined-name/unused-import correctness without turning R03 into a repository-wide formatting migration. Actual unused imports and variables identified during the Termux run were removed. Formatting/style convergence remains suitable for the later ARC-R04 gate/seal if desired.
