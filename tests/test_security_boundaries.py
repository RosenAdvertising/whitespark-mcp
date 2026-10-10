"""Regression probes for credential destination validation."""

import pytest

from whitespark_mcp.config import ConfigError, WhitesparkConfig


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/api",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:secret@example.test/api",
        "https://example.test/api?q=secret",
        "https://example.test/api#secret",
        "https://example.test/api?",
        "https://example.test/api#",
        "https://:443",
        "https://example.test:bad",
        "https://example.test:99999",
        "https://example.test\\@evil.test",
        "https://example.test\n/path",
        "https://%65xample.test",
        "https://example.test:",
    ],
)
def test_credential_urls_reject_review_probes(url):
    with pytest.raises(ConfigError, match="HTTPS"):
        WhitesparkConfig(api_token="synthetic", api_base_url=url)


@pytest.mark.anyio
@pytest.mark.parametrize("location", ["http://example.test/grids", "https://other.test/grids"])
async def test_redirect_never_forwards_bearer(location):
    import httpx

    from whitespark_mcp.client import WhitesparkAPIError, WhitesparkClient

    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(302, headers={"Location": location})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as http:
        client = WhitesparkClient(
            WhitesparkConfig("synthetic", "https://example.test"), http_client=http
        )
        with pytest.raises(WhitesparkAPIError):
            await client.list_grids_page()
    assert len(sent) == 1
    assert sent[0].url.scheme == "https"
    assert sent[0].headers["Authorization"] == "Bearer synthetic"
