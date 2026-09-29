from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import UsageError
from .formats import extension_for, infer_from_name, parse_format, strip_archive_suffix
from .model import ArchiveFormat


@dataclass(frozen=True, slots=True)
class CompositionSource:
    """One already-resolved composition input.

    Command layers remain responsible for local/remote staging and content
    detection.  The composition authority consumes only canonical format truth
    so merge/split/join do not invent a second format resolver.
    """

    path: Path
    format: ArchiveFormat


@dataclass(frozen=True, slots=True)
class CompositionOutput:
    """Deterministic output naming/format decision for composition commands."""

    path: Path
    format: ArchiveFormat
    format_source: str
    name_inferred: bool


def _uniform_source_format(sources: Sequence[CompositionSource]) -> ArchiveFormat | None:
    if not sources:
        return None
    first = sources[0].format
    if all(source.format.canonical == first.canonical for source in sources[1:]):
        return first
    return None


def _derived_merge_name(first: Path, fmt: ArchiveFormat) -> Path:
    stem = Path(strip_archive_suffix(first.name)).name or "archive"
    return first.with_name(f"{stem}.merged{extension_for(fmt)}")


def resolve_merge_output(
    sources: Sequence[CompositionSource],
    *,
    output: str | Path | None = None,
    explicit_format: str | ArchiveFormat | None = None,
) -> CompositionOutput:
    """Resolve merge output without silently choosing a mixed-input format.

    Precedence is intentionally explicit and reusable by the future merge CLI:

    * a recognized output suffix selects the format when ``--format`` is absent;
    * an explicit format is authoritative, but conflicts with a recognized
      output suffix fail closed;
    * otherwise uniformly formatted inputs preserve their format;
    * mixed formats require an explicit output format/suffix;
    * an output path without a recognized suffix receives the canonical suffix;
    * when output is omitted, ``<first-stem>.merged<suffix>`` is derived.
    """

    if not sources:
        raise UsageError("merge requires at least one input")

    requested: ArchiveFormat | None
    if isinstance(explicit_format, ArchiveFormat):
        requested = explicit_format
    elif explicit_format is not None:
        requested = parse_format(explicit_format)
    else:
        requested = None

    output_path = Path(output) if output is not None else None
    suffix_format = infer_from_name(output_path) if output_path is not None else None

    if requested is not None and suffix_format is not None and requested.canonical != suffix_format.canonical:
        raise UsageError(
            f"output suffix implies {suffix_format.canonical}, but --format selects {requested.canonical}"
        )

    uniform = _uniform_source_format(sources)
    if requested is not None:
        fmt = requested
        source = "explicit-format"
    elif suffix_format is not None:
        fmt = suffix_format
        source = "output-suffix"
    elif uniform is not None:
        fmt = uniform
        source = "uniform-inputs"
    else:
        raise UsageError("cannot infer merge output format from mixed inputs; use --format or an output archive suffix")

    if output_path is None:
        resolved_path = _derived_merge_name(sources[0].path, fmt)
        inferred = True
    elif infer_from_name(output_path) is None:
        resolved_path = Path(str(output_path) + extension_for(fmt))
        inferred = True
    else:
        resolved_path = output_path
        inferred = False

    return CompositionOutput(
        path=resolved_path,
        format=fmt,
        format_source=source,
        name_inferred=inferred,
    )
