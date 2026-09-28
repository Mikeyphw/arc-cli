# ARC-R09B audit — remote capability and publication parity

Status: implementation closure candidate; cumulative gate remains separate.

## Audit findings

The pre-R09B transport layer had two truth gaps. First, rclone staging used `moveto` and described finalization as atomic even though rclone's provider move semantics do not prove atomic replacement. Second, remote capability cache consumers received capability facts without explicit cache source, age, TTL, or provider-generation evidence. The remote Arc capability probe also exposed only version/tool presence rather than the R08 typed backend inventory.

## R09B implementation contract

- `arc.remote-capability/v1` is the typed public transport evidence model.
- Remote capability evidence reports locality, staging requirements, publication/finalization semantics, and probe provenance.
- SSH publication uses a same-parent temporary file plus `mv`; Arc only labels the result `same-filesystem-rename` / `guaranteed_atomic=true` after proving the required remote tools in a live or still-fresh cached probe.
- rclone publication uses a temporary object plus `rclone moveto`; even provider `Move=true` is reported as `provider-dependent` and never upgraded to a guaranteed atomic replacement claim.
- Capability cache records expose live/cache source, age, TTL, and provider-generation identity; `arc backends --remote NAME --refresh` forces a fresh probe.
- SSH probes consume R08 typed backend profiles from `arc backends --json` in the same SSH round trip as the tool/version probe, preserving the existing one-probe cache contract.
- Dry-run/explain publication planning remains zero-network. It may consume a fresh cache record, otherwise it fails closed to unproven/provider-dependent transport semantics.
- Remote conversion transaction/result evidence carries the typed publication guarantee used for the publication step.

## Merge-window decision

R09B remains standalone. R10 items 13–14 own destructive destination policy and semantic progress/cancellation UX. They share a later mutation-policy/interactive-execution boundary and should not be pulled into transport capability authority.

## Validation ownership

First-class Devtool surfaces are `test_profiles.r09b`, test `arc-r09b-remote-capability-publication`, job `remote-contract`, workflow `r09b`, and wrapper command `./devtoolw r09b`. The implementation overlay uses this bounded R09B validation; the cumulative R09B gate remains a separate artifact.

## Recovery overlay note

The recovery/hotfix packaging variant keeps the R09B implementation unchanged but
relocates the R09B-aware machine and Devtool contract checkers to
`scripts/check_machine_contract_r09b.py` and
`scripts/check_devtool_contract_r09b.py`. This avoids overwriting legacy checker
paths that a prior interrupted Devtool transaction may still classify as dirty.
The authoritative `machine-contract` and `devtool-contract` jobs point at the
relocated R09B checkers, and `remote-contract` verifies that wiring. The legacy
checker files are intentionally untouched by this recovery artifact.
