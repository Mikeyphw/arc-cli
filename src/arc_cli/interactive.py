from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from rich.prompt import Prompt

from .errors import UsageError
from .progress import console


def _decode_nul(data: bytes) -> list[str]:
    return [os.fsdecode(part) for part in data.split(b"\0") if part]


def _encode_nul(items: list[str]) -> bytes:
    if not items:
        return b""
    return b"\0".join(os.fsencode(item) for item in items) + b"\0"


def rg_files(base: Path = Path.cwd(), hidden: bool = True) -> list[str]:
    """Return filesystem candidates without losing tabs/newlines in names."""
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--files", "--no-config", "--null"]
        if hidden:
            cmd.append("--hidden")
        proc = subprocess.run(cmd, cwd=base, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if proc.returncode in {0, 1}:
            return _decode_nul(proc.stdout)
    out: list[str] = []
    for root, _dirs, files in os.walk(base):
        rootp = Path(root)
        for name in files:
            try:
                out.append(os.fspath((rootp / name).relative_to(base)))
            except ValueError:
                pass
    return out


def filesystem_candidates(include_dirs: bool = False) -> list[str]:
    files = rg_files()
    if not include_dirs:
        return files
    dirs: set[str] = set()
    for item in files:
        p = Path(item)
        for parent in p.parents:
            if str(parent) not in {".", ""}:
                dirs.add(os.fspath(parent) + os.sep)
    # Include empty directories too; rg --files intentionally only reports files.
    cwd = Path.cwd()
    for root, child_dirs, _ in os.walk(cwd):
        rootp = Path(root)
        for name in child_dirs:
            p = rootp / name
            try:
                dirs.add(os.fspath(p.relative_to(cwd)) + os.sep)
            except ValueError:
                pass
    return sorted(dirs) + files


def fzf_choose(candidates: list[str], *, multi: bool, prompt: str, preview: str | None = None) -> list[str]:
    exe = shutil.which("fzf")
    if not exe:
        raise UsageError("fzf is not installed")
    cmd = [
        exe,
        "--read0",
        "--print0",
        "--prompt",
        prompt,
        "--height=80%",
        "--border",
        "--reverse",
    ]
    if multi:
        cmd.append("--multi")
    if preview:
        cmd += ["--preview", preview]
    proc = subprocess.run(cmd, input=_encode_nul(candidates), stdout=subprocess.PIPE)
    if proc.returncode == 130:
        raise KeyboardInterrupt
    if proc.returncode != 0:
        return []
    return _decode_nul(proc.stdout)


def rich_choose(candidates: list[str], *, prompt: str) -> list[str]:
    if not candidates:
        return []
    if len(candidates) > 50:
        raise UsageError("interactive choice requires fzf when more than 50 candidates are available")
    for i, item in enumerate(candidates, 1):
        # repr-like escaping prevents control characters in odd filenames from
        # corrupting the terminal while selection remains index based.
        display = item.encode("unicode_escape", errors="backslashreplace").decode("ascii", errors="replace")
        console.print(f"[dim]{i:>3}[/] {display}")
    answer = Prompt.ask(prompt, console=console)
    try:
        index = int(answer)
    except ValueError as exc:
        raise UsageError("enter a numeric selection") from exc
    if index < 1 or index > len(candidates):
        raise UsageError("selection out of range")
    return [candidates[index - 1]]


def choose_auto(candidates: list[str], *, multi: bool, prompt: str, preview: str | None = None) -> list[str]:
    if not os.isatty(0):
        raise UsageError(f"missing operand; specify it explicitly (interactive {prompt.strip()} selection needs a TTY)")
    if shutil.which("fzf"):
        return fzf_choose(candidates, multi=multi, prompt=prompt, preview=preview)
    if multi:
        raise UsageError("multiple interactive selection requires fzf; install fzf or provide paths explicitly")
    return rich_choose(candidates, prompt=prompt)


def yazi_choose(*, multi: bool = True, cwd: Path | None = None) -> list[str]:
    """Use Yazi's chooser-file protocol and always clean the temporary file."""
    exe = shutil.which("yazi")
    if not exe:
        raise UsageError("--yazi requested but yazi is not installed")
    fd, name = tempfile.mkstemp(prefix="arc-yazi-")
    os.close(fd)
    chooser = Path(name)
    try:
        cmd = [exe, os.fspath(cwd or Path.cwd()), f"--chooser-file={chooser}"]
        proc = subprocess.run(cmd)
        if proc.returncode in {130, 143}:
            raise KeyboardInterrupt
        if proc.returncode != 0:
            return []
        try:
            raw = chooser.read_bytes()
        except OSError:
            raw = b""
        # Yazi's chooser-file format is newline separated. Keep this decoding
        # isolated at the external integration boundary; all arc-owned chooser,
        # completion and rg/fzf protocols use NUL framing.
        lines = [os.fsdecode(x) for x in raw.splitlines() if x]
        return lines if multi else lines[:1]
    finally:
        chooser.unlink(missing_ok=True)
