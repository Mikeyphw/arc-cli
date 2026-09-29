from pathlib import Path

checks = {
    "src/arc_cli/transactions.py": [
        "def _auto_cleanup_registered",
        "journal.cleanup(preserve_status=True)",
        "except KeyboardInterrupt as exc:",
        "def cleanup(self, *, preserve_status: bool = False)",
    ],
    "src/arc_cli/remote.py": [
        "def stage_remote_for_read",
        "except BaseException:",
        "target.unlink(missing_ok=True)",
    ],
    "src/arc_cli/cli.py": [
        "def _materialize_stdin",
        "except BaseException:",
        "temp.unlink(missing_ok=True)",
    ],
    "src/arc_cli/backends.py": [
        "def _manifest_file",
        "except BaseException:",
        "p.unlink(missing_ok=True)",
    ],
    "tests/test_comp_temp_cleanup.py": [
        "test_transaction_scope_auto_cleans_registered_temp_on_success",
        "test_transaction_scope_auto_cleans_registered_temp_on_error_preserving_failure",
        "test_transaction_scope_auto_cleans_registered_temp_on_interrupt_preserving_interrupt",
        "test_remote_read_stage_cleans_temp_on_keyboard_interrupt",
        "test_merge_failure_cleans_unpublished_candidate",
        "test_join_failure_cleans_unpublished_candidate",
    ],
    "docs/ARC-NEXT-ROADMAP.md": ["COMP-R01", "temporary lifecycle", "success, handled errors, and interruption"],
    "docs/WRAPPER.md": ["./devtoolw comp-r01"],
    ".devtool.toml": ["comp_r01", "arc-comp-r01-temp-lifecycle", "comp-r01-contract"],
}
missing = []
for filename, needles in checks.items():
    text = Path(filename).read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            missing.append(f"{filename}: {needle}")
if missing:
    raise SystemExit("COMP-R01 contract gaps:\n- " + "\n- ".join(missing))
print("COMP-R01 contract: PASS")
