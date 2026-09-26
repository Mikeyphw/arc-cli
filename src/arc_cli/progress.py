from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console(stderr=True)
stdout_console = Console(stderr=False)

_PERCENT = re.compile(r"(?:^|\s)(\d{1,3})%")


@dataclass
class ProgressReporter:
    description: str
    total_bytes: int
    total_files: int
    enabled: bool = True

    def __post_init__(self) -> None:
        self._seen: set[str] = set()
        self._progress: Progress | None = None
        self._task = None
        self._last_percent = 0
        self.kind = "bytes" if self.total_bytes > 0 else ("files" if self.total_files > 0 else "indeterminate")

    def __enter__(self):
        if not self.enabled:
            return self
        if self.kind == "bytes":
            cols = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TextColumn("{task.fields[files_done]}/{task.fields[files_total]} files"),
                TextColumn("[dim]{task.fields[current]}[/]"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
            ]
            self._progress = Progress(*cols, console=console, transient=False)
            self._progress.start()
            self._task = self._progress.add_task(
                self.description,
                total=self.total_bytes,
                files_done=0,
                files_total=self.total_files,
                current="",
            )
        elif self.kind == "files":
            cols = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("{task.completed:.0f}/{task.total:.0f} files"),
                TextColumn("[dim]{task.fields[current]}[/]"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
            ]
            self._progress = Progress(*cols, console=console, transient=False)
            self._progress.start()
            self._task = self._progress.add_task(self.description, total=self.total_files, current="")
        else:
            # Never fabricate a percentage when the selected backend does not
            # expose totals or usable per-member telemetry.
            cols = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TextColumn("[dim]{task.fields[current]}[/]"),
                TimeElapsedColumn(),
            ]
            self._progress = Progress(*cols, console=console, transient=False)
            self._progress.start()
            self._task = self._progress.add_task(self.description, total=None, current="")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._progress:
            self._progress.stop()

    def member_done(self, name: str, size: int = 0) -> None:
        if not self._progress or name in self._seen:
            return
        self._seen.add(name)
        if self.kind == "bytes":
            self._progress.update(self._task, advance=max(size, 0), files_done=len(self._seen), current=name)
        elif self.kind == "files":
            self._progress.update(self._task, advance=1, current=name)
        else:
            self._progress.update(self._task, current=name)

    def observe(self, line: str) -> None:
        if not self._progress:
            return
        text = line.strip()
        if text:
            self._progress.update(self._task, current=text[:120])
        self.parse_percent(line)

    def advance_bytes(self, amount: int, *, current: str = "") -> None:
        if not self._progress or amount <= 0:
            return
        if self.kind == "bytes":
            self._progress.update(self._task, advance=amount, current=current)
        elif self.kind == "indeterminate":
            self._progress.update(self._task, current=current or f"{amount} bytes")

    def parse_percent(self, line: str) -> None:
        if not self._progress or self.kind == "indeterminate":
            return
        m = _PERCENT.search(line)
        if not m:
            return
        pct = max(0, min(100, int(m.group(1))))
        if pct <= self._last_percent:
            return
        self._last_percent = pct
        if self.kind == "bytes":
            self._progress.update(self._task, completed=(self.total_bytes * pct / 100))
        else:
            self._progress.update(self._task, completed=(self.total_files * pct / 100))

    def complete(self) -> None:
        if not self._progress:
            return
        if self.kind == "bytes":
            self._progress.update(self._task, completed=self.total_bytes, files_done=self.total_files)
        elif self.kind == "files":
            self._progress.update(self._task, completed=self.total_files)


def progress_enabled(mode: str, json_mode: bool = False, quiet: bool = False) -> bool:
    if json_mode or quiet or mode == "never":
        return False
    if mode == "always":
        return True
    return sys.stderr.isatty()
