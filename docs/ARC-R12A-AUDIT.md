# ARC-R12A audit — benchmark corpus truth and portable round-trip verification

## Why this completion overlay exists

The separate R12 gate audit exercised real directory corpora instead of only generated single-file corpora. Real directory qualification exposed two distinct concerns that must not be conflated. First, absolute source paths made verification inspect the wrong extracted root. Second, metadata-rich corpora containing symlinks/empty directories can legitimately expose format/backend fidelity differences; a cross-format smoke test must not assume every ZIP provider preserves the exact same metadata set as TAR. R12A therefore uses a portable ordinary-directory corpus for real TAR/ZIP path/content round trips and separately requires metadata-loss detection to remain truthful.

The same audit found two related truth gaps: directory corpus identity ignored empty directories and symlink targets, allowing a lossy extraction to be mislabeled as verified, and format recommendation resolved the final path component before checking `is_symlink()`, making the advertised symlink input kind unreachable.

## Delivered promises

- **R12A-P01 portable benchmark input:** benchmark create subprocesses run from the corpus parent and pass only the corpus basename, so archives do not encode the host's absolute path prefix.
- **R12A-P02 real directory verification:** extracted directory identity is checked at the stable corpus root actually created by Arc; real TAR/ZIP ordinary-directory path/content round trips must verify, while metadata-rich fidelity loss remains explicit rather than being treated as a harness failure.
- **R12A-P03 logical corpus identity:** directory corpus SHA-256 covers normalized regular-file paths/content, symlink targets, and meaningful empty directories. Dropping any of those invalidates round-trip proof.
- **R12A-P04 empty-corpus schema truth:** `arc.benchmark/v1` permits zero regular files and requires explicit directory/symlink counts.
- **R12A-P05 symlink input truth:** `formats recommend` preserves a final-component symlink as `kind=symlink` instead of silently reclassifying it as its target.
- **R12A-P06 benchmark corpus boundary:** benchmark accepts a regular file or directory corpus and rejects a symlink corpus instead of silently following it.
- **R12A-P07 first-class Devtool ownership:** `r12a` profile/workflow/test/contract/wrapper surfaces own this completion boundary before the separate R12 gate.

## Next boundary

After R12A applies, rerun the separate R12 gate over R12 + R12A. The final campaign content seal remains a later artifact; it must bind the exact post-gate source identity rather than being folded into this remediation overlay.
