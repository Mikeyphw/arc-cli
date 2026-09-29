# ARC COMP-G1 — final composition qualification gate

Status: **QUALIFIED CANDIDATE** until this gate overlay itself passes the authoritative Devtool validation on the repository checkout. Successful application atomically promotes the roadmap ledger to **COMP-G1 gate: QUALIFIED**.

COMP-G1 is **qualification-only**. It adds no runtime composition behavior and contains **no implementation work**. It closes the post-seal `0.2.0` composition wave by auditing COMP-X01, COMP-X02, COMP-X03, COMP-R01, COMP-R02, and COMP-R03 together after all gate-discovered remediations.

## Promise ledger

- **Version/package identity.** The single `0.2.0` authority must remain consistent across runtime `__version__`, CLI/install truth, package metadata, generated distribution paths, and the public split-manifest schema.
- **Deterministic output resolution.** Uniform input formats preserve their format by default, explicit format/output selection stays deterministic, mixed formats fail closed without a target, and suffix/format conflicts remain errors.
- **Safe stream concat.** Same-format compatible single compressed streams may merge by byte concatenation with `reencoded=false`; container archives never use that shortcut.
- **Mixed-stream logical repack.** Explicit stream output for mixed stream codecs concatenates decompressed logical bytes in input order and encodes once. Container/archive to stream flattening stays rejected as ambiguous.
- **Logical container merge.** TAR/ZIP and, when the host exposes the backend, 7z merge named members by logical repack. Member conflict policy remains independent from final destination policy, and hostile member names cannot escape the extraction workspace.
- **Exact split/join protocol.** Zero-byte, exact-boundary, size/count partitioning, ordered `.partNNN` names, `arc.split-manifest/v1`, missing/corrupt/reordered-part rejection, sibling discovery, whole-file verification, and atomic publication remain qualified together.
- **Destructive ordering.** `--delete-inputs`, `--delete-source`, and `--delete-parts` remain explicit and occur only after the owning proof/publication boundary permits them.
- **Remote staging.** Composition continues to reuse the existing read-side transport authority and its cleanup behavior; the gate does not introduce live remote dependencies of its own.
- **Temporary-file hygiene.** Arc-owned transaction candidates, merge workspaces, split workspaces, join candidates, remote staging, stdin materialization, and backend manifest materialization are cleaned on **success, ordinary errors, and Ctrl+C/SIGINT**. Durable failed/interrupted journals remain truthful, while only genuinely unremovable paths are left for `arc recover --cleanup`.
- **Interruption/recovery.** The gate includes a real SIGINT subprocess probe plus merge/split/join KeyboardInterrupt paths and the cumulative recovery suite, proving cleanup without erasing failure/interruption evidence.
- **Machine/explain/batch truth.** `arc.machine/v1`, `split-manifest-v1`, schema discovery, explain/dry-run decisions, backend command truth, and generic JSON batch execution stay aligned with the composition commands. COMP-R03's active machine-schema convergence is a required prerequisite.
- **Generated and distribution surfaces.** Aliases, command docs, completion, manpages, package data, **wheel + sdist** builds, and rebuilt-wheel identity are part of the gate rather than post-hoc documentation.
- **Backend availability is factual.** Host-optional provider probes such as 7z run when available and otherwise skip explicitly; absence is not reported as success and does not weaken mandatory provider-independent semantics.
- **Ownership preservation.** COMP-G1 adds its own profile/test/job/workflow/wrapper surface without deleting or repurposing the focused COMP-X01/X02/X03 or COMP-R01/R02/R03 ownership surfaces.

## Authoritative Devtool surfaces

- Test profile: `comp_g1`
- First-class test: `arc-comp-g1-gate`
- Contract job: `comp-g1-contract`
- Workflow: `comp_g1`
- Wrapper command: `./devtoolw comp-g1`

The workflow first re-runs the X01/X02/X03/R01/R02/R03 contracts plus source hygiene, machine, remote, alias, generated-command, completion, and active Devtool contracts. Only then may the cumulative COMP-G1 pytest matrix run. A failure leaves the composition wave unqualified and must become a separate remediation rather than being hidden in this gate.
