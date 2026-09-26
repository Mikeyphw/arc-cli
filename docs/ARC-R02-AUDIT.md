# ARC-R02 Interaction, Completion, Progress, and Safety Audit

ARC-R02 is the second remediation overlay after the initial implementation audit. It intentionally does **not** perform the full release/backend qualification matrix; that remains ARC-R03, followed by the separate gate/seal overlay requested for the campaign.

## Promise ledger

| Promise | R02 result |
| --- | --- |
| Command-specific Zsh completion | Delivered. Python owns a per-command option grammar and the generated Zsh function delegates dynamic candidates to it. |
| Context-aware fzf single/multi behavior | Delivered. `arc __complete-mode` decides the mode; `--multi` is used only for multi-value contexts. |
| Automatic fzf selection without an `--fzf` flag | Preserved and qualified with a NUL-safe chooser protocol. |
| ripgrep acceleration | Delivered with `rg --files --no-config --hidden --null`; Python traversal remains the fallback. |
| Explicit Yazi chooser modes | Delivered for archive/input/output roles with command-role validation and cleanup on all exits. |
| NUL-safe arc-owned candidate protocols | Delivered for rg, fzf, and shell dynamic completion. |
| Odd filename handling | Delivered for TAR NUL manifests, line-oriented backend argv fallback, and exact-name Info-ZIP extraction; native newline ZIP round-trip is covered. |
| Rich detailed/truthful progress | Improved. Byte/file/current-member progress is shown where telemetry exists; unknown telemetry is explicitly indeterminate. |
| RAR/7z extraction safety convergence | Delivered through technical metadata parsing and fail-closed handling of unknown/special entry types. |
| Named profiles | Delivered with profile-before-CLI precedence, ordered filtering rules, and completion. |
| `--password-env` completion | Delivered from the current environment. |
| Generated `_arc` parity | Delivered and validated byte-for-byte against `arc completion zsh`. |

## Audit-loop discoveries fixed during R02

1. Direct-argv fallback for newline filenames initially placed native passthrough options after `--`, which would have converted backend options into filenames. Options now precede the end-of-options separator.
2. Info-ZIP stores newline-containing member names correctly, but `unzip` normal extraction sanitizes the newline in the output path. Arc now performs exact-name extraction for those members with `unzip -p`, preserving the normalized member name while retaining the native backend for file data.
3. Stream compressors do not expose useful member/byte telemetry. Their Rich UI is now indeterminate instead of displaying a determinate bar that can only jump from zero to complete.
4. Yazi chooser roles were syntactically accepted too broadly. Invalid command/role combinations now fail before invoking Yazi.
5. 7-Zip technical metadata with an unfamiliar non-empty entry type could still be downgraded to a regular file. Unknown/special textual types now fail closed.

## Qualification performed before packaging

- Full Python suite: 56 tests passing.
- R02 targeted interaction suite: command grammar (including attached long-option values), profiles, fzf, ripgrep, Yazi, progress, RAR/7z metadata, odd backend argv, and native ZIP newline round-trip.
- Native TAR newline-filename round-trip.
- Native ZIP/Unzip newline-filename round-trip through the exact-name path.
- Generated completion parity check.
- Python compile checks for all modified modules.

## Targeted Devtool validation scope

The ARC-R02 overlay does not rerun build/package/SBOM/release validation. Its required validation restores the managed host-Python environment, then runs only `tests/test_r02_interaction.py`, `tests/test_completion.py`, and a small R01 semantic regression slice covering filtering, zero-write dry-run, stream conflicts, list passthrough, add/update separation, stream detection, and empty-selection behavior. The artifact validator additionally checks the R02 contract and compiles only the modified Python surfaces.

## Deferred to ARC-R03

- Complete runtime `format × backend × operation` qualification for every installed backend.
- Capability-aware backend resolver/fallback matrix.
- Real 7z/RAR runtime qualification on hosts where those binaries are present.
- Password/encryption matrix, corruption matrix, huge-manifest stress, and signal/interruption matrix.
- Final evidence ledgers across all backend combinations.

The campaign's later dedicated gate/seal overlay remains intentionally separate from R03.
