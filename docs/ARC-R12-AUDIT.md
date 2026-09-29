# ARC-R12 audit — advisory intelligence, benchmarking, and support evidence

## Boundary decision

Roadmap items 16–18 ship together. `formats recommend`, benchmark evidence, and diagnostics bundles all consume the same typed backend/config/runtime/remote evidence introduced by R08–R11. Keeping them in one boundary prevents three competing capability models. The later R12 gate remains a separate qualification artifact.

## Promise ledger

- **R12-P01 factual format guidance:** `arc formats recommend PATH` emits `arc.format-recommendation/v1` with input facts, candidate compatibility, installed backend chains, metadata preservation, streaming, encryption, multipart, and random-access tradeoffs.
- **R12-P02 human agency:** recommendation evidence has `selection=null`, no scores/ranks/winner, and explicitly states that Arc does not choose a format for the user.
- **R12-P03 truthful availability:** unavailable native backend/compressor requirements remain explicit blockers rather than being silently omitted.
- **R12-P04 reproducible benchmark corpus:** `arc benchmark` accepts a user corpus or generates a deterministic seeded corpus with a stable SHA-256 identity.
- **R12-P05 host-specific benchmark evidence:** `arc.benchmark/v1` records Arc/Python/platform identity, selected backend chain, encode/decode duration and throughput, encoded size, encoded/input ratio, traditional compression ratio, verified round trip, and peak process-tree RSS where `/proc` makes it measurable.
- **R12-P06 unavailable benchmark cases:** unsupported/uninstalled formats remain explicit `unavailable` rows rather than false performance results.
- **R12-P07 redacted support bundle:** `arc diagnostics bundle` creates a ZIP of JSON-only support evidence for runtime/config provenance, backend versions/capabilities, alias/install health, doctor results, recent transaction diagnostics, and cached remote capability evidence.
- **R12-P08 privacy boundary:** diagnostics bundles perform no network probe, do not include archive contents by default, redact password-like fields, and redact sensitive environment values.
- **R12-P09 bundle integrity:** `manifest.json` records schema identity and SHA-256/byte size for every evidence JSON member; the command reports the final bundle SHA-256.
- **R12-P10 machine/package parity:** recommendation, benchmark, and diagnostics schemas are bundled/queryable, work through `--json=v1`, and survive wheel/sdist rebuilds alongside generated manpages/completion/docs.
- **R12-P11 first-class Devtool ownership:** the repository declares the `r12` profile/workflow, `arc-r12-advisory-benchmark-diagnostics` test, `r12-contract` job, and `./devtoolw r12` wrapper command.
- **R12-P12 byte-safe native version evidence:** support-bundle backend version probing treats native output as bytes first, extracts a bounded human-readable line, and cannot crash on mixed/non-UTF-8 output such as Termux `bzip2 --version`.

## Security invariants

Diagnostics code does not call the live remote capability probe. Remote support evidence is cache-only. The bundle does not walk arbitrary user files or archives and contains no archive bytes. Only Arc-owned JSON state/cache and explicit runtime/config/backend/install evidence are serialized.

## Next boundary

R12 implementation is the final implementation overlay. A separate R12 gate/final campaign seal must audit this ledger and bind the exact post-R12 source/artifact identity; implementation and seal remain separate.
