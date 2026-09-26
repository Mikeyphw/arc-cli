#!/usr/bin/env python3
"""Write a stable release-evidence summary after Devtool's package gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = ROOT / ".devtool" / "evidence" / "release-contract.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


artifacts = []
for path in sorted(DIST.glob("*")):
    if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz")):
        artifacts.append({"path": path.relative_to(ROOT).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})

payload = {
    "schema_version": 1,
    "kind": "arc-release-contract",
    "ok": bool(artifacts),
    "artifacts": artifacts,
    "completion_sha256": sha256(ROOT / "completions" / "_arc"),
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
raise SystemExit(0 if payload["ok"] else 1)
