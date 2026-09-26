# ARC-R05 audit — SSH-native execution convergence

ARC-R05 follows ARC-R04's transport/staging work. It does not replace the conservative `auto` strategy; it expands the explicit `--execution=remote` path for users who intentionally want Arc to execute on the SSH host that owns the archive.

## Delivered behavior

- `--execution=remote` remains SSH-only. rclone has no remote process execution surface and is rejected explicitly.
- SSH-native execution now supports `identify`, `list`, `test`, `remove`, `create`, `add`, `update`, and `extract` within strict locality constraints.
- `create`/`add`/`update` require explicit input operands and every input must resolve to the same SSH endpoint as the archive. Remote paths are translated to their host-local path before being passed to remote Arc.
- `extract` supports `--stdout` back to the caller or an SSH `-o/--output` on the same endpoint. Local or cross-host output paths are rejected.
- `remove` requires explicit member names because an SSH subprocess is not assumed to have an interactive TTY suitable for Arc's member picker.
- Inline `--include`/`--exclude` rules are forwarded in order. `--include-from`/`--exclude-from` are rejected because those files are local and R05 does not silently upload them.
- Password/password-file/password-env requests remain rejected in remote-native mode; staging remains the supported credential-bearing path.
- Backend passthrough after `--` is shell-quoted and forwarded unchanged.
- Remote Arc's normalized process exit code is returned directly to the local caller.
- `--show-command`/`--dry-run` show the actual SSH command; `--show-native` records the same SSH stage in the execution plan.
- Remote-native dry-run performs no capability probe and no SSH execution.
- Successful remote-native archive mutations invalidate the archive parent completion cache. Remote-to-remote extraction invalidates both the output directory cache and its parent.
- `-o/--output` completion now shares the local/SSH/rclone directory provider, including attached `--output=...` values.

## Capability negotiation

SSH capability discovery now records remote `arc --version` when available. Pre-R05 capability-cache records use a different cache key so stale entries cannot hide the new version metadata.

Compatibility is intentionally conservative for pre-1.0 Arc: local and remote major+minor must match when both versions are known. If an older remote exposes Arc but no parseable version metadata, execution remains allowed for backward compatibility; command-level failures still preserve the remote exit code.

## Locality and safety

Endpoint equality is based on the resolved SSH invocation (host, user, port, identity, jump host, and configured SSH arguments), not merely the alias string. Two aliases that resolve to the same SSH endpoint can therefore participate in one remote-native operation; aliases targeting different endpoints cannot.

All remote command arguments are rendered with `shlex.join`. Hostile spaces, quotes, shell metacharacters, and member names remain single argv values on the remote shell command line.

## Deliberate boundaries

- `auto` remains staging/streaming-first and does not silently switch to remote-native execution.
- R05 does not upload local inputs, local rule files, or credentials for remote-native execution.
- Extracting an entire SSH archive into a local directory still uses R04's local safety/staging path. `extract --stdout` is the direct remote-to-local exception.
- rclone continues to use its transport operations; it cannot use `--execution=remote`.

## Validation policy

ARC-R05 follows the tests-only policy requested for ARC overlays. `.devtool.toml` defines `test_profiles.r05` and `targets.arc.jobs.r05-tests`, both scoped to `tests/test_r05_remote_native.py`. The artifact validation surface invokes only `python3 scripts/run_r05_tests.py`; no Ruff, formatter, source audit, SBOM, dependency check, build/package/release validation, or separate contract script is requested.

The local audit loop also ran the pre-R04 R01-R03 regression suite and the R04 tests most directly affected by SSH execution/capability changes. Those checks are audit evidence only; they are not added to the overlay's Devtool validation workload.
