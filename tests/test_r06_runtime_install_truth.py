from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import arc_cli.doctor as doctor
from arc_cli.cli import main
from arc_cli.command_docs import alias_specs, console_script_mapping


ROOT = Path(__file__).resolve().parents[1]


def test_alias_registry_is_typed_unique_and_includes_canonical_script() -> None:
    specs = alias_specs()
    assert len(specs) == len({spec.executable for spec in specs})
    assert all(spec.command for spec in specs)
    mapping = console_script_mapping()
    assert mapping["arc"] == "arc_cli.cli:main"
    assert len(mapping) == len(specs) + 1
    assert all(mapping[spec.executable] == "arc_cli.cli:main" for spec in specs)


def test_registry_consumers_are_generated_from_one_contract() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/sync_alias_registry.py", "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    devtool = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    expected = console_script_mapping()
    assert project["project"]["scripts"] == expected
    assert devtool["python"]["package"]["console_scripts"] == list(expected)


def test_sync_alias_registry_detects_drift_in_disposable_copy(tmp_path: Path) -> None:
    for name in ("pyproject.toml", ".devtool.toml"):
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('arci = "arc_cli.cli:main"\n', ""), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_alias_registry.py"), "--check", "--root", str(tmp_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 1
    assert "pyproject.toml" in proc.stderr


def test_doctor_detects_declared_but_missing_path_alias(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor, "installed_entry_points", lambda: console_script_mapping())
    monkeypatch.setattr(doctor, "_distribution", lambda: None)
    report = doctor.collect_doctor_report(
        source=ROOT,
        which=lambda name: "/usr/bin/arc" if name == "arc" else None,
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["path-aliases"]["status"] == "warn"
    assert "arci" in checks["path-aliases"]["detail"]
    assert checks["path-aliases"]["repairable"] is True


def test_doctor_distinguishes_invalid_config(monkeypatch, tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    config_path = config_home / "arc" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[profiles\ninvalid", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(doctor, "installed_entry_points", lambda: console_script_mapping())
    monkeypatch.setattr(doctor, "_distribution", lambda: None)
    report = doctor.collect_doctor_report(source=ROOT, which=lambda _name: "/bin/true")
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["config"]["status"] == "fail"
    assert "invalid" in checks["config"]["detail"]


def test_aliases_json_exposes_runtime_availability(capsys) -> None:
    assert main(["aliases", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    by_name = {row["executable"]: row for row in payload["aliases"]}
    assert by_name["arci"]["command"] == "info"
    assert {"metadata_declared", "available", "path"} <= set(by_name["arci"])


def test_doctor_json_schema_and_source_truth(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "installed_entry_points", lambda: console_script_mapping())
    assert main(["doctor", "--json", "--source", str(ROOT)]) in {0, 1}
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert Path(payload["source_root"]) == ROOT
    assert {"pass", "warn", "fail"} == set(payload["summary"])


def test_refresh_helper_verifies_the_synced_project_script_projection() -> None:
    path = ROOT / "scripts" / "refresh_dev_install.py"
    spec = importlib.util.spec_from_file_location("arc_refresh_dev_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._declared_console_scripts(ROOT) == tuple(sorted(console_script_mapping()))


def test_refresh_helper_has_non_mutating_dry_run() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/refresh_dev_install.py", "--source", str(ROOT), "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert payload["source"] == str(ROOT)
    assert "pip" in payload["install"]
    assert "-e" in payload["install"]


def test_r06_is_native_devtool_workflow_and_test_contract() -> None:
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    tests = {item["id"]: item for item in cfg["test"]}
    assert "arc-r06-runtime-install-truth" in tests
    jobs = cfg["targets"]["arc"]["jobs"]
    assert jobs["alias-contract"]["command"][-1] == "--check"
    assert "refresh-install" in jobs
    workflow = cfg["targets"]["arc"]["workflows"]["r06"]
    refs = {step["ref"] for step in workflow}
    assert "job:alias-contract" in refs
    assert "test:arc-r06-runtime-install-truth" in refs
    wrappers = cfg["wrapper"]["commands"]
    assert wrappers["r06"]["workflow"] == "r06"
    assert wrappers["refresh-install"]["workflow"] == "refresh_install"


def test_roadmap_preserves_merge_window_and_all_eighteen_items() -> None:
    text = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    assert "inspect the next three" in text.lower()
    for number in range(1, 19):
        assert f"{number}." in text
    assert "Separate R06 gate" in text
