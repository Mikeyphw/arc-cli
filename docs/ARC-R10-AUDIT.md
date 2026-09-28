# ARC-R10 implementation audit

R10 owns one mutation-policy/execution-UX boundary. The next-three merge window inspected items 13–15 and kept configuration provenance (15) in R11.

## Delivered promises

- `DestinationPolicy` is a shared typed authority: `fail`, `replace`, `rename`, `skip-identical`.
- Legacy create/extract `--overwrite` and convert `--force` remain compatibility aliases for replace; an explicit `--destination-policy` wins.
- Local create/convert publication evaluates the shared policy against the unpublished candidate. `skip-identical` requires byte identity and otherwise fails closed.
- Rename preservation chooses a non-colliding `.old.N` path. `--backup-existing[=PATH]` is allowed only for replace and never clobbers an existing backup.
- Resume may replace only the batch-owned destination already protected by R07 identity/fingerprint checks; R10 does not weaken that ownership boundary.
- Remote convert refuses rename/skip-identical where Arc lacks local identity evidence rather than inventing semantics.
- Semantic progress has stable `scan -> encode -> verify -> publish` phases, batch position, elapsed time, and compression-ratio context. Convert drives these phases around real work.
- Progress remains disabled for JSON, quiet, and auto/non-TTY execution.
- R10 has repository-owned test/profile/workflow/wrapper identity `arc-r10-mutation-policy-progress` / `r10` / `./devtoolw r10`.

## Validation boundary

Targeted implementation validation covers R10 policy/progress, convert, CLI integration, R07 recovery/resume, R09B publication truth, completion, generated manuals, and distribution packaging. A later gate remains a separate artifact.

## Gate-audit remediation: R10C

The widened gate audit found that the original implementation ledger overstated convergence in three places: extract parsed `--destination-policy` but still executed only legacy flags, add/update accepted `--backup-existing` without snapshotting the archive, and remove/remote-native mutation lacked equivalent recoverability/forwarding. R10C closes those gaps before the gate may qualify R10.

- Explicit extract policy is authoritative over legacy overwrite/rename flags.
- Extract `skip-identical` stages conflicting members and proves file/link identity before skipping; differing targets fail closed without mutation.
- Legacy `--skip-existing` remains intentionally weaker for compatibility when no explicit policy is supplied.
- Add/update/remove use a non-clobbering pre-mutation archive snapshot when `--backup-existing[=PATH]` is requested.
- Add/update no longer advertise destination-collision policy, because they mutate an already-existing archive in place.
- Remote-native create/extract/add/update/remove forwards destination-policy and backup authority to the delegated Arc process.
