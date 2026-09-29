from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from arc_cli.machine import load_schema, schema_names

ROOT = Path(__file__).resolve().parents[1]


def test_split_manifest_is_part_of_public_machine_schema_registry() -> None:
    assert "split-manifest-v1" in schema_names()
    schema = load_schema("split-manifest-v1")
    assert schema["$id"] == "arc.split-manifest/v1"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["required"]


def test_active_machine_contract_accepts_split_manifest_registry() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_machine_contract_r09b.py"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "machine contract: pass" in result.stdout
