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
