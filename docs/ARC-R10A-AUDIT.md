# ARC-R10A Audit — backend command display truth

## Scope

Operator feedback identified a truth mismatch in interactive output: Arc printed the Arc wrapper invocation under `Command`, even though that line is intended to teach/show what native backend Arc actually uses.

## Delivered

- Automatic interactive `Command` output is sourced from the recorded `ExecutionStage`, using exact redacted backend argv/pipeline.
- The received Arc invocation is no longer printed automatically as the backend command. It remains available for machine invocation identity, alias provenance, transaction journals, and parser diagnostics.
- Backend preprocessing/compression pipelines render as the actual pipeline Arc executes.
- SSH remote-native delegation is not mislabeled as the archive backend: the local SSH argv is labeled `Remote`, while `--show-command` is forwarded so the delegated Arc prints its actual backend command. Streaming pipelines continue to use the shared execution-stage authority.
- `--show-command` and dry-run remain explicit native-command surfaces; secrets continue to be redacted by `ExecutionStage`.
- JSON, quiet, and non-TTY execution do not gain unsolicited human output.

## Validation

The R10A regression slice covers exact backend argv, pipelines, retained received-invocation identity, execution-plan compatibility, and remote-native command behavior. The separate R10 gate remains deferred until R10B is implemented.
