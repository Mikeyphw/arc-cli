from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

from arc_cli.command_docs import EXECUTABLE_ALIASES
from arc_cli.manual import generated_pages

ROOT = Path(__file__).resolve().parents[1]


def _assert_wheel_contract(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        entry_points_name = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(zf.read(entry_points_name).decode("utf-8"))
        scripts = set(parser["console_scripts"])
        assert {"arc", *EXECUTABLE_ALIASES} <= scripts

        data_prefix = "arc_cli-0.1.0.data/data/share/man"
        for page, _content in generated_pages():
            assert f"arc_cli/man/{page.filename}" in names
            assert f"{data_prefix}/man{page.section}/{page.filename}" in names


def _build_distributions(source: Path, output: Path) -> tuple[Path, Path]:
    wheel_dir = output / "wheel"
    sdist_dir = output / "sdist"
    wheel_dir.mkdir(parents=True)
    sdist_dir.mkdir(parents=True)
    wheel_code = "from setuptools import build_meta; import sys; print(build_meta.build_wheel(sys.argv[1]))"
    sdist_code = "from setuptools import build_meta; import sys; print(build_meta.build_sdist(sys.argv[1]))"
    subprocess.run(
        [sys.executable, "-c", wheel_code, str(wheel_dir)],
        cwd=source,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [sys.executable, "-c", sdist_code, str(sdist_dir)],
        cwd=source,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return next(wheel_dir.glob("*.whl")), next(sdist_dir.glob("*.tar.gz"))


def test_distribution_metadata_covers_aliases_and_generated_manpages() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert set(EXECUTABLE_ALIASES) <= set(scripts)
    assert all(scripts[name] == "arc_cli.cli:main" for name in EXECUTABLE_ALIASES)

    data_files = project["tool"]["setuptools"]["data-files"]
    installed = {Path(path).name for paths in data_files.values() for path in paths}
    expected = {page.filename for page, _content in generated_pages()}
    assert installed == expected


def test_sdist_manifest_keeps_generated_docs_workflow() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include docs *.md" in manifest
    assert "recursive-include scripts *.py" in manifest
    assert "recursive-include src/arc_cli/man *" in manifest


def test_built_wheel_and_sdist_rebuild_preserve_alias_and_manual_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git", ".devtool", ".pytest_cache", "__pycache__", "*.pyc", "*.egg-info", "build", "dist"
        ),
    )
    wheel, sdist = _build_distributions(source, tmp_path / "first")
    _assert_wheel_contract(wheel)

    with tarfile.open(sdist, "r:gz") as tf:
        names = set(tf.getnames())
        top = next(iter(sorted({Path(name).parts[0] for name in names if name})))
        assert f"{top}/scripts/generate_command_docs.py" in names
        assert f"{top}/scripts/sync_alias_registry.py" in names
        assert f"{top}/scripts/refresh_dev_install.py" in names
        assert f"{top}/docs/COMMAND_REFERENCE.md" in names
        assert f"{top}/docs/ARC-NEXT-ROADMAP.md" in names
        assert f"{top}/src/arc_cli/manual.py" in names
        tf.extractall(tmp_path / "sdist-tree", filter="data")

    rebuilt_root = tmp_path / "sdist-tree" / top
    rebuilt_wheel_dir = tmp_path / "rebuilt-wheel"
    rebuilt_wheel_dir.mkdir()
    code = "from setuptools import build_meta; import sys; print(build_meta.build_wheel(sys.argv[1]))"
    subprocess.run(
        [sys.executable, "-c", code, str(rebuilt_wheel_dir)],
        cwd=rebuilt_root,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    _assert_wheel_contract(next(rebuilt_wheel_dir.glob("*.whl")))
