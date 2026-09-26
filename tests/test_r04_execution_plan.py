from __future__ import annotations

from arc_cli.backends import run_backend
from arc_cli.execution import begin_plan, emit_after, plan_dict, record_stage


def test_native_plan_redacts_credentials_and_has_structured_json(capsys):
    begin_plan("create", mode="after", style="exact")
    run_backend(
        ["7z", "a", "-psecret-value", "archive.7z", "file.txt"],
        {"redact": ["secret-value"], "cleanup": []},
        dry_run=True,
    )
    data = plan_dict()
    assert data["operation"] == "create"
    assert "secret-value" not in str(data)
    assert "<redacted>" in data["stages"][0]["display"]
    emit_after()
    captured = capsys.readouterr()
    assert "secret-value" not in captured.err + captured.out


def test_reproducible_plan_hides_internal_staging_paths():
    begin_plan("create", mode=None, style="reproducible")
    record_stage(
        "backend",
        ["7z", "a", "/tmp/arc-remote-stage-abc/archive.7z", "@/tmp/arc-list-123"],
        implementation_paths=["/tmp/arc-list-123"],
    )
    stage = plan_dict()["stages"][0]
    assert "/tmp/arc-remote-stage" not in stage["reproducible"]
    assert "<staged-archive>" in stage["reproducible"]
    assert "<manifest>" in stage["reproducible"]


def test_before_mode_prints_as_stage_is_recorded(capsys):
    begin_plan("test", mode="before", style="reproducible")
    record_stage("backend", ["unzip", "-t", "a.zip"])
    captured = capsys.readouterr()
    assert "native:" in captured.err + captured.out
    assert "unzip -t a.zip" in captured.err + captured.out


def test_native_pipeline_renders_real_copyable_pipeline():
    begin_plan("create", mode=None, style="exact")
    run_backend(
        ["tar", "-cf", "-", "src"],
        {"pipeline": ["zstd", "-T4", "-c"], "stdout_file": "backup.tar.zst", "cleanup": []},
        dry_run=True,
    )
    stage = plan_dict()["stages"][0]
    assert stage["display"] == "tar -cf - src | zstd -T4 -c > backup.tar.zst"
    assert "<backend>" not in stage["display"]
    assert stage["pipeline"] == [["zstd", "-T4", "-c"]]


def test_devtool_r04_profile_is_tests_only_and_reusable():
    import tomllib
    from pathlib import Path

    config = tomllib.loads(Path('.devtool.toml').read_text(encoding='utf-8'))
    profile = config['test_profiles']['r04']
    assert profile['pytest_tests'] == [
        'tests/test_r04_execution_plan.py',
        'tests/test_r04_remote_transport.py',
    ]
    assert profile['pytest_include_defaults'] is False
    assert profile['pytest_args'] == ['--maxfail=1']

    test_choices = config['wrapper']['commands']['test']['options']['profile']['choices']
    validate_choices = config['wrapper']['commands']['validate']['options']['profile']['choices']
    assert 'r04' in test_choices
    assert 'r04' in validate_choices

    job = config['targets']['arc']['jobs']['r04-tests']
    assert job['runner'] == 'command'
    assert job['command'] == ['python3', 'scripts/run_r04_tests.py']
    assert Path('scripts/run_r04_tests.py').is_file()
