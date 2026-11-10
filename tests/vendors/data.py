"""A small vendor catalog of made-up vendors, and the credentials the tests hold for them.

The made-up vendors exercise every auth shape the format has -- header keys,
a query key, HTTP Basic, an OAuth refresh, a region-dependent host -- so the
caller is tested against the format rather than against whichever real
vendors happen to ship.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import web_router

_COMMON: dict[str, Any] = {
    "docs_url": "https://docs.example.com",
    "sources": ["https://docs.example.com"],
    "curated": "2026-11-08",
}
_DOCS = {"docs": "https://docs.example.com/x"}


def _endpoint(id_: str, method: str, path: str, *, kind: str = "read", **extra: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "name": id_,
        "kind": kind,
        "method": method,
        "path": path,
        "docs_url": "https://docs.example.com/x",
        "verified": _DOCS,
        **extra,
    }


ACME: dict[str, Any] = {
    **_COMMON,
    "vendor": "acme",
    "name": "Acme Metrics",
    "category": "observability",
    "summary": "Metrics and logs from Acme.",
    "base_url": "https://api.{region}.acme.test",
    "settings": {
        "region": {"env": ["ACME_REGION"], "default": "us", "choices": ["us", "eu"]},
        "org": {"env": ["ACME_ORG"]},
    },
    "auth": {
        "credentials": {
            "api_key": {"env": ["ACME_API_KEY"], "description": "an Acme API key"},
            "app_key": {"env": ["ACME_APP_KEY", "ACME_APPLICATION_KEY"]},
        },
        "inject": [
            {"in": "header", "name": "X-Api-Key", "value": "{api_key}"},
            {"in": "header", "name": "X-App-Key", "value": "{app_key}"},
            {"in": "header", "name": "X-Org", "value": "{org}", "optional": True},
        ],
    },
    "headers": {"Acme-Version": "2026-01"},
    "endpoints": [
        _endpoint(
            "acme.metrics.query",
            "GET",
            "/v1/query",
            params={
                "query": {"in": "query", "required": True},
                "from": {"in": "query", "type": "integer", "required": True},
                "tags": {"in": "query", "type": "array"},
                "rollup": {"in": "query", "type": "boolean"},
                "filter": {"in": "query", "type": "object"},
            },
            job="metrics.query",
        ),
        _endpoint(
            "acme.logs.search",
            "POST",
            "/v2/logs/search",
            params={
                "filter": {"in": "body", "type": "object", "required": True},
                "cursor": {"in": "query", "wire_name": "page[cursor]"},
                "trace": {"in": "header", "wire_name": "X-Trace"},
            },
            job="logs.search",
        ),
        _endpoint(
            "acme.monitors.get",
            "GET",
            "/v1/monitor/{monitor_id}",
            params={"monitor_id": {"in": "path", "required": True}},
        ),
        _endpoint(
            "acme.monitors.delete",
            "DELETE",
            "/v1/monitor/{monitor_id}",
            kind="write",
            params={"monitor_id": {"in": "path", "required": True}},
        ),
    ],
}

QUERYKEY: dict[str, Any] = {
    **_COMMON,
    "vendor": "querykey",
    "name": "Query Key Co",
    "category": "seo",
    "summary": "A vendor that takes its key in the query string.",
    "base_url": "https://api.querykey.test",
    "auth": {
        "credentials": {"key": {"env": ["QUERYKEY_KEY"]}},
        "inject": [{"in": "query", "name": "api_key", "value": "{key}"}],
    },
    "endpoints": [_endpoint("querykey.domain.overview", "GET", "/overview", params={"domain": {"in": "query"}})],
}

BASIC: dict[str, Any] = {
    **_COMMON,
    "vendor": "basicco",
    "name": "Basic Co",
    "category": "enrichment",
    "summary": "HTTP Basic auth.",
    "base_url": "https://api.basic.test",
    "auth": {
        "credentials": {"login": {"env": ["BASIC_LOGIN"]}, "password": {"env": ["BASIC_PASSWORD"]}},
        "basic": {"username": "{login}", "password": "{password}"},
    },
    "endpoints": [_endpoint("basicco.account.get", "GET", "/account")],
}

OAUTH: dict[str, Any] = {
    **_COMMON,
    "vendor": "oauthco",
    "name": "OAuth Co",
    "category": "advertising",
    "summary": "An OAuth refresh-token vendor with a second static header.",
    "base_url": "https://ads.oauth.test/v3",
    "auth": {
        "credentials": {
            "client_id": {"env": ["OAUTHCO_CLIENT_ID"]},
            "client_secret": {"env": ["OAUTHCO_CLIENT_SECRET"]},
            "refresh_token": {"env": ["OAUTHCO_REFRESH_TOKEN"]},
            "developer_token": {"env": ["OAUTHCO_DEVELOPER_TOKEN"]},
        },
        "oauth2": {
            "grant": "refresh_token",
            "token_url": "https://auth.oauth.test/token",
            "client_id": "{client_id}",
            "client_secret": "{client_secret}",
            "refresh_token": "{refresh_token}",
        },
        "inject": [
            {"in": "header", "name": "Authorization", "value": "Bearer {access_token}"},
            {"in": "header", "name": "developer-token", "value": "{developer_token}"},
        ],
    },
    "endpoints": [
        _endpoint(
            "oauthco.report.query",
            "POST",
            "/customers/{customer_id}/search",
            params={"customer_id": {"in": "path", "required": True}, "query": {"in": "body", "required": True}},
        )
    ],
}

KEYLESS: dict[str, Any] = {
    **_COMMON,
    "vendor": "openco",
    "name": "Open Co",
    "category": "developer",
    "summary": "No credentials at all.",
    "base_url": "https://open.test",
    "endpoints": [_endpoint("openco.items.list", "GET", "/items")],
}

ALL_VENDORS = (ACME, QUERYKEY, BASIC, OAUTH, KEYLESS)

CREDENTIALS = {
    "acme": {"api_key": "acme-api-secret", "app_key": "acme-app-secret"},
    "querykey": {"key": "query-key-secret"},
    "basicco": {"login": "user", "password": "basic-password-secret"},
    "oauthco": {
        "client_id": "client-123",
        "client_secret": "client-secret-xyz",
        "refresh_token": "refresh-secret-abc",
        "developer_token": "dev-token-secret",
    },
}


def shipped_dir() -> Path:
    """Where the package's own vendor files are."""
    return Path(web_router.__file__).parent / "data" / "vendors"
