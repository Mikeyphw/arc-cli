from arc_cli.completion import zsh_completion, completion_candidates


def test_zsh_completion_has_integrations():
    text = zsh_completion()
    assert "#compdef arc" in text
    assert "fzf" in text
    assert "__complete" in text


def test_format_completion():
    assert "tar.zstd" in completion_candidates(["create", "x", "--format", "tar."])
