# Arc Next Roadmap — runtime truth, resilient operations, typed evidence, and operator UX

Status: active implementation roadmap after the ARC-R01..R05 final seal and the convert/info/alias/manual audit remediation.

## Working rules

- Before each implementation overlay, inspect the next three unimplemented roadmap items as one merge window.
- Merge only items that share a coherent ownership and validation boundary; never reduce scope merely to reduce overlay count.
- Keep implementation overlays separate from milestone gates and final seals.
- Implementation overlays run bounded targeted validation for the exact changed non-Gradle boundary. They add and exercise native Devtool `[[test]]`, target-local jobs, workflows, and EXO-visible identities. Targeted implementation validation does not replace milestone-gate or final-seal evidence.
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

**Merge-window decision:** merge items 4–6. Explainable dry-run planning, durable mutation journals, and resumable batch conversion share one execution-plan/transaction/evidence boundary; splitting them would create competing state models.

4. Add `arc explain` and richer `--dry-run` planning. Plans expose chosen format/backend, capability reasoning, staging, temp/publication paths, overwrite/source-removal policy, remote locality, and verification strategy without mutation.
5. Generalize transactional publication into a durable transaction journal for mutating create/add/update/remove/convert paths. Track staging, publication, source deletion, verification, remote transfer, cleanup, and interruption state; add `arc recover`/cleanup semantics.
6. Add resumable batch conversion using durable manifests keyed by source identity, target policy, and completed verification evidence. Unchanged proven items can be reused; changed inputs invalidate only affected work.

Acceptance: explain/dry-run must be mutation-free; mutating commands emit durable phase evidence; recovery only removes transaction-owned temporary state; resumable batches reuse only matching source + verified-destination evidence, selectively invalidate changed items, and require explicit force before replacing externally changed outputs.

**Separate R07 gate:** qualify the complete execution/recovery/resume promise ledger and broader regression surface after this targeted implementation validation; do not merge the gate into R07 implementation.

## R08 — Stable machine schema, backend capabilities, and verification policy

**Merge-window decision:** merge items 7–9. Stable machine output, backend capability negotiation, and verification evidence are one typed planner/runtime/evidence boundary; splitting them would allow the JSON contract to describe a capability/verification model different from the executor's authority.

7. Add explicit `--json=v1` with the `arc.machine/v1` result/error/diagnostic envelope while preserving historical bare `--json` shapes for compatibility. Bundle `machine-v1`, backend-capability-v1, and verification-evidence-v1 JSON Schemas; preserve canonical and redacted received invocation identity, including installed aliases and parser failures.
8. Make immutable typed backend profiles authoritative for normalized operation support, stdin/stdout, encryption read/write, solid/multipart behavior, metadata/mutation, random access, thread support, safe indexing, remote suitability, and verification depth. Legacy capability strings remain a compatibility projection of this typed source.
9. Add explicit `--verify-level none|structure|members|full`. Requested proof must be satisfied by the selected backend or fail closed; a weaker proof is legal only with `--allow-verification-downgrade` and produces explicit requested/achieved/downgraded/check evidence. Verification `none` cannot authorize `--replace-source` or resumable-batch evidence reuse.

Acceptance: legacy bare JSON remains regression-compatible; `--json=v1` is one self-contained record with secret-redacted invocation and typed errors/diagnostics; schemas survive wheel/sdist rebuilds; capability reporting and planner decisions consume the typed profile; impossible proof requests fail before being mislabeled; downgrade is opt-in and visible; verification-disabled conversion cannot become deletion/resume authority.

**Separate R08 gate:** qualify the cumulative typed-machine/capability/verification promise ledger and broader backend matrix after this targeted implementation validation; do not merge the gate into R08 implementation.

## R09A — Logical provenance, equivalence, and archive diff

**Merge-window decision:** merge items 10–11 and split item 12. Logical fingerprints and archive diff share one archive-content/provenance authority. Remote capability negotiation is owned by the SSH/rclone transport/cache/publication layer and remains R09B rather than coupling transport semantics to content comparison.

10. Add `arc info --fingerprint` and the `arc.logical-fingerprint/v1` contract. The logical digest is format-independent: normalized NFC member paths, kinds, regular-file sizes/content SHA-256, link targets, and meaningful empty directories participate; timestamp-only metadata is recorded in a separate digest; encoded archive bytes retain their own SHA-256 provenance. Non-empty explicit directory records are normalized away because formats disagree about whether parent entries must be stored. Single streams use the synthetic `@stream` logical member.
11. Add `arc diff` / `arcdiff` / `arc-diff` with stable `arc.archive-diff/v1` JSON. Classify added/removed/type/content/metadata changes and report logical equivalence, metadata equivalence, byte identity, format change, and encoding-only equivalence. Side-specific passwords must be redacted from machine evidence. A successful comparison returns success even when archives differ; equivalence is data, not an execution status. Reuse the same authority for `arc convert --prove-equivalent`: fingerprint source and unpublished destination before local publication, fail closed on logical mismatch, and for remote destinations re-read/fingerprint the published object before reporting the proof.

Acceptance: logically equal ZIP/TAR/container encodings share the logical digest even when byte hashes differ; timestamp-only changes do not become content changes; explicit non-empty directory entries do not create false cross-format differences; empty directories remain logical content; normalized-path collisions are rejected before extraction; `convert --prove-equivalent` publishes local output only after logical equivalence succeeds and treats intentional filters as semantic changes; schemas/manpages/aliases/completion survive wheel/sdist rebuilds; R08 machine schema guarantees remain green with the additive provenance schemas.

**Separate R09A gate:** targeted implementation validation runs on this overlay, while cumulative provenance/equivalence gate evidence remains a separate artifact.

## R09B — Remote capability and publication parity

**Merge-window decision:** keep item 12 standalone. Items 13–14 belong to the later R10 mutation-policy/interactive-execution boundary and must not become transport-policy authority.

12. Deepen remote execution parity: typed locality/atomicity/staging capabilities, cached remote capability probes with explicit age/provenance, and explicit publication guarantees. Never describe remote behavior as locally atomic without evidence. R09B consumes R08 typed backend profiles through the same SSH probe and carries R09A-compatible read-side truth without making either layer the owner of transport policy. rclone `moveto` remains explicitly provider-dependent; SSH same-parent rename becomes a guaranteed-atomic claim only from probed tool evidence. Dry-run planning stays zero-network and fails closed when no fresh evidence exists.

Acceptance: `arc.remote-capability/v1` is bundled and queryable; live/cache provenance, age, TTL, and provider generation are visible; `--refresh` bypasses a fresh cache; the pre-existing one-SSH-probe cache contract remains intact; publication guarantees appear in conversion planning/runtime evidence; rclone never inherits a false local-style atomicity claim.

**Separate R09B gate:** qualify the cumulative remote capability/publication promise ledger and broader transport matrix after this targeted implementation validation; do not merge the gate into R09B implementation.

**R09B gate: QUALIFIED.** The separate cumulative gate closes the transport capability/publication ledger with first-class `arc-r09b-gate` / `r09b_gate` Devtool ownership. R10 is now the next implementation boundary.

## R10 — Destructive-operation policy and interactive execution UX

13. Centralize destination/destructive policies: fail/replace/rename/skip-identical, recoverable removal when supported, optional backup-existing, and one publication policy shared by all mutating commands.
14. Improve interactive progress around semantic phases (`scan → encode → verify → publish`), batch/member progress, throughput/ratio, and safe cancellation/cleanup while keeping non-TTY and JSON output clean.

Merge-window decision: items 13–14 form one mutation-policy/execution-UX authority and ship together in R10. Item 15 remains R11 because configuration provenance has a distinct ownership and validation boundary.

R10 implementation owns the shared `fail/replace/rename/skip-identical` destination policy, optional recoverable `backup-existing`, compatibility aliases for legacy overwrite/force flags, and semantic `scan → encode → verify → publish` progress that remains silent for JSON/non-TTY/quiet output.

### R10A — Backend command display truth

Inserted before the R10 gate after operator feedback. Human interactive `Command` output must show the exact redacted native execution stage Arc actually launches—not echo the Arc wrapper invocation. Received Arc argv remains authoritative machine/provenance identity. `--show-command`, dry-run, and `--show-native` continue to expose native execution without inventing shell syntax.

The R10 gate is deferred until R10C is complete and will audit R10 + R10A + R10B + R10C together.

### R10B — Machine batch input

A generic `arc batch` operation accepts the bundled `arc.batch-input/v1` JSON Schema contract for heterogeneous machine-submitted Arc operations. Each item is a NUL-free argv string array with a unique ID; shell command strings, recursive batches, malformed nested argv, and prompt-style credentials are rejected during whole-batch preflight before execution. The batch owns explicit stop/continue behavior, per-item allowed failures, unconditional interrupt propagation, secret-redacted results, bounded input/operation sizes, validate-only mode, and stable JSON output. This is distinct from `arc convert --batch`, which remains the resumable multi-conversion feature.

R10B completes the inserted implementation pair. The widened R10 gate audit then found one remaining R10 ownership gap: extract exposed an explicit destination policy without enforcing it, in-place add/update accepted backup syntax without using it, remove had no recoverable snapshot surface, and remote-native mutation did not forward those policy/backup decisions.

### R10C — Mutation-policy convergence and recoverability

R10C closes that gate-discovered gap without changing the R11 boundary. Extract now normalizes explicit `--destination-policy` over legacy flags; `skip-identical` proves conflicting members in a same-filesystem comparison area before skipping them. In-place `add`, `update`, and `remove` expose a real non-clobbering `--backup-existing` archive snapshot. Remote-native delegation forwards destination policy and backup authority instead of silently dropping it. Add/update no longer advertise destination-collision policy that is meaningless for an already-existing archive mutated in place.

The next artifact remains the separate widened R10 gate, now auditing R10 + R10A + R10B + R10C before R11 begins.

**R10 gate: QUALIFIED.** The separate cumulative gate closes R10 through R10C with first-class `arc-r10-gate` / `r10_gate` Devtool ownership and a 240-test mutation/command-truth/batch/recovery/transport/docs/package matrix. R11 is now the next implementation boundary.

## R11 — Configuration provenance and explainability

15. Add `arc config show --effective`, `arc config explain KEY`, and resolved-profile inspection with provenance for built-in defaults, config, environment, profile, and CLI. Invalid/unknown configuration should be diagnosable without silently becoming `{}` in doctor/config-audit surfaces.

**Merge-window decision:** keep R11 standalone. R12 consumes configuration provenance but owns advisory recommendations, benchmarking, and support evidence rather than configuration authority.

R11 implementation owns one typed load/validation/result model, one effective-setting resolver, `arc.config-inspection/v1`, raw/effective/explain/profile inspection, runtime fail-closed behavior for invalid configuration, warning-level unknown-key diagnostics, doctor parity, backend environment provenance, generated command/docs/completion parity, and first-class Devtool workflow/test ownership. Precedence is built-in → config → supported environment → selected profile → explicit CLI.

### R11A — Diagnostic convergence and strict typed inputs

The first R11 gate audit found an implementation gap inside the existing R11 boundary: supported environment overrides were explainable when valid but invalid values were not part of the typed load/diagnostic result, allowing doctor to report a healthy default configuration when no TOML file existed. It also found TOML integer fields accepting float/boolean coercion through Python `int(...)`. R11A closes both gaps without changing the R12 boundary: supported environment failures are typed and fail closed across runtime/config/doctor/machine surfaces, invalid effective inspection remains structured, backend environment lists cannot normalize to empty, and TOML/profile integer fields require actual integers.

### R11B — Doctor environment isolation

The widened R11 gate audit found one final convergence gap: after diagnosing an invalid `ARC_BACKEND_*` override, doctor rebuilt backend inventory with an empty config but implicitly re-read the same invalid process environment and could crash. R11B makes backend inventory environment-explicit and makes doctor use safe built-in preferences only after the shared configuration result is invalid, while preserving normal runtime/environment behavior when configuration is valid.

**Separate R11 gate:** audit R11 + R11A + R11B across the full provenance/diagnostic promise ledger and cumulative compatibility surface before R12 begins.

**R11 gate: QUALIFIED.** The separate cumulative gate closes R11 through R11B with first-class `arc-r11-gate` / `r11_gate` Devtool ownership and a 134-test provenance/diagnostic/runtime/docs/package matrix. R12 is now the final implementation boundary.

## R12 — Advisory intelligence, benchmarking, and support evidence

**Merge-window decision:** merge items 16–18. Format recommendation, benchmark evidence, and diagnostics/support bundles consume the same typed backend/config/runtime/remote evidence authority; splitting them would create competing host-capability models. The R12 gate/final campaign seal remains a separate artifact.

16. `arc formats recommend PATH` emits factual `arc.format-recommendation/v1` compatibility/tradeoff evidence—input compatibility, metadata preservation, streaming, encryption, multipart, random-access behavior, and installed backend chains. It intentionally carries `selection=null`: no score, rank, winner, or silent format choice.
17. `arc benchmark [CORPUS]` accepts a user-selected file/directory or a deterministic generated corpus. `arc.benchmark/v1` records corpus SHA-256 identity, host/Python/Arc identity, capability availability, encode/decode throughput, encoded size, encoded/input ratio, traditional compression ratio, round-trip proof, and process-tree RSS where measurable. Unavailable formats stay explicit; evidence is reproducible for its corpus parameters and explicitly host-specific.
18. `arc diagnostics bundle` creates an offline JSON-only ZIP containing redacted runtime/config provenance, backend versions/capabilities, alias/install health, doctor evidence, recent structured transaction diagnostics, and cached remote capability evidence. It performs no network probe, never includes archive contents by default, and redacts password-like fields and sensitive environment values. `manifest.json` hashes every evidence JSON member.

R12 also bundles/queryable `format-recommendation-v1`, `benchmark-v1`, and `diagnostics-bundle-v1` schemas, supports `--json=v1`, ships generated manuals/completion/package resources, and owns first-class `r12` Devtool profile/workflow/test/contract/wrapper surfaces.

**R12A gate-discovered completion:** real directory benchmark qualification exposed four R12 truth gaps before the gate could seal: absolute corpus paths made extraction verification look in the wrong root, directory corpus identity ignored empty directories/symlink targets, final-component symlink recommendation facts were erased by path resolution, and the cross-format TAR/ZIP gate incorrectly assumed identical metadata fidelity across providers. R12A establishes a portable corpus root by running benchmark create from the corpus parent with a basename argument, verifies ordinary-directory path/content round trips across TAR/ZIP, extends corpus identity to normalized files + empty directories + symlink targets so metadata loss remains explicitly detectable, permits empty-directory corpora in `arc.benchmark/v1`, preserves recommendation symlink kind, and rejects symlink benchmark corpora instead of silently following them. R12A is the final implementation-remediation overlay; the separate R12 gate and later content seal remain distinct.

**R12 gate: QUALIFIED.** The separate `r12_gate` qualification closes R12 + R12A across the complete advisory/benchmark/diagnostics promise ledger and the cumulative R09B→R12 regression surface. Recommendation remains non-prescriptive, benchmark corpus truth is preserved across real backends, diagnostics manifest/redaction/no-network guarantees are qualified, and machine/config/remote/docs/completion/distribution contracts remain green.

**Separate final campaign content seal:** bind the exact post-gate source/artifact identity in a later seal artifact rather than folding content-root/seal work into the gate.

## Gate and seal policy

Milestone gates remain separate artifacts. A gate should reference repository-declared first-class test IDs and exercise the named Devtool workflow/EXO path. At a broader wave boundary, perform a promise-by-promise audit rather than treating green tests as proof of complete delivery. The final seal must bind the exact source/artifact identity and remain separate from the last implementation overlay.
