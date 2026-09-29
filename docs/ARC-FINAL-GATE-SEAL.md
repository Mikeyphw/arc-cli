# ARC R01–R12 Final Campaign Gate and Content Seal

This is the terminal qualification and content-identity contract for the ARC roadmap through R12/R12A. All implementation overlays and milestone gates remain separate. The final seal changes no Arc runtime behavior: it upgrades the historical R01–R05 seal authority to cover the complete qualified campaign and records a content-addressed identity for the exact validation transaction tree.

## Authoritative entry points

Use the checked-in Devtool wrapper:

```bash
./devtoolw gate
./devtoolw seal
```

`gate` resolves to `final_gate`, which executes every current repository test file in fresh phase processes plus source/completion/Devtool contracts. `seal` resolves to `final_seal`, which repeats that cumulative gate, executes Devtool's wrapper contract seal (tolerating only safe applicator-template byte drift for existing executable marker-bearing launchers), generates/verifies the live content ledger, and writes the final `SEALED` verdict.

The qualified pre-seal campaign identity is the R12 Gate commit prefix `95981f7` (378/378 tests, zero diagnostics). The final content root is intentionally generated from the live transaction tree rather than precomputed in the overlay.

## Seal model

`release/ARC-FINAL-SEAL.json` is a static schema-7 contract. It binds:

- seal ID `ARC-R01-R12-FINAL`;
- historical campaign base `a7028c1`;
- qualified R12 Gate commit prefix `95981f7`;
- the R01–R12 roadmap ledger;
- wrapper gate/seal entry points;
- live candidate-ledger and verdict locations;
- the requirement that the candidate be reverified immediately before commit.

During `./devtoolw seal`, `scripts/run_content_seal.py` computes a schema-5 live ledger from the exact transaction worktree. Each authoritative file contributes path, SHA-256, and byte size to a canonical content-root SHA-256. `scripts/verify_arc_final_seal.py` fails closed on changed, missing, newly-authoritative, or incorrectly-scoped paths.

The authoritative scope now explicitly includes Python source/scripts/tests, JSON Schemas, generated manpages, docs, completion, workflows, release contracts, `MANIFEST.in`, packaging metadata, launchers, and top-level project metadata. Runtime caches/evidence/build outputs remain outside the content root.

## Required gates

| Gate | Requirement | Executable evidence |
|---|---|---|
| G01 | Qualified campaign base | R12 Gate commit prefix `95981f7`, gate ledger, ancestry check |
| G02 | Complete behavior | every `tests/test_*.py` file through `run_final_gate_tests.py` |
| G03 | R01 semantics | R01 suite |
| G04 | R02 interaction/progress/safety | R02 suite |
| G05 | R03 backend qualification | R03 runtime/capability matrix |
| G06 | R04 transport/planning | R04 execution + remote transport suites |
| G07 | R05 remote-native convergence | R05 suite |
| G08 | R06 runtime/install truth | R06 suite |
| G09 | R07 explain/recovery/resume | R07 suite |
| G10 | R08 machine/capability/verification | R08 suite |
| G11 | R09 provenance + remote publication | R09A/R09B + R09B gate |
| G12 | R10 mutation/command/batch/recovery | R10/R10A/R10B/R10C + R10 gate |
| G13 | R11 configuration provenance | R11/R11A/R11B + R11 gate |
| G14 | R12 advisory/benchmark/diagnostics | R12/R12A + R12 gate |
| G15 | Packaging/manpage/schema/completion parity | core compatibility + package tests |
| G16 | Devtool wrapper contract | `devtool wrapper seal --json` |
| G17 | Exact repository content identity | live schema-5 candidate ledger + root verification |
| G18 | Final verdict | gate + wrapper seal + content candidate => `SEALED` |

## Content-root invariants

The content seal must detect all of the following:

- mutation of any sealed authoritative file;
- deletion of a sealed authoritative file;
- addition of a new authoritative source/test/schema/manpage/doc/workflow file;
- drift in generated completion, schemas, manpages, packaging manifests, or release contract;
- a candidate ledger whose path coverage no longer equals live authoritative discovery.

Unrelated ignored/runtime detritus remains outside the seal scope by design.

## Evidence tree

A successful `./devtoolw seal` writes under `.devtool/evidence/arc-final-gate/`:

```text
environment.json
gate-verdict.json
gate-summary.json
pytest-*.xml
wrapper-seal.json
candidate-seal.json
content-seal.json
final-seal-verdict.json
summary.json
```

The authoritative outcome is `final-seal-verdict.json` with `status: SEALED`, `seal_id: ARC-R01-R12-FINAL`, the live `content_root_sha256`, the qualified gate prefix, and the R01–R12 roadmap.

## Re-running

```bash
./devtoolw gate
./devtoolw seal
```

Re-running recomputes the entire behavior gate and live root. Any authoritative drift after the campaign seal fails closed instead of inheriting historical green evidence.
