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
from rich.table import Column

console = Console(stderr=True)
stdout_console = Console(stderr=False)

_PERCENT = re.compile(r"(?:^|\s)(\d{1,3})%")


def _current_column(max_width: int) -> TextColumn:
    # Filenames are data, not Rich markup. Keep them on one line so a long
    # member cannot blow up a phone-sized terminal.
    return TextColumn(
        "{task.fields[current]}",
        style="dim",
        markup=False,
        table_column=Column(max_width=max_width, overflow="ellipsis", no_wrap=True),
    )


def _progress_columns(kind: str, width: int):
    """Return a density-aware column set for the current terminal width.

    The previous layout tried to show bytes, speed, file count, current member,
    elapsed time and ETA simultaneously. On a narrow Termux terminal that made
    Rich collapse/truncate several fields into unreadable fragments. Prefer a
    stable core and progressively add detail as width becomes available.
    """
    core = [
        SpinnerColumn(finished_text="[green]✓[/]"),
        TextColumn("[progress.description]{task.description}"),
    ]

    if kind == "indeterminate":
        columns = [*core, _current_column(22 if width < 46 else (28 if width < 100 else 48))]
        if width >= 46:
            columns.append(TimeElapsedColumn())
        return columns

    columns = [
        *core,
        BarColumn(bar_width=None),
        TaskProgressColumn(),
    ]

    if kind == "bytes":
        if width >= 78:
            columns.append(DownloadColumn())
        columns.append(TextColumn("{task.fields[files_done]}/{task.fields[files_total]}"))
        if width >= 108:
            columns.append(TransferSpeedColumn())
        if width >= 126:
            columns.append(_current_column(32))
        if width >= 46:
            columns.append(TimeElapsedColumn())
        if width >= 146:
            columns.append(TimeRemainingColumn())
        return columns

    # File-count progress follows the same density rules but doesn't waste
    # horizontal space on a byte counter that doesn't exist.
    columns.append(TextColumn("{task.completed:.0f}/{task.total:.0f}"))
    if width >= 104:
        columns.append(_current_column(36))
    if width >= 46:
        columns.append(TimeElapsedColumn())
    if width >= 132:
        columns.append(TimeRemainingColumn())
    return columns


@dataclass
class ProgressReporter:
    description: str
    total_bytes: int
    total_files: int
    enabled: bool = True
    terminal_width: int | None = None

    def __post_init__(self) -> None:
        self._seen: set[str] = set()
        self._progress: Progress | None = None
        self._task = None
        self._last_percent = 0
        self.kind = "bytes" if self.total_bytes > 0 else ("files" if self.total_files > 0 else "indeterminate")

    def __enter__(self):
        if not self.enabled:
            return self
        width = self.terminal_width or console.size.width
        cols = _progress_columns(self.kind, width)
        self._progress = Progress(*cols, console=console, transient=False, expand=True)
        self._progress.start()
        if self.kind == "bytes":
            self._task = self._progress.add_task(
                self.description,
                total=self.total_bytes,
                files_done=0,
                files_total=self.total_files,
                current="",
            )
        elif self.kind == "files":
            self._task = self._progress.add_task(self.description, total=self.total_files, current="")
        else:
            # Never fabricate a percentage when the selected backend does not
            # expose totals or usable per-member telemetry.
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
            self._progress.update(self._task, current=text[:160])
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
