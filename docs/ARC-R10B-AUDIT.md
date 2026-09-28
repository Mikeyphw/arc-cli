# ARC-R10B Audit — versioned machine batch input

## Scope

Arc already had `convert --batch`, but that is a resumable multi-conversion feature rather than a general machine submission surface. R10B adds a distinct generic batch operation without shell-string execution.

## Contract

- `arc batch [FILE|-]` accepts `arc.batch-input/v1`; `arc schema batch-input-v1` exposes the bundled Draft 2020-12 schema.
- Input is UTF-8 JSON bounded to 4 MiB, 1–1024 operations, and at most 4096 NUL-free argv tokens per operation.
- Each operation has a unique machine ID and a non-empty argv string array. Every nested argv is syntactically pre-parsed before the first operation executes; unknown commands/options, recursive `batch`, unknown JSON fields, binary `extract --stdout`, NUL argv, and interactive password prompts are rejected before execution.
- `on_error=stop|continue` is explicit. `allow_failure=true` records an expected non-zero result without failing the whole batch. Exit 130 is always treated as an interrupt: it stops the batch even under `continue` or `allow_failure` and propagates 130.
- Operations execute sequentially through Arc's own parser/runtime—not through a shell. Context isolation prevents one nested invocation's execution-plan display state from leaking into the parent batch.
- Results use `arc.batch-result/v1`; argv and captured output redact archive password values. Parseable JSON stdout is additionally surfaced as structured `result`.
- `--validate-only` performs the same full input + nested-argv preflight without operation execution. Bare `--json` returns the batch result directly; `--json=v1` wraps it in `arc.machine/v1`.

## Ownership

The schema is package data, generated command/man documentation includes `arc-batch(1)`, completion knows the command/options, and Devtool owns `batch-contract`, `arc-r10b-machine-batch`, workflow `r10b`, and wrapper command `r10b`.
