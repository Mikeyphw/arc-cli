import pytest

from arc_cli.errors import UnsafeArchive
from arc_cli.model import Member
from arc_cli.safety import validate_members


def test_rejects_parent_escape():
    with pytest.raises(UnsafeArchive):
        validate_members([Member("../../etc/passwd")])


def test_rejects_absolute_symlink():
    with pytest.raises(UnsafeArchive):
        validate_members([Member("x", kind="symlink", link_target="/etc")])


def test_rejects_descendant_through_symlink():
    with pytest.raises(UnsafeArchive):
        validate_members([
            Member("a", kind="symlink", link_target="target"),
            Member("a/file", size=1),
        ])
