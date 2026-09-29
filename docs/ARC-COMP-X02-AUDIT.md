# ARC COMP-X02 — logical merge with automatic safe concat

Status: IMPLEMENTED; separate COMP-G1 qualification remains pending.

## Promise ledger

- `arc merge INPUT...` is a first-class command and packaged alias surface.
- COMP-X01 owns deterministic output name/format resolution; COMP-X02 consumes it unchanged.
- `--strategy=auto` selects concat only for same-format single compressed streams with the same output stream format.
- TAR+compression and archive containers repack logical members; they are never byte-concatenated as a logical merge.
- `--strategy=concat` fails closed when the safe concat predicate is false.
- Repack owns explicit `--member-conflict=fail|replace|skip|rename` semantics independent from final `--destination-policy`.
- Local publication uses the existing transaction/destination-policy machinery. `--delete-inputs` runs only after publication and optional verification.
- Remote inputs use Arc's existing read-side staging. Remote merge output is deliberately not claimed yet.
- Legacy JSON and `--json=v1`, `arc explain merge`, dry-run, completion, generated manuals, package aliases, batch preflight, and Devtool ownership are first-class.
- COMP-X03 remains separate and owns exact split/join volumes.
