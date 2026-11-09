"""SigV4 signing for AgentCore Gateway requests, apart so ``agentcore.py`` stops growing.

Every request :mod:`web_router.providers.agentcore` sends is signed here for
the gateway's region, read from its hostname. Needs botocore, from the ``aws``
extra.
"""

from __future__ import annotations

import os
import re
from collections.abc import Generator
from typing import Any

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

from web_router.constants_agentcore import (
    AGENTCORE_HOST_PATTERN,
    AGENTCORE_SETUP_DOC,
    AGENTCORE_SIGNING_SERVICE,
    AGENTCORE_UNSIGNED_HEADER,
    ENV_AWS_REGION,
    MSG_AGENTCORE_NO_CREDENTIALS,
    MSG_AGENTCORE_NO_REGION,
)
from web_router.exceptions import ConfigurationError

_HOST = re.compile(AGENTCORE_HOST_PATTERN)


class _SigV4(httpx.Auth):
    """Signs each request for the gateway's region, with credentials resolved on first use.

    Resolved late so a provider can be built -- by ``auto``, by a test -- on a
    machine with no AWS credential, and fail only when it is actually called.
    Frozen per request, so a refreshable credential (SSO, an assumed role) is
    renewed by botocore rather than going stale inside a long-lived router.
    """

    requires_request_body = True

    def __init__(self, session: Session, host: str) -> None:
        self._session = session
        self._host = host
        self._credentials: Any = None
        self._region: str | None = None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._credentials is None:
            self._credentials = self._session.get_credentials()
            if self._credentials is None:
                raise ConfigurationError(MSG_AGENTCORE_NO_CREDENTIALS.format(doc=AGENTCORE_SETUP_DOC))
        headers = {name: value for name, value in request.headers.items() if name.lower() != AGENTCORE_UNSIGNED_HEADER}
        signed = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        SigV4Auth(
            self._credentials.get_frozen_credentials(), AGENTCORE_SIGNING_SERVICE, self._signing_region()
        ).add_auth(signed)
        request.headers.update(dict(signed.headers.items()))
        yield request

    def _signing_region(self) -> str:
        if self._region is None:
            match = _HOST.match(self._host)
            region = (
                match["region"]
                if match
                else os.environ.get(ENV_AWS_REGION) or self._session.get_config_variable("region")
            )
            if not region:
                raise ConfigurationError(MSG_AGENTCORE_NO_REGION.format(host=self._host, doc=AGENTCORE_SETUP_DOC))
            self._region = region
        return self._region
