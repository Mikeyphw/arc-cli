from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path

from .errors import UsageError
from .manual import DEFAULT_TOPIC_SECTIONS, available_topics as _available_topics, manual_topic


# Backward-compatible public name used by focused tests.  The mapping itself is
# owned by the structured manual model rather than duplicated here.
SECTIONS = DEFAULT_TOPIC_SECTIONS


def man_filename(topic: str) -> str:
    try:
        return manual_topic(topic).filename
    except KeyError as exc:
        raise UsageError(f"unknown manual topic: {topic}") from exc


def available_topics() -> list[str]:
    return _available_topics()


def read_manpage(topic: str) -> str:
    name = man_filename(topic)
    try:
        return resources.files("arc_cli").joinpath("man", name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise UsageError(f"bundled manual page is missing: {name}") from exc


def _plain_roff(text: str) -> str:
    """Small fallback renderer for Arc's deliberately conservative man roff."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith(".TH "):
            continue
        if line.startswith(".SH "):
            title = line[4:].strip().strip('"')
            out.extend(["", title])
            continue
        if line.startswith(".SS "):
            out.extend(["", line[4:].strip().strip('"')])
            continue
        if line in {".PP", ".P", ".br", ".RS", ".RE"}:
            if out and out[-1] != "":
                out.append("")
            continue
        if line.startswith((".B ", ".I ")):
            line = line[3:]
        elif line.startswith((".BR ", ".IR ", ".BI ")):
            line = line[4:]
        elif line.startswith(".TP"):
            continue
        if line.startswith((".nf", ".fi")):
            continue
        line = line.replace("\\fB", "").replace("\\fI", "").replace("\\fR", "")
        line = line.replace("\\-", "-").replace("\\&", "")
        if line.startswith('.\\"'):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def show_manpage(topic: str, *, plain: bool = False) -> int:
    text = read_manpage(topic)
    if plain or not sys.stdout.isatty():
        sys.stdout.write(_plain_roff(text))
        return 0

    # Prefer the system viewer when present. `man -l -` is not portable, so
    # materialize our generated bundled page into a short-lived file. Termux
    # systems without man fall through to the bundled renderer and pager.
    man = shutil.which("man")
    if man:
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".man", delete=False) as fh:
                fh.write(text)
                tmp_name = fh.name
            try:
                proc = subprocess.run([man, "-l", tmp_name], check=False)
                if proc.returncode == 0:
                    return 0
            finally:
                Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass

    rendered = _plain_roff(text)
    pager = os.environ.get("MANPAGER") or os.environ.get("PAGER")
    if pager:
        try:
            proc = subprocess.run(pager, input=rendered, text=True, shell=True, check=False)
            return proc.returncode
        except OSError:
            pass
    sys.stdout.write(rendered)
    return 0
