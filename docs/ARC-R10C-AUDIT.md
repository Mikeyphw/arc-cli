# ARC-R10C mutation-policy convergence audit

R10C is a gate-discovered completion overlay inside the R10 ownership boundary. It exists because the widened R10 gate audit found documented/parser promises that were not yet enforced by runtime mutation paths.

## Closed promise gaps

- **Extract policy authority.** `--destination-policy fail|replace|rename|skip-identical` is normalized before extraction and overrides legacy collision flags. `skip-identical` extracts only conflicting members into a same-filesystem comparison area, proves file/symlink identity, and mutates nothing when the target differs.
- **Compatibility.** Legacy `--overwrite`, `--rename-existing`, and `--skip-existing` retain their historical behavior when no explicit destination policy is supplied.
- **In-place recoverability.** `add`, `update`, and `remove` expose `--backup-existing[=PATH]` as a pre-mutation archive snapshot. Snapshot publication is non-clobbering and leaves the original archive in place for the backend mutation.
- **Truthful command surfaces.** Add/update no longer expose destination collision policy that cannot describe an in-place archive mutation. Completion and generated manuals follow the parser.
- **Remote parity.** Remote-native mutation forwards explicit destination policy and backup authority to the delegated Arc process instead of dropping those decisions at the SSH boundary.
- **Repository ownership.** R10C has a dedicated test profile, Devtool test/workflow, and `./devtoolw r10c` wrapper entry.

## Validation boundary

R10C validation covers the new convergence tests plus R10/R10A/R10B regressions, CLI integration, R05 remote-native behavior, R07 recovery, completion, generated manuals, and distribution packaging. The widened R10 gate remains a separate artifact and must qualify R10 through R10C before R11 begins.
