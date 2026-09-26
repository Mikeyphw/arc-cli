# Contributing

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[test]'
```

## Validation

Run the test suite before committing:

```bash
python -m pytest
```

Useful smoke checks:

```bash
arc --help
arc formats
arc backends
arc completion zsh >/tmp/_arc
```

## Project rules

- Keep normalized wrapper semantics backend-independent where practical.
- Arguments after the first `--` belong to the selected native backend.
- Never fake archive progress when the backend does not expose reliable telemetry.
- Keep human UI on stderr and machine/data output on stdout.
- Extraction safety must be fail-closed by default.
- Add regression tests for behavior fixes, especially path safety and backend inconsistencies.
