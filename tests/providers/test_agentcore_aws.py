"""AgentCore's AWS side: SigV4 signing, the signing region and the credential.

The fake gateway and credentials are shared with test_agentcore.py, in _agentcore.py.
"""

from __future__ import annotations

import httpx
import pytest

from tests.providers._agentcore import aws_env  # noqa: F401  (autouse fixture)
from tests.providers._agentcore import gateway as _gateway
from tests.providers._agentcore import provider as _provider
from web_router.exceptions import ConfigurationError
from web_router.types import SearchRequest

# -- Signing -----------------------------------------------------------------


def test_requests_are_signed_for_the_gateways_region_without_the_connection_header() -> None:
    seen: list[httpx.Request] = []
    _provider(_gateway(seen=seen)).search(SearchRequest(query="hi"))

    authorization = seen[1].headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/bedrock-agentcore/aws4_request" in authorization
    signed = authorization.split("SignedHeaders=")[1].split(",")[0].split(";")
    assert "host" in signed
    assert "connection" not in signed
    assert "x-amz-date" in seen[1].headers


def test_a_host_without_a_region_falls_back_to_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    seen: list[httpx.Request] = []
    _provider(_gateway(seen=seen), url="https://gateway.example.com/mcp").search(SearchRequest(query="hi"))

    assert "/eu-west-1/bedrock-agentcore/aws4_request" in seen[0].headers["authorization"]


def test_a_host_without_a_region_and_none_configured_raises() -> None:
    with pytest.raises(ConfigurationError, match="region"):
        _provider(_gateway(), url="https://gateway.example.com/mcp").search(SearchRequest(query="hi"))


def test_no_aws_credentials_raise_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")

    with pytest.raises(ConfigurationError, match="No AWS credentials"):
        _provider(_gateway()).search(SearchRequest(query="hi"))


def test_redirects_are_refused_since_the_signature_is_a_credential() -> None:
    with pytest.raises(ConfigurationError, match="redirect"):
        _provider(_gateway(), follow_redirects=True)
