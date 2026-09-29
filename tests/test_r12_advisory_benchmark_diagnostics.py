from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from arc_cli import advisory
from arc_cli.cli import main
from arc_cli.config import ConfigLoadResult
from arc_cli.machine import schema_names


def _valid_config(tmp_path: Path) -> ConfigLoadResult:
    return ConfigLoadResult(tmp_path / "config.toml", False, {}, ())


def test_formats_recommend_is_factual_and_never_selects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "tree"
    source.mkdir()
    (source / "a.txt").write_text("hello")
    monkeypatch.setattr(advisory, "_backend_chain", lambda name, config: ([{"role": "archive", "binary": "fake", "path": "/fake", "profile": {"metadata": ["timestamps"], "encryption": {"write": False}, "archive_features": {"multipart": False}}}], []))
    payload = advisory.format_recommendation(source, {})
    assert payload["schema"] == "arc.format-recommendation/v1"
    assert payload["selection"] is None
    assert "does not choose" in payload["selection_policy"]
    assert not any("score" in row or "rank" in row or "recommended" in row for row in payload["candidates"])
    streams = {row["format"]: row for row in payload["candidates"] if row["tradeoffs"]["single_stream"]}
    assert streams
    assert all(not row["compatible_with_input"] for row in streams.values())


def test_formats_recommend_cli_json(tmp_path: Path, capsys) -> None:
    source = tmp_path / "x.txt"
    source.write_text("x")
    assert main(["formats", "recommend", str(source), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.format-recommendation/v1"
    assert len(payload["candidates"]) == len(advisory.FORMAT_NAMES)


def test_formats_recommend_requires_path(capsys) -> None:
    assert main(["formats", "recommend"]) == 2
    assert "requires PATH" in capsys.readouterr().err


def test_benchmark_generated_corpus_is_reproducible_and_host_specific(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(advisory, "_backend_chain", lambda name, config: ([{"binary": "fake", "role": "archive", "path": "/fake", "profile": {}}], []))

    def fake_run(argv: list[str], env):
        if "create" in argv:
            idx = argv.index("create")
            archive = Path(argv[idx + 1])
            source = Path(argv[idx + 2])
            data = source.read_bytes()
            archive.write_bytes(data[: max(1, len(data) // 2)])
        else:
            idx = argv.index("extract")
            archive = Path(argv[idx + 1])
            out = Path(argv[argv.index("-o") + 1])
            # The benchmark source is a deterministic generated single file.
            # Recover its bytes from the source path recorded by the create call.
            source = next(Path(p) for p in created_sources if Path(p).is_file())
            out.mkdir(parents=True, exist_ok=True)
            (out / source.name).write_bytes(source.read_bytes())
        return {"returncode": 0, "seconds": 0.5, "peak_rss_bytes": 1024, "memory_method": "test", "stdout_tail": "", "stderr_tail": ""}

    created_sources: list[str] = []
    original = fake_run
    def capture(argv, env):
        if "create" in argv:
            idx = argv.index("create")
            created_sources.append(argv[idx + 2])
        return original(argv, env)
    monkeypatch.setattr(advisory, "_run_measured", capture)
    one = advisory.benchmark({}, formats=["tar"], iterations=1, size_mib=1, seed=99)
    created_sources.clear()
    two = advisory.benchmark({}, formats=["tar"], iterations=1, size_mib=1, seed=99)
    assert one["schema"] == "arc.benchmark/v1"
    assert one["host_specific"] is True
    assert one["corpus"]["sha256"] == two["corpus"]["sha256"]
    row = one["results"][0]["iterations"][0]
    assert row["verified_roundtrip"] is True
    assert row["encode"]["throughput_bytes_per_second"] > 0
    assert row["decode"]["throughput_bytes_per_second"] > 0
    assert row["ratio"] == pytest.approx(0.5)
    assert row["encoded_to_input_ratio"] == pytest.approx(0.5)
    assert row["compression_ratio"] == pytest.approx(2.0)
    assert row["space_savings_fraction"] == pytest.approx(0.5)


def test_benchmark_directory_marks_stream_unavailable_without_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = tmp_path / "dir"
    corpus.mkdir()
    (corpus / "x").write_bytes(b"x")
    monkeypatch.setattr(advisory, "_run_measured", lambda *a, **k: pytest.fail("must not execute"))
    payload = advisory.benchmark({}, corpus=corpus, formats=["gzip"], iterations=1)
    assert payload["results"][0]["status"] == "unavailable"
    assert "regular-file" in payload["results"][0]["reason"]


def test_benchmark_bounds_fail_closed() -> None:
    with pytest.raises(Exception, match="iterations"):
        advisory.benchmark({}, formats=["tar"], iterations=0)
    with pytest.raises(Exception, match="size-mib"):
        advisory.benchmark({}, formats=["tar"], size_mib=0)


def _write_mixed_binary_version_tool(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.buffer.write(b'fake-backend 1.2.3\\n' + bytes([0xff, 0x90, 0x00]) + b'BZh9')\n"
    )
    path.chmod(0o755)
    return path


def test_binary_version_tolerates_non_utf8_backend_output(tmp_path: Path) -> None:
    tool = _write_mixed_binary_version_tool(tmp_path / "mixed-version")
    assert advisory._binary_version(str(tool)) == "fake-backend 1.2.3"


def test_diagnostics_bundle_tolerates_non_utf8_backend_version_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _write_mixed_binary_version_tool(tmp_path / "mixed-version")
    monkeypatch.setattr(
        advisory,
        "backend_inventory",
        lambda *args, **kwargs: [
            {
                "role": "compressor",
                "candidates": [
                    {"name": "mixed", "path": str(tool), "installed": True}
                ],
            }
        ],
    )
    monkeypatch.setattr(advisory, "collect_doctor_report", lambda: {"checks": [], "summary": {"pass": 0, "warn": 0, "fail": 0}})
    monkeypatch.setattr(advisory, "alias_status_rows", lambda: [])
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    target = tmp_path / "support.zip"
    advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target)
    with zipfile.ZipFile(target) as zf:
        backends = json.loads(zf.read("backends.json"))
    assert backends[0]["candidates"][0]["version"] == "fake-backend 1.2.3"


def test_diagnostics_bundle_contains_only_declared_json_and_redacts_secret_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARC_SUPPORT_TOKEN", "super-secret-value")
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(advisory, "collect_doctor_report", lambda: {"checks": [{"detail": "super-secret-value"}], "summary": {"pass": 1, "warn": 0, "fail": 0}})
    monkeypatch.setattr(advisory, "alias_status_rows", lambda: [{"path": "/bin/arc", "note": "super-secret-value"}])
    target = tmp_path / "support.zip"
    result = advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target, recent=5)
    assert result["schema"] == "arc.diagnostics-bundle/v1"
    assert result["archive_contents_included"] is False
    assert result["network_probe_performed"] is False
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert names == {"manifest.json", "runtime.json", "config.json", "backends.json", "aliases.json", "doctor.json", "diagnostics.json", "remote-capabilities.json"}
        all_text = "\n".join(zf.read(name).decode() for name in names)
    assert "super-secret-value" not in all_text
    assert "***" in all_text


def test_diagnostics_bundle_does_not_read_arbitrary_archive_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "ARCHIVE-CONTENT-MUST-NOT-LEAK"
    archive = tmp_path / "private.zip"
    archive.write_text(marker)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    target = tmp_path / "support.zip"
    advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target)
    with zipfile.ZipFile(target) as zf:
        all_text = "\n".join(zf.read(name).decode() for name in zf.namelist())
    assert marker not in all_text


def test_diagnostics_existing_path_requires_force(tmp_path: Path) -> None:
    target = tmp_path / "support.zip"
    target.write_bytes(b"old")
    with pytest.raises(Exception, match="--force"):
        advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target)
    result = advisory.build_diagnostics_bundle(_valid_config(tmp_path), output=target, force=True)
    assert result["bytes"] > 3


def test_diagnostics_cli_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    target = tmp_path / "bundle.zip"
    monkeypatch.setenv("ARC_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ARC_CACHE_HOME", str(tmp_path / "cache"))
    assert main(["diagnostics", "bundle", str(target), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.diagnostics-bundle/v1"
    assert target.is_file()


def test_r12_schemas_are_queryable() -> None:
    assert {"format-recommendation-v1", "benchmark-v1", "diagnostics-bundle-v1"} <= set(schema_names())


def test_machine_v1_wraps_r12_result(tmp_path: Path, capsys) -> None:
    source = tmp_path / "x"
    source.write_bytes(b"x")
    assert main(["formats", "recommend", str(source), "--json=v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "arc.machine/v1"
    assert payload["operation"] == "formats"
    assert payload["result"]["schema"] == "arc.format-recommendation/v1"
