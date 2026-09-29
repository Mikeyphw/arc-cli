# ARC-R12 Gate — final campaign qualification

Status: **QUALIFIED CANDIDATE** until the Devtool gate overlay itself passes on the authoritative repository checkout.

This gate is qualification-only. It introduces no R12 runtime behavior and does not fold the later content seal into the gate. Its job is to prove the cumulative R09B→R12 implementation chain after the R12A remediation and leave the exact post-gate tree available for a **separate content seal**.

## Promise ledger

- **Recommendation remains non-prescriptive.** `arc formats recommend PATH` emits every normalized candidate with compatibility, backend availability and factual tradeoffs; `selection` stays `null` and the gate rejects score/rank/winner-style output authority.
- **Benchmark corpus truth.** Real ordinary-directory TAR qualification must round-trip, R12A's portable corpus-root and logical identity remain authoritative, and empty-directory/symlink fidelity loss stays detectable rather than being normalized away.
- **Host-specific benchmark evidence.** Successful rows carry encode/decode return codes, throughput and round-trip proof; corpus SHA-256 and host identity remain explicit.
- **Diagnostics manifest integrity.** Every evidence JSON member except the manifest itself is named, byte-counted and SHA-256 hashed by `manifest.json`; the returned ZIP SHA-256 must identify the actual bundle bytes.
- **No live remote/network probe.** Diagnostics may consume only cached remote-capability evidence. The gate poisons socket connection attempts and still requires bundle generation to succeed.
- **Redaction and content boundary.** Cached remote evidence, doctor output, alias evidence and environment-derived secrets are redacted; arbitrary archive contents are never included by default.
- **Invalid configuration remains diagnosable.** Support bundle generation remains available with a typed invalid configuration result and records that failure rather than crashing or pretending the configuration is healthy.
- **Machine contract parity.** Recommendation, benchmark and diagnostics remain compatible with `arc.machine/v1` wrapping and their bundled public schemas stay queryable.
- **Cumulative campaign qualification.** The explicit `r12_gate` profile is the union of the prior R09B, R10 and R11 qualification boundaries plus R12/R12A and the gate-specific evidence probes. It intentionally preserves prior transport, recovery, mutation, batch, provenance, completion, manual and distribution contracts.
- **Separate final campaign content seal.** The gate does not mutate or bind the final content root. The next artifact must bind the exact post-gate repository identity independently.

## Authoritative Devtool surfaces

- Test profile: `r12_gate`
- First-class test: `arc-r12-gate`
- Contract job: `r12-gate-contract`
- Workflow: `r12_gate`
- Wrapper command: `./devtoolw r12-gate`

The gate is valid only when those surfaces, all prerequisite R10/R11/R12 contracts, generated docs/completion/Devtool contracts, and the cumulative pytest matrix pass with zero target diagnostics.
