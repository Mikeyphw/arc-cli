from pathlib import Path
import gzip
import tarfile

from arc_cli.composition import CompositionSource, resolve_merge_output
from arc_cli.errors import UsageError
from arc_cli.formats import infer_from_name, parse_format, detect


def test_compound_extension_wins():
    assert infer_from_name("x.tar.zst").canonical == "tar.zstd"
    assert infer_from_name("x.tgz").canonical == "tar.gzip"


def test_parse_aliases():
    assert parse_format("tgz").canonical == "tar.gzip"
    assert parse_format("zst").canonical == "zstd"


def test_detect_zip_magic(tmp_path: Path):
    p = tmp_path / "wrong.bin"
    p.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    assert detect(p).canonical == "zip"


def test_detect_targz(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    p = tmp_path / "thing.bin"
    with tarfile.open(p, "w:gz") as tf:
        tf.add(src, arcname="a.txt")
    assert detect(p).canonical == "tar.gzip"



def _source(name: str, fmt: str) -> CompositionSource:
    return CompositionSource(Path(name), parse_format(fmt))


def test_comp_x01_merge_output_preserves_uniform_input_format():
    result = resolve_merge_output([_source("alpha.zip", "zip"), _source("beta.zip", "zip")])
    assert result.path == Path("alpha.merged.zip")
    assert result.format.canonical == "zip"
    assert result.format_source == "uniform-inputs"
    assert result.name_inferred is True


def test_comp_x01_output_suffix_selects_mixed_merge_format():
    result = resolve_merge_output(
        [_source("alpha.tar", "tar"), _source("beta.zip", "zip")],
        output="combined.tar.zst",
    )
    assert result.path == Path("combined.tar.zst")
    assert result.format.canonical == "tar.zstd"
    assert result.format_source == "output-suffix"
    assert result.name_inferred is False


def test_comp_x01_explicit_format_adds_canonical_extension():
    result = resolve_merge_output(
        [_source("alpha.tar", "tar"), _source("beta.zip", "zip")],
        output="combined",
        explicit_format="7z",
    )
    assert result.path == Path("combined.7z")
    assert result.format.canonical == "7z"
    assert result.format_source == "explicit-format"


def test_comp_x01_mixed_inputs_require_output_format_truth():
    import pytest

    with pytest.raises(UsageError, match="mixed inputs"):
        resolve_merge_output([_source("alpha.tar", "tar"), _source("beta.zip", "zip")])


def test_comp_x01_conflicting_output_suffix_and_format_fail_closed():
    import pytest

    with pytest.raises(UsageError, match="output suffix implies zip"):
        resolve_merge_output([_source("alpha.zip", "zip")], output="combined.zip", explicit_format="tar")


def test_comp_x01_repository_contract_script():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "scripts/check_comp_x01_contract.py"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "COMP-X01 contract: PASS" in proc.stdout
