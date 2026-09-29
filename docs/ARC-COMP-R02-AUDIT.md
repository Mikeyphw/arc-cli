# ARC COMP-R02 Audit — mixed-stream logical repack convergence

Status: **IMPLEMENTED; apply before COMP-G1.**

## Audit trigger

COMP-G1 qualification exercised the COMP-X01 promise that mixed input formats may be merged when the destination format is explicit. Output resolution was correct, but COMP-X02's repack executor extracted each stream source to a separate file and then asked the stream backend to create one gzip/xz/bzip2/zstd output from multiple files. Stream backends correctly reject that shape, so explicit mixed-stream merge failed after planning.

## Remediation contract

- Safe same-format stream inputs retain the existing zero-reencode `concat` path under `--strategy=auto`.
- A stream-output `repack` is legal when **all inputs are streams**. Arc decompresses each source in user-specified order, concatenates the logical bytes, and compresses that single logical byte stream once into the requested output format.
- A container/archive input cannot be silently flattened into a stream output because named-member semantics would be lost or become ambiguous. Arc fails closed before mutation and tells the user to choose a container output format.
- Transactional unpublished-candidate cleanup from COMP-R01 remains authoritative for the repaired path.
- The remediation does not weaken mixed-format inference: mixed inputs without an explicit destination format still fail closed.

## Qualification added

`tests/test_comp_x02_merge.py` now proves mixed gzip+xz → gzip logical repack and explicit container→stream rejection. COMP-G1 must independently retain the same mixed-format promise in its cumulative qualification matrix; that gate-specific coverage is deliberately not bundled into this remediation overlay.

## Gate consequence

COMP-G1 remains qualification-only and follows COMP-R02. Its final matrix must preserve both zero-reencode safe concat and decompressed-byte mixed-stream repack, plus temporary-file cleanup on the repaired execution path.
