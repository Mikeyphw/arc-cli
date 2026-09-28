# ARC-R10 cumulative gate

Status: QUALIFIED

## Scope

This is the separate qualification gate for the complete R10 mutation/execution wave. It audits R10, R10A, R10B, and the gate-discovered R10C remediation as one cumulative boundary without adding new runtime behavior.

The gate keeps R11 configuration provenance separate. Runtime source is unchanged by the gate artifact; only qualification tests, Devtool ownership, wrapper/docs, and this promise ledger are added.

## Closed promise ledger

### Mutation and publication policy

- `DestinationPolicy` remains the shared `fail|replace|rename|skip-identical` authority for destination-producing mutation paths.
- Explicit extract destination policy is authoritative over legacy collision flags. Replace, rename, fail, and proven `skip-identical` behavior are all exercised against real archives.
- `skip-identical` proves regular-file bytes and symlink targets before skipping a conflict; mismatches fail without mutating the existing destination.
- Rename preservation retains the previous object under a non-colliding `.old.N` path and publishes the requested member to the original destination.
- Create/convert publication continues to use the shared policy while R07-owned resume replacement remains limited to batch-owned, verified destinations.
- In-place `add`, `update`, and `remove` use non-clobbering pre-mutation `--backup-existing` snapshots. The gate explicitly proves update snapshots are byte-exact pre-mutation archives and an occupied explicit backup fails before the archive changes.
- Remote-native mutation forwards destination-policy and backup authority rather than silently diverging from local Arc semantics.

### Interactive execution truth

- Semantic mutation progress remains `scan -> encode -> verify -> publish`, with batch position and ratio context where evidence exists.
- JSON, quiet, and automatic non-TTY execution remain free of unsolicited progress output.
- Human `Command` output comes from the exact redacted backend `ExecutionStage`, never from the Arc wrapper invocation.
- SSH remote-native delegation is labeled as `Remote`; delegated `--show-command` exposes the actual remote backend command.
- Received Arc argv remains a separate redacted machine/provenance identity.

### Machine batch contract

- `arc batch` accepts the bundled `arc.batch-input/v1` schema and executes argv arrays through Arc itself, never shell command strings.
- The whole request is bounded and preflighted before operation 1: unique IDs, known commands/options, no recursion, no NUL argv, no binary `extract --stdout`, and no interactive password prompt.
- `stop|continue`, `allow_failure`, and unconditional exit-130 interruption semantics remain explicit.
- Secret values are redacted from argv/stdout/stderr evidence.
- `--validate-only`, bare `--json`, and `--json=v1` remain stable machine surfaces.
- The gate proves aggregate JSON remains a single clean JSON record even when an inner operation normally renders a Rich human table; that human output is captured inside the operation result rather than leaking beside the batch record.

### Recovery and dependency boundaries

- R07 transaction interruption and cleanup ownership remain green; R10 does not weaken recovery authority.
- R08 machine-schema contracts, R09B remote capability/publication truth, completion, generated manuals, aliases, CLI safety, and distribution packaging remain compatible.
- No R11 configuration-provenance implementation is folded into this gate.

## Gate-specific probes

The gate adds deterministic coverage for previously implicit edges:

1. explicit extract rename preserves the old target and publishes the new member;
2. `skip-identical` proves symlink target identity and fails closed on mismatch;
3. update backup is the exact pre-mutation archive;
4. occupied explicit update backup fails before archive mutation;
5. machine batch JSON is a single clean record with human inner output captured;
6. backend command rendering redacts stage secrets;
7. all R10/R10A/R10B/R10C first-class Devtool identities remain declared.

## Authoritative validation

The repository owns the gate as:

- first-class test: `arc-r10-gate`;
- test profile: `r10_gate`;
- Devtool workflow: `r10_gate`;
- contract job: `r10-gate-contract`;
- wrapper entry: `./devtoolw r10-gate`.

The cumulative gate matrix contains 240 tests across the R10 family, R04/R05 transport and execution-plan behavior, R07 recovery, R08 machine evidence, R09B publication truth, convert/CLI/safety/aliases, completion/manual generation, and distribution packaging.

## Result

R10 mutation policy, recoverability, backend-command truth, machine batch execution, and semantic execution UX are qualified. R11 configuration provenance and explainability is the next implementation boundary.
