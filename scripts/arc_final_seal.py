#!/usr/bin/env python3
"""ARC final-seal primitives shared by gate, validator, and finalizer."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "release" / "ARC-FINAL-SEAL.json"
SEAL_ID = "ARC-R01-R12-FINAL"
CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX = "a7028c1"
QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX = "95981f7"
ROADMAP = {"ARC-R01": "semantic correctness", "ARC-R02": "interaction, completion, progress, and safety", "ARC-R03": "capability-aware backend qualification", "ARC-R04": "execution plans, SSH/rclone transport, remote completion/cache, native command learning", "ARC-R05": "SSH-native execution convergence", "ARC-R06": "runtime, alias, installation, and packaging truth", "ARC-R07": "explainable plans, recovery journals, and resumable batches", "ARC-R08": "stable machine schema, typed backend capabilities, and verification policy", "ARC-R09A": "logical provenance, equivalence, and archive diff", "ARC-R09B": "remote capability, cache provenance, and publication guarantees", "ARC-R10": "destructive-operation policy, backend command truth, machine batch, and recoverability", "ARC-R11": "configuration provenance, strict diagnostics, and doctor convergence", "ARC-R12": "advisory evidence, benchmark corpus truth, and offline support diagnostics"}

AUTHORITATIVE_TOP_LEVEL = {
    ".devtool.toml",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "arc.py",
    "devtoolw",
    "devtoolw.cmd",
    "pyproject.toml",
    "uv.lock",
    "MANIFEST.in",
    "release/ARC-FINAL-SEAL.json",
}
AUTHORITATIVE_TREES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".github/workflows", (".yml", ".yaml")),
    ("completions", ("_arc",)),
    ("docs", (".md",)),
    ("scripts", (".py",)),
    ("src/arc_cli", (".py", ".json", ".1", ".5", ".7")),
    ("tests", (".py",)),
)
IGNORED_NAMES = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _is_tree_match(rel: Path, prefix: str, suffixes: tuple[str, ...]) -> bool:
    posix = rel.as_posix()
    if posix == prefix or not posix.startswith(prefix.rstrip("/") + "/"):
        return False
    if any(part in IGNORED_NAMES for part in rel.parts):
        return False
    if rel.suffix in IGNORED_SUFFIXES:
        return False
    if suffixes == ("_arc",):
        return rel.name == "_arc"
    return rel.suffix in suffixes


def authoritative_paths(root: Path = ROOT) -> list[str]:
    paths: set[str] = set()
    for raw in AUTHORITATIVE_TOP_LEVEL:
        path = root / raw
        if path.is_file():
            paths.add(raw)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        posix = rel.as_posix()
        if any(_is_tree_match(rel, prefix, suffixes) for prefix, suffixes in AUTHORITATIVE_TREES):
            paths.add(posix)
    return sorted(paths)


def _entry_for_rel(rel: str, root: Path = ROOT) -> dict[str, object]:
    path = root / rel
    data = path.read_bytes()
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def compute_entries(paths: list[str] | None = None, root: Path = ROOT) -> list[dict[str, object]]:
    selected = authoritative_paths(root) if paths is None else sorted(paths)
    return [_entry_for_rel(rel, root) for rel in selected if (root / rel).is_file()]


def compute_root(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: str(item["path"])):
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def git_head(root: Path = ROOT) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def qualified_gate_ancestry(root: Path = ROOT) -> tuple[bool | None, dict[str, Any]]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None, {
            "status": "UNAVAILABLE",
            "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
            "reason": "git metadata unavailable",
        }
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX}^{{commit}}"],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if exists.returncode != 0:
        return False, {
            "status": "FAIL",
            "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
            "reason": "qualified gate commit object missing",
        }
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX, "HEAD"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    ok = proc.returncode == 0
    return ok, {
        "status": "PASS" if ok else "FAIL",
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "head": git_head(root),
        "stderr": proc.stderr.strip(),
    }


def build_manifest(root: Path = ROOT, *, seal_source: str = "validated-transaction-worktree") -> dict[str, Any]:
    entries = compute_entries(root=root)
    return {
        "schema_version": 5,
        "status": "SEALED_CONTENT",
        "seal_id": SEAL_ID,
        "seal_scope": "authoritative-repository-content",
        "seal_source": seal_source,
        "campaign_base_commit_expected_prefix": CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX,
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "source_head_at_seal": git_head(root),
        "roadmap": ROADMAP,
        "sealed_file_count": len(entries),
        "root_sha256": compute_root(entries),
        "sealed_files": entries,
    }


def write_manifest(path: Path, root: Path = ROOT, *, seal_source: str = "validated-transaction-worktree") -> dict[str, Any]:
    manifest = build_manifest(root, seal_source=seal_source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(path: Path = DEFAULT_MANIFEST, root: Path = ROOT) -> tuple[bool, dict[str, Any]]:
    if not path.is_file():
        return False, {"schema_version": 5, "status": "FAIL", "error": f"seal manifest missing: {path}"}
    try:
        seal = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, {"schema_version": 5, "status": "FAIL", "error": f"invalid seal manifest: {exc}"}

    expected = seal.get("sealed_files")
    expected_root = seal.get("root_sha256")
    if seal.get("schema_version") != 5 or not isinstance(expected, list) or not isinstance(expected_root, str):
        return False, {
            "schema_version": 5,
            "status": "FAIL",
            "seal_scope": seal.get("seal_scope"),
            "manifest_status": seal.get("status"),
            "error": "seal manifest is not an ARC schema-5 final seal",
        }

    expected_by_path = {str(item["path"]): item for item in expected}
    expected_paths = sorted(expected_by_path)
    discovered_paths = authoritative_paths(root)
    expected_set = set(expected_paths)
    discovered_set = set(discovered_paths)
    missing = sorted(path for path in expected_paths if not (root / path).is_file())
    unsealed_authoritative = sorted(discovered_set - expected_set)
    sealed_non_authoritative = sorted(expected_set - discovered_set)
    actual = compute_entries([p for p in expected_paths if (root / p).is_file()], root)
    actual_by_path = {str(item["path"]): item for item in actual}
    changed = sorted(
        path
        for path in expected_set & set(actual_by_path)
        if expected_by_path[path].get("sha256") != actual_by_path[path]["sha256"]
        or expected_by_path[path].get("size") != actual_by_path[path]["size"]
    )
    actual_root = compute_root(actual)
    ancestry_ok, ancestry = qualified_gate_ancestry(root)
    content_ok = not (missing or changed or unsealed_authoritative or sealed_non_authoritative) and actual_root == expected_root
    ok = content_ok and ancestry_ok is not False
    return ok, {
        "schema_version": 5,
        "status": "PASS" if ok else "FAIL",
        "seal_scope": seal.get("seal_scope"),
        "seal_source": seal.get("seal_source"),
        "campaign_base_commit_expected_prefix": seal.get("campaign_base_commit_expected_prefix"),
        "qualified_gate_commit_expected_prefix": seal.get("qualified_gate_commit_expected_prefix"),
        "expected_root_sha256": expected_root,
        "actual_root_sha256": actual_root,
        "sealed_file_count": len(expected),
        "actual_sealed_file_count": len(actual),
        "authoritative_file_count": len(discovered_paths),
        "missing": missing,
        "changed": changed,
        "unsealed_authoritative": unsealed_authoritative,
        "sealed_non_authoritative": sealed_non_authoritative,
        "qualified_gate_ancestry": ancestry,
    }
