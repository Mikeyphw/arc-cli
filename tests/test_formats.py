from pathlib import Path
import gzip
import tarfile

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
