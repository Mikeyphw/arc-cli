from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .backends import backend_inventory, resolve_backend
from .cli import main as arc_main
from .config import load_config
from .errors import ArcError
from .formats import detect, parse_format

PASS = "PASS"
FAIL = "FAIL"
SKIP_BACKEND = "SKIPPED_BACKEND_UNAVAILABLE"
SKIP_CAPABILITY = "SKIPPED_CAPABILITY_UNSUPPORTED"


@dataclass(slots=True)
class QualificationCase:
    name: str
    status: str
    duration_seconds: float = 0.0
    detail: str = ""
    backend: str | None = None
    format: str | None = None
    operation: str | None = None


def _call_arc(args: list[str], cwd: Path) -> int:
    """Invoke arc without leaking expected negative-test diagnostics.

    Qualification deliberately exercises unsafe archives, corruption and bad
    passwords.  Those are PASS cases when arc rejects them correctly, so their
    normal ``error: ...`` stderr must not be misclassified by Devtool as an
    overlay diagnostic error.  Exact failures are reported through the
    qualification row instead.
    """
    old = Path.cwd()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        os.chdir(cwd)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return arc_main(args)
    finally:
        os.chdir(old)


def _record(name: str, fn, *, backend: str | None = None, fmt: str | None = None, operation: str | None = None) -> QualificationCase:
    start = time.monotonic()
    try:
        detail = fn() or ""
    except Exception as exc:  # qualification must preserve the entire matrix
        return QualificationCase(name, FAIL, time.monotonic() - start, f"{type(exc).__name__}: {exc}", backend, fmt, operation)
    return QualificationCase(name, PASS, time.monotonic() - start, str(detail), backend, fmt, operation)


def _fixture(root: Path) -> Path:
    src = root / "fixture"
    (src / "nested").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (src / "nested" / "βeta.txt").write_text("beta\n", encoding="utf-8")
    return src


def _compare_fixture(extracted: Path) -> None:
    base = extracted / "fixture"
    assert (base / "alpha.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (base / "nested" / "βeta.txt").read_text(encoding="utf-8") == "beta\n"
    assert (base / "empty").is_dir()


def _roundtrip_case(fmt_name: str, suffix: str, root: Path) -> QualificationCase:
    fmt = parse_format(fmt_name)
    operation = "create"
    try:
        backend = resolve_backend(fmt, operation, load_config())
    except ArcError as exc:
        return QualificationCase(f"roundtrip:{fmt_name}", SKIP_BACKEND, detail=str(exc), format=fmt_name, operation=operation)

    backend_name = backend.info.binary
    start = time.monotonic()
    try:
        case_root = root / ("case-" + fmt_name.replace(".", "-"))
        case_root.mkdir(parents=True, exist_ok=True)
        if fmt.is_stream:
            source = case_root / f"stream-{fmt_name}.txt"
            source.write_text(f"stream:{fmt_name}\n", encoding="utf-8")
            archive = case_root / f"{source.name}{suffix}"
            rc = _call_arc(["create", archive.name, source.name, "--format", fmt_name, "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"create exit={rc}")
            if detect(archive).canonical != fmt.canonical:
                raise AssertionError(f"identify mismatch: {detect(archive).canonical}")
            rc = _call_arc(["test", archive.name, "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"test exit={rc}")
            out = case_root / "out"
            out.mkdir()
            rc = _call_arc(["extract", archive.name, "-o", "out", "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"extract exit={rc}")
            if (out / source.name).read_text(encoding="utf-8") != source.read_text(encoding="utf-8"):
                raise AssertionError("stream round-trip content mismatch")
        else:
            source = _fixture(case_root)
            archive = case_root / f"bundle{suffix}"
            rc = _call_arc(["create", archive.name, source.name, "--format", fmt_name, "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"create exit={rc}")
            if detect(archive).canonical != fmt.canonical:
                raise AssertionError(f"identify mismatch: {detect(archive).canonical}")
            reader = resolve_backend(fmt, "list", load_config())
            members = reader.list_members(archive)
            if not members:
                raise AssertionError("member listing empty")
            rc = _call_arc(["test", archive.name, "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"test exit={rc}")
            rc = _call_arc(["extract", archive.name, "-o", "out", "--progress", "never", "--quiet"], case_root)
            if rc != 0:
                raise AssertionError(f"extract exit={rc}")
            _compare_fixture(case_root / "out")
        return QualificationCase(f"roundtrip:{fmt_name}", PASS, time.monotonic() - start, backend=backend_name, format=fmt_name, operation="roundtrip")
    except Exception as exc:
        return QualificationCase(f"roundtrip:{fmt_name}", FAIL, time.monotonic() - start, f"{type(exc).__name__}: {exc}", backend_name, fmt_name, "roundtrip")


def _adversarial_cases(root: Path) -> list[QualificationCase]:
    cases: list[QualificationCase] = []

    def bad_zip() -> str:
        path = root / "evil.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("../escape.txt", "no")
        out = root / "evil-zip-out"
        rc = _call_arc(["extract", path.name, "-o", out.name, "--progress", "never", "--quiet"], root)
        if rc != 7:
            raise AssertionError(f"expected unsafe exit 7, got {rc}")
        if (root / "escape.txt").exists():
            raise AssertionError("malicious ZIP escaped destination")
        return "unsafe traversal rejected"

    cases.append(_record("safety:zip-traversal", bad_zip, fmt="zip", operation="extract"))

    def bad_tar() -> str:
        path = root / "evil.tar"
        with tarfile.open(path, "w") as tf:
            info = tarfile.TarInfo("../escape-tar.txt")
            payload = b"no"
            info.size = len(payload)
            import io
            tf.addfile(info, io.BytesIO(payload))
        out = root / "evil-tar-out"
        rc = _call_arc(["extract", path.name, "-o", out.name, "--progress", "never", "--quiet"], root)
        if rc != 7:
            raise AssertionError(f"expected unsafe exit 7, got {rc}")
        if (root / "escape-tar.txt").exists():
            raise AssertionError("malicious TAR escaped destination")
        return "unsafe traversal rejected"

    cases.append(_record("safety:tar-traversal", bad_tar, fmt="tar", operation="extract"))
    return cases


def _corruption_case(root: Path) -> QualificationCase:
    if not shutil.which("zip") or not shutil.which("unzip"):
        return QualificationCase("resilience:corrupt-zip", SKIP_BACKEND, detail="zip/unzip unavailable", format="zip", operation="test")
    path = root / "corrupt.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("file.txt", "content")
    data = path.read_bytes()
    path.write_bytes(data[: max(8, len(data) // 3)])
    rc = _call_arc(["test", path.name, "--progress", "never", "--quiet"], root)
    status = PASS if rc == 5 else FAIL
    return QualificationCase("resilience:corrupt-zip", status, detail=f"exit={rc}", format="zip", operation="test")


def _password_case(root: Path) -> QualificationCase:
    fmt = parse_format("zip")
    try:
        backend = resolve_backend(fmt, "create", load_config(), required_capabilities={"password"})
    except ArcError as exc:
        return QualificationCase("resilience:zip-password", SKIP_CAPABILITY, detail=str(exc), format="zip", operation="password")
    src = root / "secret.txt"
    src.write_text("secret", encoding="utf-8")
    archive = root / "secret.zip"
    rc = _call_arc(["create", archive.name, src.name, "--password", "right", "--progress", "never", "--quiet"], root)
    if rc != 0:
        return QualificationCase("resilience:zip-password", FAIL, detail=f"create exit={rc}", backend=backend.info.binary, format="zip", operation="password")
    wrong = _call_arc(["extract", archive.name, "-o", "wrong", "--password", "wrong", "--progress", "never", "--quiet"], root)
    status = PASS if wrong == 6 else FAIL
    return QualificationCase("resilience:zip-password", status, detail=f"wrong-password-exit={wrong}", backend=backend.info.binary, format="zip", operation="password")


def _huge_manifest_case(root: Path) -> QualificationCase:
    src = root / "many"
    src.mkdir()
    # Large enough to exercise list-file/stdin transport while staying cheap on mobile.
    for i in range(1200):
        (src / f"f-{i:04d}.txt").write_text("x", encoding="utf-8")
    archive = root / "many.tar"
    start = time.monotonic()
    rc = _call_arc(["create", archive.name, src.name, "--progress", "never", "--quiet"], root)
    if rc != 0:
        return QualificationCase("resilience:large-manifest", FAIL, time.monotonic() - start, f"create exit={rc}", format="tar", operation="create")
    members = resolve_backend(parse_format("tar"), "list", load_config()).list_members(archive)
    if len(members) < 1200:
        return QualificationCase("resilience:large-manifest", FAIL, time.monotonic() - start, "member count mismatch", format="tar", operation="create")
    return QualificationCase("resilience:large-manifest", PASS, time.monotonic() - start, "1200-member archive", format="tar", operation="create")




def _operation_matrix(config: dict) -> list[dict]:
    formats = {
        "tar": ["create", "list", "extract", "test", "add", "update", "remove"],
        "tar.gz": ["create", "list", "extract", "test"],
        "zip": ["create", "list", "extract", "test", "add", "update", "remove"],
        "7z": ["create", "list", "extract", "test", "add", "update", "remove"],
        "rar": ["create", "list", "extract", "test", "add", "update", "remove"],
        "gzip": ["create", "extract", "test"],
        "bzip2": ["create", "extract", "test"],
        "xz": ["create", "extract", "test"],
        "zstd": ["create", "extract", "test"],
    }
    rows: list[dict] = []
    inventory = backend_inventory(config)
    candidates = sorted({c["binary"] for role in inventory for c in role["candidates"]})
    for fmt_name, operations in formats.items():
        fmt = parse_format(fmt_name)
        for operation in operations:
            for binary in candidates:
                path = shutil.which(binary)
                if not path:
                    rows.append({
                        "format": fmt_name, "operation": operation, "backend": binary,
                        "status": SKIP_BACKEND, "detail": "not installed",
                    })
                    continue
                try:
                    backend = resolve_backend(fmt, operation, config, forced=binary)
                except ArcError as exc:
                    rows.append({
                        "format": fmt_name, "operation": operation, "backend": binary,
                        "status": SKIP_CAPABILITY, "detail": str(exc),
                    })
                else:
                    rows.append({
                        "format": fmt_name, "operation": operation, "backend": binary,
                        "status": PASS, "detail": "resolver-qualified",
                        "capabilities": sorted(backend.info.capabilities),
                    })
    return rows


def _mutation_case(fmt_name: str, suffix: str, root: Path) -> QualificationCase:
    fmt = parse_format(fmt_name)
    config = load_config()
    mutation_backends: dict[str, object] = {}
    try:
        for operation in ("add", "update", "remove"):
            mutation_backends[operation] = resolve_backend(fmt, operation, config)
    except ArcError as exc:
        return QualificationCase(
            f"mutation:{fmt_name}",
            SKIP_CAPABILITY,
            detail=str(exc),
            format=fmt_name,
            operation="mutation",
        )
    backend_detail = ",".join(
        f"{operation}={candidate.info.binary}"
        for operation, candidate in mutation_backends.items()
    )
    case = root / ("mutation-" + fmt_name.replace(".", "-"))
    case.mkdir(parents=True, exist_ok=True)
    (case / "one.txt").write_text("one-v1", encoding="utf-8")
    archive = case / ("archive" + suffix)
    rc = _call_arc(["create", archive.name, "one.txt", "--progress", "never", "--quiet"], case)
    if rc != 0:
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail=f"create exit={rc}", backend=backend_detail, format=fmt_name, operation="mutation")
    (case / "two.txt").write_text("two", encoding="utf-8")
    rc = _call_arc(["add", archive.name, "two.txt", "--progress", "never", "--quiet"], case)
    if rc != 0:
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail=f"add exit={rc}", backend=backend_detail, format=fmt_name, operation="mutation")
    (case / "one.txt").write_text("one-v2", encoding="utf-8")
    future = time.time() + 3
    os.utime(case / "one.txt", (future, future))
    rc = _call_arc(["update", archive.name, "one.txt", "--progress", "never", "--quiet"], case)
    if rc != 0:
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail=f"update exit={rc}", backend=backend_detail, format=fmt_name, operation="mutation")
    rc = _call_arc(["remove", archive.name, "two.txt", "--progress", "never", "--quiet"], case)
    if rc != 0:
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail=f"remove exit={rc}", backend=backend_detail, format=fmt_name, operation="mutation")
    rc = _call_arc(["extract", archive.name, "-o", "out", "--progress", "never", "--quiet"], case)
    if rc != 0:
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail=f"extract exit={rc}", backend=backend_detail, format=fmt_name, operation="mutation")
    if (case / "out" / "one.txt").read_text(encoding="utf-8") != "one-v2":
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail="updated member content mismatch", backend=backend_detail, format=fmt_name, operation="mutation")
    if (case / "out" / "two.txt").exists():
        return QualificationCase(f"mutation:{fmt_name}", FAIL, detail="removed member still present", backend=backend_detail, format=fmt_name, operation="mutation")
    return QualificationCase(f"mutation:{fmt_name}", PASS, backend=backend_detail, format=fmt_name, operation="mutation")


def _password_cases(root: Path) -> list[QualificationCase]:
    out: list[QualificationCase] = []
    for fmt_name, suffix in (("zip", ".zip"), ("7z", ".7z"), ("rar", ".rar")):
        fmt = parse_format(fmt_name)
        try:
            creator = resolve_backend(fmt, "create", load_config(), required_capabilities={"password"})
            resolve_backend(fmt, "extract", load_config(), required_capabilities={"password"})
        except ArcError as exc:
            out.append(QualificationCase(f"password:{fmt_name}", SKIP_CAPABILITY, detail=str(exc), format=fmt_name, operation="password"))
            continue
        case = root / ("password-" + fmt_name)
        case.mkdir(parents=True, exist_ok=True)
        (case / "secret.txt").write_text("secret", encoding="utf-8")
        archive = case / ("secret" + suffix)
        rc = _call_arc(["create", archive.name, "secret.txt", "--password", "right", "--progress", "never", "--quiet"], case)
        if rc != 0:
            out.append(QualificationCase(f"password:{fmt_name}", FAIL, detail=f"create exit={rc}", backend=creator.info.binary, format=fmt_name, operation="password"))
            continue
        wrong = _call_arc(["extract", archive.name, "-o", "wrong", "--password", "wrong", "--progress", "never", "--quiet"], case)
        out.append(QualificationCase(
            f"password:{fmt_name}", PASS if wrong == 6 else FAIL,
            detail=f"wrong-password-exit={wrong}", backend=creator.info.binary,
            format=fmt_name, operation="password",
        ))
    return out


def _corrupt_tar_case(root: Path) -> QualificationCase:
    path = root / "corrupt.tar"
    payload = root / "corrupt-source.txt"
    payload.write_text("x" * 4096, encoding="utf-8")
    with tarfile.open(path, "w") as tf:
        tf.add(payload, arcname="payload.txt")
    data = path.read_bytes()
    path.write_bytes(data[: max(600, len(data) // 4)])
    rc = _call_arc(["test", path.name, "--progress", "never", "--quiet"], root)
    return QualificationCase(
        "resilience:corrupt-tar", PASS if rc == 5 else FAIL,
        detail=f"exit={rc}", format="tar", operation="test",
    )
def run_qualification(output: Path | None = None, *, include_large_manifest: bool = True) -> dict:
    config = load_config()
    inventory = backend_inventory(config)
    operation_matrix = _operation_matrix(config)
    with tempfile.TemporaryDirectory(prefix="arc-r03-") as td:
        root = Path(td)
        formats = [
            ("tar", ".tar"),
            ("tar.gz", ".tar.gz"),
            ("tar.bz2", ".tar.bz2"),
            ("tar.xz", ".tar.xz"),
            ("tar.zstd", ".tar.zst"),
            ("zip", ".zip"),
            ("7z", ".7z"),
            ("rar", ".rar"),
            ("gzip", ".gz"),
            ("bzip2", ".bz2"),
            ("xz", ".xz"),
            ("zstd", ".zst"),
        ]
        backend_cases = [_roundtrip_case(name, suffix, root) for name, suffix in formats]
        mutation_cases = [_mutation_case(name, suffix, root) for name, suffix in (("tar", ".tar"), ("zip", ".zip"), ("7z", ".7z"), ("rar", ".rar"))]
        safety_cases = _adversarial_cases(root)
        resilience = [_corruption_case(root), _corrupt_tar_case(root), *_password_cases(root)]
        if include_large_manifest:
            resilience.append(_huge_manifest_case(root))

    all_cases = backend_cases + mutation_cases + safety_cases + resilience
    result = {
        "schema_version": 1,
        "capability_matrix": inventory,
        "backend_operation_matrix": operation_matrix,
        "backend_matrix": [asdict(x) for x in backend_cases],
        "mutation_matrix": [asdict(x) for x in mutation_cases],
        "safety_matrix": [asdict(x) for x in safety_cases],
        "resilience_matrix": [asdict(x) for x in resilience],
        "summary": {
            "pass": sum(x.status == PASS for x in all_cases),
            "fail": sum(x.status == FAIL for x in all_cases),
            "failures": [
                {"name": x.name, "detail": x.detail, "backend": x.backend, "format": x.format, "operation": x.operation}
                for x in all_cases if x.status == FAIL
            ],
            "skipped_backend_unavailable": sum(x.status == SKIP_BACKEND for x in all_cases),
            "skipped_capability_unsupported": sum(x.status == SKIP_CAPABILITY for x in all_cases),
            "operation_matrix_pass": sum(x["status"] == PASS for x in operation_matrix),
            "operation_matrix_skipped_backend": sum(x["status"] == SKIP_BACKEND for x in operation_matrix),
            "operation_matrix_skipped_capability": sum(x["status"] == SKIP_CAPABILITY for x in operation_matrix),
            "status": FAIL if any(x.status == FAIL for x in all_cases) else PASS,
        },
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result

