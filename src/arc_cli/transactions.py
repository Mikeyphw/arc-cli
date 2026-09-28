from __future__ import annotations

import contextvars
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


TRANSACTION_SCHEMA_VERSION = 1
BATCH_SCHEMA_VERSION = 1


def _validate_state_id(value: str, *, label: str) -> str:
    if (
        not value
        or Path(value).name != value
        or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in value)
    ):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_root() -> Path:
    explicit = os.environ.get("ARC_STATE_HOME")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "arc"
    return Path.home() / ".local" / "state" / "arc"


def transactions_root() -> Path:
    return state_root() / "transactions"


def batches_root() -> Path:
    return state_root() / "batches"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        if os.name == "posix":
            dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def file_fingerprint(path: Path) -> dict[str, Any]:
    path = path.expanduser().absolute()
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "kind": "local-file",
        "path": os.fspath(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def same_fingerprint(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    keys = ("kind", "path", "size", "sha256")
    return all(left.get(key) == right.get(key) for key in keys)


class TransactionJournal:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @classmethod
    def create(
        cls,
        operation: str,
        *,
        metadata: dict[str, Any] | None = None,
        transaction_id: str | None = None,
    ) -> "TransactionJournal":
        txid = transaction_id or f"{int(time.time())}-{uuid.uuid4().hex[:12]}"
        path = transactions_root() / f"{txid}.json"
        data: dict[str, Any] = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "id": txid,
            "operation": operation,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
            "events": [],
            "cleanup_paths": [],
            "result": None,
            "error": None,
        }
        journal = cls(data, path)
        journal._save()
        return journal

    @classmethod
    def load(cls, transaction_id: str) -> "TransactionJournal":
        transaction_id = _validate_state_id(transaction_id, label="transaction id")
        path = transactions_root() / f"{transaction_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(transaction_id) from exc
        if not isinstance(data, dict) or data.get("id") != transaction_id:
            raise ValueError(f"invalid transaction journal: {path}")
        return cls(data, path)

    def _save(self) -> None:
        self.data["updated_at"] = _now()
        _atomic_json(self.path, self.data)

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def status(self) -> str:
        return str(self.data.get("status", "unknown"))

    def event(self, phase: str, *, status: str = "done", **detail: Any) -> None:
        event = {"at": _now(), "phase": phase, "status": status}
        for key, value in detail.items():
            if value is not None:
                event[key] = value
        self.data.setdefault("events", []).append(event)
        self._save()

    def register_cleanup(self, path: Path, *, kind: str = "file", reason: str = "temporary") -> None:
        resolved = os.fspath(path.expanduser().absolute())
        entries = self.data.setdefault("cleanup_paths", [])
        item = {"path": resolved, "kind": kind, "reason": reason, "cleaned": False}
        if not any(existing.get("path") == resolved for existing in entries):
            entries.append(item)
            self._save()

    def mark_cleanup_done(self, path: Path) -> None:
        resolved = os.fspath(path.expanduser().absolute())
        changed = False
        for item in self.data.setdefault("cleanup_paths", []):
            if item.get("path") == resolved and not item.get("cleaned"):
                item["cleaned"] = True
                changed = True
        if changed:
            self._save()

    def complete(self, result: dict[str, Any] | None = None) -> None:
        self.data["status"] = "completed"
        self.data["result"] = result
        self.data["error"] = None
        self.event("complete", status="completed")
        self._save()

    def fail(self, error: BaseException | str, *, interrupted: bool = False) -> None:
        self.data["status"] = "interrupted" if interrupted else "failed"
        self.data["error"] = {
            "type": type(error).__name__ if isinstance(error, BaseException) else "error",
            "message": str(error),
        }
        self.event("interrupted" if interrupted else "failed", status=self.data["status"], message=str(error))
        self._save()

    def cleanup(self) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for item in self.data.setdefault("cleanup_paths", []):
            if item.get("cleaned"):
                continue
            path = Path(str(item.get("path", "")))
            if not str(path):
                continue
            outcome = {"path": os.fspath(path), "kind": item.get("kind", "file"), "removed": False}
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    outcome["removed"] = True
                elif path.exists() or path.is_symlink():
                    path.unlink()
                    outcome["removed"] = True
                item["cleaned"] = True
            except OSError as exc:
                outcome["error"] = str(exc)
            outcomes.append(outcome)
        if self.status != "completed" and all(item.get("cleaned") for item in self.data.get("cleanup_paths", [])):
            self.data["status"] = "cleaned"
        self.event("cleanup", status=self.data["status"], outcomes=outcomes)
        self._save()
        return outcomes


_current_transaction: contextvars.ContextVar[TransactionJournal | None] = contextvars.ContextVar(
    "arc_transaction", default=None
)


def current_transaction() -> TransactionJournal | None:
    return _current_transaction.get()


@contextmanager
def transaction_scope(
    operation: str,
    *,
    metadata: dict[str, Any] | None = None,
    transaction_id: str | None = None,
) -> Iterator[TransactionJournal]:
    journal = TransactionJournal.create(operation, metadata=metadata, transaction_id=transaction_id)
    token = _current_transaction.set(journal)
    try:
        journal.event("begin", status="active")
        yield journal
        if journal.status == "active":
            journal.complete()
    except KeyboardInterrupt as exc:
        journal.fail(exc, interrupted=True)
        raise
    except BaseException as exc:
        journal.fail(exc)
        raise
    finally:
        _current_transaction.reset(token)


def tx_event(phase: str, *, status: str = "done", **detail: Any) -> None:
    journal = current_transaction()
    if journal is not None:
        journal.event(phase, status=status, **detail)


def tx_cleanup(path: Path, *, kind: str = "file", reason: str = "temporary") -> None:
    journal = current_transaction()
    if journal is not None:
        journal.register_cleanup(path, kind=kind, reason=reason)


def tx_cleanup_done(path: Path) -> None:
    journal = current_transaction()
    if journal is not None:
        journal.mark_cleanup_done(path)


def list_transactions(*, include_completed: bool = False) -> list[dict[str, Any]]:
    root = transactions_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if not include_completed and data.get("status") in {"completed", "cleaned"}:
            continue
        rows.append(data)
    return sorted(rows, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def batch_policy_key(jobs: list[dict[str, Any]], policy: dict[str, Any]) -> str:
    canonical = {
        "jobs": [
            {
                "source": str(job["source"]),
                "destination": str(job["destination"]),
                "target_format": getattr(job["target_format"], "canonical", str(job["target_format"])),
            }
            for job in jobs
        ],
        "policy": policy,
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class BatchManifest:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @classmethod
    def open(cls, batch_id: str, *, policy: dict[str, Any], reset: bool = False) -> "BatchManifest":
        batch_id = _validate_state_id(batch_id, label="batch id")
        path = batches_root() / f"{batch_id}.json"
        if path.is_file() and not reset:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid batch manifest: {path}") from exc
            if not isinstance(data, dict) or data.get("schema_version") != BATCH_SCHEMA_VERSION:
                raise ValueError(f"unsupported batch manifest schema: {path}")
            if data.get("batch_id") != batch_id:
                raise ValueError(f"batch manifest identity mismatch: {path}")
            if data.get("policy") != policy:
                raise ValueError(f"batch {batch_id} was created with a different conversion policy")
            return cls(data, path)
        data = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "batch_id": batch_id,
            "created_at": _now(),
            "updated_at": _now(),
            "policy": policy,
            "items": {},
        }
        manifest = cls(data, path)
        manifest.save()
        return manifest

    def save(self) -> None:
        self.data["updated_at"] = _now()
        _atomic_json(self.path, self.data)

    def item(self, source: str) -> dict[str, Any] | None:
        value = self.data.setdefault("items", {}).get(source)
        return value if isinstance(value, dict) else None

    def update_item(self, source: str, value: dict[str, Any]) -> None:
        self.data.setdefault("items", {})[source] = value
        self.save()

    def to_summary(self) -> dict[str, Any]:
        items = list(self.data.get("items", {}).values())
        return {
            "batch_id": self.data.get("batch_id"),
            "path": os.fspath(self.path),
            "items": len(items),
            "completed": sum(1 for item in items if isinstance(item, dict) and item.get("status") == "completed"),
            "failed": sum(1 for item in items if isinstance(item, dict) and item.get("status") == "failed"),
        }
