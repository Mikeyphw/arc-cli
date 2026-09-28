# ARC-R09B cumulative gate — remote capability and publication parity

Status: QUALIFIED

This gate closes the ARC-R09B implementation window after the R09B completion overlay. It is deliberately separate from implementation and from R10. The gate audits the transport/publication promise ledger as a whole rather than treating the targeted implementation test run as sufficient evidence.

## Qualified promise ledger

| Promise | Gate conclusion |
| --- | --- |
| Public typed remote evidence | **Closed.** `arc.remote-capability/v1` is registered, packaged, queryable, and requires transport locality, staging, publication, and probe provenance. |
| SSH locality and publication truth | **Closed.** SSH publication uses a same-parent temporary file and `mv`; `guaranteed_atomic=true` is emitted only when the required remote tools are proven by capability evidence. |
| rclone publication truth | **Closed.** Temporary-object + `rclone moveto` remains `provider-dependent` even when the provider reports `Move=true`; provider move support never becomes a local-style atomicity claim. |
| Cache provenance | **Closed.** Live/cache source, age, TTL, provider generation, provider identity, fetch time, and freshness are explicit. Live evidence is always age zero; cache age advances from probe completion. |
| Refresh and expiry | **Closed.** `--refresh` bypasses a fresh cache; expired evidence causes a fresh capability probe during live queries. |
| One-probe SSH contract | **Closed.** R08 typed backend inventory is collected by `arc backends --json` inside the same single SSH probe used for tool/version evidence. |
| Zero-I/O planning | **Closed.** Dry-run/plan publication logic is zero-network: it may consume fresh cached evidence but fails closed to unproven/provider-dependent semantics when evidence is missing or stale. |
| Runtime publication evidence | **Closed.** Remote upload/stream publication records use the same transport-owned typed guarantee as planning. |
| R09A compatibility | **Closed.** Remote `convert --prove-equivalent` retains post-publication remote re-read verification and the R09A logical fingerprint authority. |
| Machine/docs/completion/package parity | **Closed.** R08/R09A schemas remain additive and green; generated command docs, manpages, completion, wheel/sdist schema inclusion, and aliases are included in the cumulative gate matrix. |
| Devtool ownership | **Closed.** The gate is a first-class `arc-r09b-gate` test, `r09b-gate-contract` job, `r09b_gate` EXO workflow, and `./devtoolw r09b-gate` wrapper command. |

## Gate matrix

The implementation completion overlay passed **144 targeted tests**. The gate widens qualification to the R09B-specific gate tests plus R04 transport, R05 remote-native execution, R07 recovery/execution, R08 machine/capability evidence, R09A provenance/equivalence, conversion, CLI integration, safety, info, aliases, completion, manpages, and distribution contracts. Static machine, provenance, remote, Devtool, command-doc, completion, and gate-contract checks are separate workflow nodes so one green pytest aggregate cannot mask contract drift.

The gate specifically adds deterministic matrix coverage for live → cache → refresh → expiry transitions, fresh-cache versus stale-cache zero-network dry-run behavior, and rclone `Move=true` / `Move=false` / unknown publication semantics.

## Audit-loop result

The implementation audit found one R09B timing defect before this gate: live probe age could become `1` when a provider probe crossed a one-second boundary. The completion overlay corrected that by timestamping probe completion and defining live evidence as age zero. The cumulative gate found no remaining product/runtime promise gap requiring another implementation change.

## Boundary decision

R10 remains separate. Destination fail/replace/rename/skip-identical policy, recoverable removal/backup policy, semantic progress phases, cancellation, and cleanup UX are mutation-policy/interactive-execution concerns; they must not become transport-capability authority.
