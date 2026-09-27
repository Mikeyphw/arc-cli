from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Operation = Literal["identify", "list", "extract", "create", "info", "test", "convert", "add", "update", "remove"]


@dataclass(frozen=True, slots=True)
class ArchiveFormat:
    container: str | None
    compression: str | None = None

    @property
    def canonical(self) -> str:
        if self.container == "tar" and self.compression:
            return f"tar.{self.compression}"
        return self.container or self.compression or "unknown"

    @property
    def is_stream(self) -> bool:
        return self.container is None and self.compression is not None


@dataclass(slots=True)
class Member:
    name: str
    size: int = 0
    kind: str = "file"  # file, dir, symlink, hardlink, special, unknown
    link_target: str | None = None
    mtime: str | None = None


@dataclass(slots=True)
class BackendInfo:
    name: str
    binary: str
    path: str
    capabilities: set[str] = field(default_factory=set)


@dataclass(slots=True)
class FilterRule:
    action: Literal["include", "exclude"]
    pattern: str


@dataclass(slots=True)
class ManifestEntry:
    source: Path
    member_name: str
    size: int
    is_dir: bool = False
