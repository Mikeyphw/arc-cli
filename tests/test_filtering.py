from pathlib import Path

from arc_cli.filtering import build_manifest, selected
from arc_cli.model import FilterRule


def test_ordered_rules_last_match_wins():
    rules = [FilterRule("exclude", "build/**"), FilterRule("include", "build/releases/**")]
    assert not selected("build/tmp/a.bin", False, rules)
    assert selected("build/releases/a.bin", False, rules)


def test_manifest_filters(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("x")
    (tmp_path / "p" / "a.pyc").write_text("x")
    entries = build_manifest(["p"], [FilterRule("exclude", "*.pyc")])
    names = {e.member_name for e in entries}
    assert "p/a.py" in names
    assert "p/a.pyc" not in names
