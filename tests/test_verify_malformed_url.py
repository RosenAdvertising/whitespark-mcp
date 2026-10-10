"""Real configuration-loader regression for the offline verify command."""

import pytest

from whitespark_mcp import config, setup


@pytest.mark.parametrize("variable", ["WHITESPARK_API_BASE_URL"])
@pytest.mark.parametrize(
    "url", ["https://[invalid", "https://example.invalid:bad", "https://example.invalid:99999"]
)
def test_verify_malformed_url(monkeypatch, capsys, variable, url):
    for key, value in {
        "WHITESPARK_API_BASE_URL": "https://example.invalid",
        "WHITESPARK_API_TOKEN": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ELITE3E_AUDIENCE", raising=False)
    monkeypatch.setenv(variable, url)
    with pytest.raises(config.ConfigError, match=variable):
        config.WhitesparkConfig.from_env()
    assert setup.verify() == 2
    output = capsys.readouterr().err
    assert "whitespark-mcp-setup" in output
    assert variable in output
    assert "Traceback" not in output
    assert url not in output
