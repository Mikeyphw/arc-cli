from __future__ import annotations

from pathlib import Path

import pytest

from arc_cli.cli import main
from arc_cli.command_docs import COMMAND_DOCS, resolve_topic
from arc_cli.manpages import SECTIONS, man_filename, read_manpage
from arc_cli.manual import generated_pages, markdown_reference


ROOT = Path(__file__).resolve().parents[1]


def test_all_declared_manual_topics_are_bundled() -> None:
    for topic in SECTIONS:
        path = ROOT / "src" / "arc_cli" / "man" / man_filename(topic)
        assert path.is_file(), (topic, path)
        text = read_manpage(topic)
        assert ".SH \"NAME\"" in text


def test_command_aliases_resolve_to_canonical_manual_topic() -> None:
    assert resolve_topic("arccv") == "convert"
    assert man_filename("arccv") == "arc-convert.1"
    assert resolve_topic("arci") == "info"
    assert man_filename("arc-info") == "arc-info.1"


def test_arc_help_renders_same_detailed_manual_content(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["help", "convert"]) == 0
    out = capsys.readouterr().out
    assert "arc-convert - convert an archive or compressed stream" in out
    assert "BATCH CONVERSION" in out
    assert "--replace-source" in out


def test_arc_man_list_exposes_reference_topics(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["man", "--list"]) == 0
    topics = set(capsys.readouterr().out.split())
    assert {"arc", "convert", "info", "formats", "remote", "config"} <= topics


def test_main_manpage_documents_core_command_family_and_reference_sections() -> None:
    text = read_manpage("arc")
    for word in ("create", "extract", "list", "info", "test", "convert", "EXECUTABLE ALIASES", "SAFETY", "REMOTE PATHS"):
        assert word in text


def test_command_metadata_summaries_appear_in_argparse_help() -> None:
    # The shared metadata drives the parser's help for newly introduced public
    # surfaces; this catches drift without requiring manpages to be terse copies
    # of argparse output.
    from arc_cli.cli import parser
    help_text = parser().format_help()
    for name in ("info", "convert", "profiles", "man", "help"):
        assert COMMAND_DOCS[name].summary in help_text


def test_generated_manual_sources_match_authoritative_model() -> None:
    expected = {page.filename: content for page, content in generated_pages()}
    man_root = ROOT / "src" / "arc_cli" / "man"
    actual = {path.name: path.read_text(encoding="utf-8") for path in man_root.iterdir() if path.is_file()}
    assert actual == expected
    assert (ROOT / "docs" / "COMMAND_REFERENCE.md").read_text(encoding="utf-8") == markdown_reference()


def test_manual_generator_check_is_clean() -> None:
    import subprocess
    import sys

    subprocess.run([sys.executable, "scripts/generate_command_docs.py", "--check"], cwd=ROOT, check=True)


def test_reference_page_section_can_be_selected_explicitly() -> None:
    assert man_filename("backends(7)") == "arc-backends.7"
    assert man_filename("arc-backends(1)") == "arc-backends.1"


def test_argparse_help_stays_compact_while_manual_carries_detailed_semantics(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["convert", "--help"])
    short_help = capsys.readouterr().out
    assert "--replace-source" in short_help
    assert "unpublished same-filesystem candidate" not in short_help

    assert main(["help", "convert"]) == 0
    detailed = capsys.readouterr().out
    assert "unpublished same-filesystem candidate" in detailed
    assert "transport-staged" in detailed
