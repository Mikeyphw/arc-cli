# ARC COMP-R01 Audit — temporary lifecycle hardening

Status: IMPLEMENTED; apply before COMP-G1.

## Audit trigger

During COMP-G1 preparation, temporary-state qualification exposed a systemic gap: transaction journals recorded unpublished candidates for recovery, but a failed/interrupted `transaction_scope()` did not itself remove those registered paths. `merge` and `join` could therefore leave same-filesystem candidate files after an exception or Ctrl+C. Separately, remote read staging caught `Exception` rather than `BaseException`, so `KeyboardInterrupt` could bypass its unlink path.

## Remediation contract

- Arc-owned temporary state is ephemeral on every normal command exit: success, handled error, and interruption.
- Durable transaction evidence remains truthful. Automatic cleanup does not rewrite `failed` or `interrupted` into success; a path that cannot be removed stays registered and remains eligible for explicit `arc recover --cleanup`.
- Transaction cleanup is centralized at `transaction_scope()` so newly added transaction-owned candidates inherit the same lifecycle instead of each command reimplementing interruption cleanup.
- Remote read staging removes `arc-remote-*` state on `BaseException`, including `KeyboardInterrupt` and `SystemExit`.
- Stdin materialization removes `arc-stdin-*` if copy/materialization aborts before ownership is returned to the caller.
- Backend manifest creation removes `arc-manifest-*` if writing aborts before execution metadata takes ownership.
- Existing command-local `finally` cleanup remains valid defense in depth.

## Qualification added

`tests/test_comp_temp_cleanup.py` proves:

1. registered transaction temps disappear on success;
2. registered transaction temps disappear on ordinary failure while journal status stays `failed`;
3. registered transaction temps disappear on `KeyboardInterrupt` while journal status stays `interrupted`;
4. remote read staging cleans on interruption;
5. stdin materialization cleans on interruption;
6. failed `arc merge` leaves no `.arc-merge-*` candidate;
7. failed `arc join` leaves no `.arc-join-*` candidate.

The remediation profile also reruns R03 interruption qualification, R04 remote transport, R07 recovery, COMP-X02 merge, COMP-X03 split/join, generic machine batch, and CLI integration.

## Gate consequence

COMP-G1 must now treat temp-file hygiene as a first-class cumulative promise. Its interruption matrix must cover success, ordinary error, and Ctrl+C/SIGINT for composition candidates and staging paths. COMP-G1 remains qualification-only; this remediation is deliberately separate implementation work.
