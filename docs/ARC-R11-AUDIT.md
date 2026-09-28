# ARC-R11 Audit — Configuration provenance and explainability

## Boundary

R11 remains standalone after the merge-window audit. R12 depends on R11 evidence but owns advisory recommendation, benchmark, and support-bundle behavior rather than configuration resolution.

## Problem found

The pre-R11 loader caught TOML/read failures and returned `{}`. That erased the distinction between “no configuration” and “configuration exists but is invalid,” so doctor/config-adjacent surfaces could not explain the failure. Scalar defaults were also normalized independently inside CLI code, making future provenance output vulnerable to drift from runtime behavior.

## Promise ledger

| Promise | Delivery |
| --- | --- |
| Effective configuration | `arc config show --effective` reports normalized values and selected sources. |
| Explain one key | `arc config explain KEY` reports ordered layers and the winning source/value. |
| Resolved profile inspection | `arc config profile NAME` reports the raw profile plus effective scalar provenance. |
| Five-layer provenance | built-in → config → environment → profile → CLI is represented explicitly. |
| Backend provenance | `backends.ROLE` includes compiled defaults, TOML and `ARC_BACKEND_*`. |
| Invalid config truth | TOML/read/type/value failures are retained as typed diagnostics; normal runtime fails closed. |
| Unknown-key truth | unknown supported-table/profile/remote keys are warnings rather than being silently ignored by audit surfaces. |
| Doctor parity | doctor consumes the same load/validation result instead of reparsing a narrower subset. |
| Shared runtime authority | scalar profile/default normalization uses the same resolver used by config inspection. |
| Machine contract | `arc.config-inspection/v1` is bundled/queryable and can be wrapped by `arc.machine/v1`. |
| Generated surfaces | command identity, manpages/reference, completion and package data include the new command/schema. |
| Devtool ownership | `arc-r11-config-provenance`, `r11` workflow/profile and `./devtoolw r11` own targeted validation. |

## Deliberate boundaries

- `--cli OPTION=VALUE` on inspection commands models the explicit CLI layer without executing a mutating Arc operation.
- Secret-bearing password variables are not configuration-provenance keys and are never surfaced by this contract.
- R12 diagnostics bundles may consume this evidence but do not become configuration authority.

## R11A completion findings

The first gate audit found two R11-owned gaps before qualification. Supported environment overrides were part of the declared precedence chain but were not part of the typed diagnostic result, so `ARC_LEVEL=bogus` could make effective resolution fail while `arc doctor` still reported configuration healthy when no TOML file existed. The same audit found file/profile integer validation coercing TOML floats/booleans through `int(...)` despite the contract describing typed integers.

R11A closes those gaps by validating supported environment overrides in the shared result model, keeping invalid effective inspection machine-readable without manufacturing an effective value, ordering doctor diagnostics before the no-file fast path, rejecting empty backend environment preference lists, and requiring actual TOML integers for integer-valued file/profile/remote/cache fields.

## Gate obligations

The separate R11 gate must re-audit invalid TOML, invalid typed values, invalid supported environment values with and without a TOML file, unknown keys, all five precedence layers, backend environment overrides, profile resolution, JSON/machine/schema parity, doctor/runtime behavior, generated docs/completion/package inclusion, and cumulative R10 compatibility.
## R11B completion finding

The widened gate audit found one final doctor-only split-brain after R11A: an invalid `ARC_BACKEND_*` value was correctly represented as an `invalid_environment` configuration issue, but `collect_doctor_report()` then called backend inventory with an empty config while still inheriting the process environment. Backend preference resolution re-read the same invalid override and raised, so doctor crashed after already diagnosing the configuration failure.

R11B makes backend inventory environment-explicit. Runtime and ordinary inventory behavior still inherit the process environment, while doctor passes an empty environment mapping only after the shared configuration result is invalid. That preserves the original failure evidence and lets downstream backend/install/generated-surface checks finish using safe built-in preferences. The R11 gate must prove the full invalid environment matrix, including invalid backend overrides, cannot re-enter a failing resolver after the configuration check.
## R11 gate result: QUALIFIED

The separate cumulative R11 gate re-audits R11, R11A, and R11B together. It proves runtime-vs-explain parity, valid/invalid `arc.config-inspection/v1` shape, every supported invalid environment override through doctor, strict typed input behavior, generated/package parity, and cumulative R10 compatibility. No additional runtime remediation was required after R11B. R12 remains outside this configuration-authority boundary.

