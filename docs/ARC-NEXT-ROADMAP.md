# Arc Next Roadmap — runtime truth, resilient operations, typed evidence, and operator UX

Status: active implementation roadmap after the ARC-R01..R05 final seal and the convert/info/alias/manual audit remediation.

## Working rules

- Before each implementation overlay, inspect the next three unimplemented roadmap items as one merge window.
- Merge only items that share a coherent ownership and validation boundary; never reduce scope merely to reduce overlay count.
- Keep implementation overlays separate from milestone gates and final seals.
- Ordinary implementation overlays are applied with `--no-validate`; they still add and exercise native Devtool `[[test]]`, target-local jobs, workflows, and EXO-visible identities.
- Gate overlays reference the same repository-owned test IDs through artifact validation rather than duplicating test orchestration in shell scripts.
- Lifecycle hooks are reserved for lifecycle-specific work such as refreshing the active editable install; they are not a replacement for tests or workflows.
- Machine output, human output, documentation, completion, packaging metadata, and runtime behavior must derive from common typed contracts wherever practical.

## R06 — Runtime, alias, and installation truth

**Merge-window decision:** merge roadmap items 1–3. `arc doctor`, first-class aliases, and development-install refresh all own the same repository/package/runtime-consistency boundary.

Deliver:

1. `arc doctor` audits installed distribution identity, source checkout, editable-install state, console entry-point metadata, PATH aliases, config/profile validity, backend availability, optional integrations, generated docs, and completion freshness. `--json` is machine-readable; `--fix` performs only bounded deterministic repairs.
2. A canonical typed alias registry owns executable alias → canonical command identity. `arc aliases` exposes registry/runtime truth. Packaging metadata, Devtool packaging expectations, dispatch, completion, and documentation are checked/generated from that registry.
3. A development-install refresh contract regenerates/checks derived surfaces and refreshes `pip install -e` without dependencies/build isolation. Devtool owns a target-local refresh job, and the R06 artifact uses a lifecycle finalizer so newly added console entry points become active after apply.

Acceptance: explicit regression for “declared alias but missing on PATH”; registry drift fails closed; generated docs/completion remain synchronized; package/runtime drift is visible and repairable.

**Separate R06 gate:** qualify repository tests, packaging rebuild parity, generated surfaces, and an installed-environment alias probe. Do not combine this gate with R06 implementation.

## R07 — Explainable plans, transaction journal, and resumable batches

Merge window: inspect items 4–6 before implementation.

4. Add `arc explain` and richer `--dry-run` planning. Plans expose chosen format/backend, capability reasoning, staging, temp/publication paths, overwrite/source-removal policy, remote locality, and verification strategy without mutation.
5. Generalize transactional publication into a durable transaction journal for mutating create/add/update/remove/convert paths. Track staging, publication, source deletion, verification, remote transfer, cleanup, and interruption state; add `arc recover`/cleanup semantics.
6. Add resumable batch conversion using durable manifests keyed by source identity, target policy, and completed verification evidence. Unchanged proven items can be reused; changed inputs invalidate only affected work.

Potential merge: all three likely share execution-plan/transaction ownership, but the merge decision is made only when R07 starts.

## R08 — Stable machine schema, backend capabilities, and verification policy

Merge window: inspect items 7–9 before implementation.

7. Version Arc's machine-output envelope and typed result/error/diagnostic structures. Publish JSON Schema and stable canonical/literal invocation identity.
8. Replace scattered backend special cases with typed capability negotiation covering format operations, streams, encryption, solid/multipart metadata, stdin/stdout, mutation, verification depth, random access, and remote suitability.
9. Add explicit verification levels (`none`, `structure`, `members`, `full`) with evidence stating exactly what Arc proved and what the selected backend could not prove.

Expected boundary: typed capability/evidence contract shared by planner, runtime, JSON, diagnostics, and docs.

## R09 — Provenance, logical equivalence, and archive diff

Merge window begins with items 10–12; item 12 may split if remote transport ownership makes the boundary materially different.

10. Add logical archive fingerprints based on normalized members, sizes, checksums where available, and selected metadata so differently encoded archives can be compared semantically.
11. Add `arc diff` for added/removed/changed members, metadata changes, logical equivalence, and container/compression differences; provide stable JSON.
12. Deepen remote execution parity: typed locality/atomicity/staging capabilities, cached remote capability probes, and explicit publication guarantees. Never describe remote behavior as locally atomic without evidence.

## R10 — Destructive-operation policy and interactive execution UX

13. Centralize destination/destructive policies: fail/replace/rename/skip-identical, recoverable removal when supported, optional backup-existing, and one publication policy shared by all mutating commands.
14. Improve interactive progress around semantic phases (`scan → encode → verify → publish`), batch/member progress, throughput/ratio, and safe cancellation/cleanup while keeping non-TTY and JSON output clean.

Item 15 is inspected in the same merge window but should merge only if configuration provenance is part of the same UI/policy implementation boundary.

## R11 — Configuration provenance and explainability

15. Add `arc config show --effective`, `arc config explain KEY`, and resolved-profile inspection with provenance for built-in defaults, config, environment, profile, and CLI. Invalid/unknown configuration should be diagnosable without silently becoming `{}` in doctor/config-audit surfaces.

This may merge with R10 only if the next-three merge-window audit proves a coherent implementation boundary; otherwise it stays independent.

## R12 — Advisory intelligence, benchmarking, and support evidence

Merge window: inspect items 16–18 before implementation.

16. Add `arc formats recommend PATH` as a factual compatibility/tradeoff surface—metadata preservation, streaming, encryption, multipart, installed-backend availability—without silently choosing for the user.
17. Add `arc benchmark` using generated or user-selected corpora to measure encode/decode throughput, compression ratio, memory where measurable, and capability availability. Keep benchmark evidence reproducible and clearly host-specific.
18. Add `arc diagnostics bundle` containing redacted version/config provenance, backend versions/capabilities, platform/Python/runtime identity, alias/install health, recent structured diagnostics, and relevant remote capability evidence. Never include passwords, secret environment values, or archive contents by default.

Expected boundary: advisory/diagnostic tooling that consumes the typed capabilities, machine schema, config provenance, and runtime truth implemented earlier.

## Gate and seal policy

Milestone gates remain separate artifacts. A gate should reference repository-declared first-class test IDs and exercise the named Devtool workflow/EXO path. At a broader wave boundary, perform a promise-by-promise audit rather than treating green tests as proof of complete delivery. The final seal must bind the exact source/artifact identity and remain separate from the last implementation overlay.
