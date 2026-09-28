# ARC-R11 cumulative gate

Status: QUALIFIED

## Scope

This is the separate qualification gate for the complete R11 configuration-authority boundary: R11 effective configuration/provenance, R11A strict typed environment and TOML diagnostics, and R11B doctor environment isolation. R12 advisory intelligence remains outside this gate.

## Promise ledger

### Configuration authority and provenance

- Effective scalar configuration is resolved by one authority with precedence **built-in → config → environment → profile → CLI**.
- `arc config show --effective`, `arc config explain KEY`, and `arc config profile NAME` expose the same selected values and sources used by runtime default/profile application.
- The gate directly proves **runtime-vs-explain parity** for scalar configuration and explicit CLI precedence.
- Backend preferences retain compiled-default/config/`ARC_BACKEND_*` provenance through the same resolver used by runtime backend inventory.
- Unknown supported configuration remains warning-level and visible rather than being silently discarded.

### Typed failure and diagnostic behavior

- Invalid TOML, invalid supported types/values, and every **invalid supported environment** override fail closed instead of becoming an empty/default configuration.
- File/profile integer fields reject TOML floats and booleans rather than coercing them through Python integer conversion.
- Invalid effective inspection stays structured and does not manufacture effective values the runtime would refuse to use.
- Normal runtime remains fail-closed, while config/schema/help/doctor diagnostic surfaces remain available.
- **doctor environment isolation** prevents an already-diagnosed invalid `ARC_BACKEND_*` value from being re-read by downstream backend inventory; doctor completes with the configuration failure preserved.

### Machine/schema and generated surfaces

- `arc.config-inspection/v1` remains bundled, queryable, and package-visible.
- Valid effective payloads and invalid diagnostic payloads are checked against the bundled schema's declared object/resolution/layer shape and additional-property boundary.
- `--json=v1` continues to wrap configuration evidence in `arc.machine/v1` without losing typed failure evidence.
- Generated command docs, manpages, completion, distribution metadata, and Devtool configuration remain synchronized.

### Compatibility and ownership

- R10 command-truth, machine-batch, mutation-policy convergence and cumulative gate contracts remain green under the R11 configuration changes.
- R08 stable machine-schema and R06 doctor/runtime/install-truth behavior remain compatible.
- R11, R11A, and R11B keep first-class test/workflow/wrapper ownership.
- The cumulative gate itself is first-class: `arc-r11-gate`, `r11_gate`, `r11-gate-contract`, and `./devtoolw r11-gate`.

## Gate-specific probes

The gate adds deterministic coverage for previously implicit edges:

1. valid five-layer effective output matches the bundled configuration schema shape;
2. invalid environment output remains schema-shaped and omits manufactured effective values;
3. runtime scalar/profile application and config-explain authority resolve to identical values;
4. explicit runtime CLI values win through the same resolver shown by explain;
5. every supported invalid scalar/backend environment variable leaves doctor diagnostic and non-crashing;
6. unknown-key warnings remain visible but nonfatal;
7. all R11/R11A/R11B first-class Devtool identities remain declared.

## Authoritative validation

The repository owns this gate as:

- first-class test: `arc-r11-gate`;
- test profile: `r11_gate`;
- Devtool workflow: `r11_gate`;
- contract job: `r11-gate-contract`;
- wrapper entry: `./devtoolw r11-gate`.

The cumulative matrix contains **134 tests** across R11/R11A/R11B, the gate-only invariants, R10 gate/mutation/batch/command-truth compatibility, R08 machine evidence, R06 doctor/runtime truth, CLI integration, completion/manual generation, and distribution packaging.

## Result

R11 configuration provenance, strict diagnostic behavior, and doctor isolation are qualified. **R12 advisory intelligence**, benchmarking, and support evidence is the next implementation boundary.
