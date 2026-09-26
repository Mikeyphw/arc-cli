# ARC-R01 semantic correctness audit

ARC-R01 closes the behavioral defects recorded in the initial 2026-09-25 audit. It is intentionally limited to normalized semantics; interactive UX, NUL-safe protocols, richer completion/progress, and the full backend qualification matrix remain for later roadmap overlays.

## Promise ledger

| Contract | Result | Evidence |
|---|---|---|
| Normalized filters on list/extract/test | PASS | Shared ordered member filter path plus regression tests |
| Empty filtered selection cannot become “all” | PASS | Explicit no-op guards for list/extract/test |
| Stream conflict policy | PASS | gzip full policy test; gzip/bzip2/xz/zstd skip-existing matrix |
| Dry-run performs no filesystem mutation | PASS | rename-existing/output-dir and create-manifest regression tests |
| Native list passthrough after `--` | PASS | real GNU tar passthrough regression |
| `list --show-command` | PASS | native backend command generated and rendered |
| Add/update distinction | PASS | Info-ZIP add conflict and update replacement round-trip |
| Stream/TAR detection ambiguity | PASS | zero-filled gzip/bzip2/xz/zstd remain streams; empty `.tar.gz` remains TAR |
| Password vs corruption classification | PASS | encrypted ZIP wrong-password regression and backend index classifiers |
| Existing behavior regression | PASS | full 32-test suite passes after ARC-R01 changes |

## Audit-loop findings fixed during implementation

The first R01 pass exposed an additional backend semantic hazard: an empty normalized member list is interpreted by common archive binaries as “all members.” The implementation now detects an explicitly requested selection that resolves to zero members and returns a true no-op before invoking the backend.

The detection audit also tightened precedence: a misleading `.tar.gz` suffix no longer overrides non-TAR decompressed content; the suffix is only a tie-breaker for an empty-TAR-shaped zero marker.

The regression work also exposed ZIP metadata written with permission bits but no POSIX file-type bits. Such entries are now treated as regular files rather than incorrectly classified as special filesystem objects.

## Deferred by roadmap

ARC-R02 remains responsible for full context-sensitive Zsh completion, fzf/Yazi qualification, NUL-safe protocols, detailed Rich telemetry, profiles, and RAR/7z safety convergence. ARC-R03 remains responsible for the complete format × backend × operation qualification matrix and resilience/final evidence work.
