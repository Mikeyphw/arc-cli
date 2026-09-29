# ARC COMP-X03 — exact split + join volume protocol

Status: **IMPLEMENTED; COMP-G1 qualification pending.**

## Delivered contract

- `arc split SOURCE (--size SIZE | --parts N)` is a first-class exact byte-volume operation for any regular file, independent of archive format.
- Default parts are ordered `<prefix>.partNNN` files with configurable output prefix/directory and digit width.
- Split emits `arc.split-manifest/v1` by default. The manifest records the original filename, whole-file byte length/checksum, split parameters, and ordered per-part size/checksum evidence.
- Supported checksum authorities are SHA-256 (default), SHA-512, and BLAKE2b.
- Zero-byte inputs produce one zero-byte part. Exact-boundary sizes do not create a trailing empty part. `--parts` divides bytes deterministically without empty parts.
- Split verifies the published part set against the source checksum by default. `--delete-source` is rejected when verification is disabled and runs only after publication/proof.
- Local and remote read-side sources reuse Arc's existing staging authority; outputs/manifests remain local in COMP-X03.
- `arc join` accepts the manifest directly or any ARC-generated `.partNNN`; a part discovers the sibling `<prefix>.arc-split.json` automatically.
- Manifest-backed join is strict: part presence, order, size, per-part checksums, total byte length, and whole-file checksum are checked before/while publication.
- Reconstruction is written to a same-filesystem transaction-owned candidate and atomically published through Arc's destination policy authority.
- `--allow-missing-manifest` is an explicit weaker mode for contiguous `.partNNN` concatenation; it is reported as unanchored and cannot be combined with `--delete-parts`.
- `--delete-parts` requires manifest-backed verification and runs only after successful publication.
- Split/join participate in `arc explain`, `--dry-run`, legacy JSON, machine-v1 envelopes, generic `arc batch`, aliases, completion, generated manuals, packaged schema resources, wheel/sdist data, and Devtool ownership.
- Native 7z/RAR multipart archive creation is intentionally **not** implemented by `arc split`; future native volume creation remains a separate `--volume-size` surface.

## Safety / failure semantics

1. Fail-policy destination collisions are preflighted before split reads the source or join writes a candidate.
2. Split publishes data parts first and the manifest last; the manifest therefore acts as the commit record for a complete generated set.
3. Join refuses output aliases of parts or the manifest.
4. Corrupt/missing parts fail before final output publication.
5. Transaction journals register unpublished split/join temporary state for recovery cleanup.
6. Destructive source/part cleanup is downstream of verification and publication only.

## COMP-G1 work intentionally left separate

COMP-G1 remains the authoritative cumulative composition gate. It must qualify X01-X03 together, including interruption/recovery, remote staging, machine/batch surfaces, generated docs/completion, distribution packaging, and destructive cleanup ordering. No gate implementation is folded into COMP-X03.
