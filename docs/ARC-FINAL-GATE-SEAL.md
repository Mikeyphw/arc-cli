# ARC R01–R05 Final Gate and Seal

This is the cumulative qualification contract for the normalized archive CLI after ARC-R01 through ARC-R05. Feature overlays intentionally used narrow validation. The final overlay is different: it proves the campaign as one wrapper-driven system and records a content-addressed seal for the exact repository tree.

## Authoritative entry points

Use the checked-in Devtool wrapper:

```bash
./devtoolw gate
./devtoolw seal
```

`gate` resolves to the `final_gate` EXO workflow. `seal` resolves to `final_seal`, which repeats the full gate and then requires both Devtool's wrapper-surface final seal and the repository content seal before producing the final verdict. The overlay itself validates by invoking `./devtoolw seal`, so the same repository-facing entry point must succeed before Devtool commits the overlay.

## Gate model

The committed `release/ARC-FINAL-SEAL.json` is a static schema-6 seal contract. It defines the campaign, authoritative scope, wrapper entry points, base R05 ancestry, and where live seal evidence is written. During `./devtoolw seal`, ARC computes a schema-5 candidate ledger from the exact post-gate transaction tree, recording every authoritative file with SHA-256 and size and deriving one canonical root SHA-256. The candidate is immediately verified. Devtool then invokes a read-only `before_commit` finalizer that re-verifies the same candidate ledger before commit. The final-contract suite proves generator/verifier round-trip, fail-closed mutation detection, authoritative-path coverage, and ancestry from the R05 base `a7028c1`.

The content seal alone is not a release verdict. `final_seal` requires these layers in order:

1. cumulative behavior/packaging gate;
2. Devtool `wrapper seal` over launchers, help, discovery, completion state, resolution, doctor integration, and canonical wrapper execution provenance;
3. exact content-seal verification;
4. final verdict aggregation.

Only step 4 may write `status: SEALED`.

## Required gates

| Gate | Requirement | Executable evidence |
|---|---|---|
| G01 | Repository content identity | static schema-6 release contract + post-gate schema-5 live ledger + exact authoritative-path/hash verification + R05 ancestry check |
| G02 | Full behavior | complete pytest tree in `run_final_gate_tests.py` |
| G03 | R01 semantic correctness | R01 regression suite |
| G04 | R02 interaction, completion, progress, safety | R02 suite |
| G05 | R03 native capability qualification | full R03 qualification including large manifest |
| G06 | R04 SSH/rclone transport, cache, remote completion, native rendering | R04 suites |
| G07 | R05 SSH-native execution convergence | R05 suite |
| G08 | Packaging | wheel + sdist build, metadata, entry point, package contents |
| G09 | Completion artifact parity | committed `_arc` equals generator output |
| G10 | Version and CLI coherence | pyproject, package version, CLI version/discovery surfaces |
| G11 | Devtool wrapper contract | `./devtoolw gate`/`seal` configuration plus Devtool `wrapper seal` |
| G12 | Promise ledger and final verdict | R01–R05 audits + `write_final_seal_verdict.py` |

## Wrapper workflows

`final_gate` runs syntax/source-hygiene and completion/repository-contract jobs before the cumulative gate job. The gate job bootstraps only the Python packages needed to execute the complete test tree and writes `.devtool/evidence/arc-final-gate/gate-verdict.json`.

`final_seal` contains the same gate DAG and then adds:

- `wrapper-seal`: `scripts/run_wrapper_seal.py`, which calls Devtool's own `wrapper seal --json` and stores the machine result;
- `content-seal`: exact repository-tree verification;
- `verdict`: aggregation that fails closed unless gate, wrapper seal, and post-gate live content ledger are all PASS; success is `SEALED`.

## Capability skips

Native backend availability is environment-specific. The R03 matrix records missing binaries and unsupported operations as `SKIPPED_BACKEND_UNAVAILABLE` or `SKIPPED_CAPABILITY_UNSUPPORTED`; they are not counted as runtime PASS. Installed compatible backends are exercised for real, while deterministic unit/integration tests cover resolver behavior for optional backends.

SSH/rclone credentials or a live private remote are not required for the seal. R04/R05 use controlled fake-provider integration tests for transport semantics. The final verdict records which transport/backend binaries were installed on the validation host.

## Evidence tree

A successful wrapper-driven seal writes approximately:

```text
.devtool/evidence/arc-final-gate/
├── pytest.xml
├── environment.json
├── gate-verdict.json
├── gate-summary.json
├── r03/
│   ├── qualification.json
│   ├── capability-matrix.json
│   ├── backend-matrix.json
│   ├── safety-matrix.json
│   ├── resilience-matrix.json
│   └── summary.json
├── package/
│   └── summary.json
├── seal-verification.json
├── wrapper-seal.json
├── content-seal.json
├── final-seal-verdict.json
└── summary.json
```

The authoritative workflow outcome is `final-seal-verdict.json` with `SEALED`, including the live `content_root_sha256`. The read-only `before_commit` hook re-verifies the exact candidate ledger created by that same workflow. Re-running `./devtoolw seal` recomputes the live root and will fail if authoritative bytes or path coverage have drifted.

## Re-running

```bash
./devtoolw gate
./devtoolw seal
```

The lower-level Python scripts exist for workflow jobs and debugging, but they are not the authoritative human/agent entry points.


## Seal scope

The committed content seal covers ARC's exact authoritative source/config/docs/test set. Runtime, editor, transaction, package-build, and unrelated untracked worktree files are outside the seal scope; every authoritative path must be covered and every sealed byte must match exactly.


## Live validated-tree seal

V6 deliberately does **not** precompute the final content root in the overlay. A host-independent static root proved brittle because the cumulative gate intentionally exercises platform-specific code paths before sealing. Instead, `scripts/run_content_seal.py` executes only after the behavior gate and Devtool wrapper seal have passed. It writes `.devtool/evidence/arc-final-gate/candidate-seal.json`, containing the exact authoritative path list, SHA-256 for every file, sizes, and a canonical content root derived from the live transaction tree.

`release/ARC-FINAL-SEAL.json` is the committed seal contract, not a self-referential content ledger. It is itself included in the authoritative live root. The artifact `before_commit` finalizer has no repository write capability: it re-runs `scripts/verify_arc_final_seal.py` against the candidate produced during validation and blocks the commit if any authoritative byte/path has changed after the gate.

The Devtool-created Git commit is the durable repository identity; the run evidence records the corresponding validated content root. Together they provide a reproducible campaign seal without requiring a root to be guessed on a different host before Termux validation runs.
