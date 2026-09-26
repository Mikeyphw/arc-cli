from __future__ import annotations


class ArcError(Exception):
    exit_code = 1


class UsageError(ArcError):
    exit_code = 2


class UnsupportedFormat(ArcError):
    exit_code = 3


class BackendUnavailable(ArcError):
    exit_code = 4


class CorruptArchive(ArcError):
    exit_code = 5


class PasswordError(ArcError):
    exit_code = 6


class UnsafeArchive(ArcError):
    exit_code = 7


class ConflictError(ArcError):
    exit_code = 8


class PartialFailure(ArcError):
    exit_code = 9
