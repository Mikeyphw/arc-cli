# ARC COMP-R03 — machine-schema registry convergence

Status: **IMPLEMENTED; apply before COMP-G1.**

COMP-G1 qualification found one remaining ownership drift after COMP-X03: the public `arc schema` registry correctly exposed `split-manifest-v1`, but the active `scripts/check_machine_contract_r09b.py` contract still enumerated the pre-X03 schema set. The gate therefore failed closed even though runtime/schema/package behavior was correct.

## Remediation

- Extend the active R09B machine-schema contract authority and its exact public-registry regression with `split-manifest-v1`.
- Preserve the historical pre-R09B checker unchanged; the active Devtool `machine-contract` job already points to the R09B checker and remains the current authority.
- Add a focused regression that proves `split-manifest-v1` is queryable and valid JSON Schema 2020-12, then executes the active machine checker end-to-end.
- Requalify the X03 schema/package path with machine-capability and distribution tests.
- Add first-class `comp_r03` Devtool profile/test/contract/workflow/wrapper ownership.

## Gate separation

COMP-R03 contains no COMP-G1 test, gate ledger, or final qualification status. After this remediation is applied, COMP-G1 must re-run the active machine contract together with X01/X02/X03/R01/R02/R03 and the cumulative composition matrix. Any further gap remains a separate remediation rather than being folded into the gate.
