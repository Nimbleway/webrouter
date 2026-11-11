"""The vendor catalog's auth models: the credentials and settings a vendor's calls read, and how they carry them.

Apart from :mod:`web_router.vendors.schema`, which re-exports every model
here, so the file format's top-level models stay small.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from web_router.constants_vendors import (
    MSG_VENDOR_CATALOG_SCHEME,
    MSG_VENDOR_OAUTH_LITERAL,
    VENDOR_ENV_PATTERN,
)
from web_router.vendors.schema_base import _one_line, _Strict


def _env_names(value: list[str]) -> list[str]:
    for name in value:
        if not re.match(VENDOR_ENV_PATTERN, name):
            raise ValueError(f"{name!r} is not an environment variable name")
    return value


class VendorSetting(_Strict):
    """A non-secret value a vendor's calls need: a region, an account id, an API version."""

    env: list[str] = Field(min_length=1, description="Variables read in order; the first that is set wins.")
    default: str | None = Field(default=None, description="Used when no variable is set.")
    choices: list[str] | None = Field(default=None, description="The only values accepted, when the set is closed.")
    description: str = ""

    _check_env = field_validator("env")(_env_names)
    _check_one_line = field_validator("default", "description")(_one_line)


class VendorCredential(_Strict):
    """A secret a vendor's calls need. Never logged, and masked out of every error the vendor returns."""

    env: list[str] = Field(min_length=1, description="Variables read in order; the first that is set wins.")
    required: bool = Field(default=True, description="False when only some calls need it.")
    description: str = ""

    _check_env = field_validator("env")(_env_names)
    _check_one_line = field_validator("description")(_one_line)


class VendorOAuth2(_Strict):
    """How to mint an access token before a call. The token is cached until shortly before it expires."""

    grant: Literal["refresh_token", "client_credentials"]
    token_url: str = Field(description="Literal: sent as written, so it may hold no {placeholder}.")
    client_id: str = Field(description="A template, usually '{client_id}'.")
    client_secret: str = Field(description="A template, usually '{client_secret}'.")
    refresh_token: str | None = Field(default=None, description="A template; required for the refresh_token grant.")
    scope: str | None = Field(default=None, description="Literal: sent as written, so it may hold no {placeholder}.")
    client_auth: Literal["body", "basic"] = Field(
        default="body", description="Where the client id and secret go: the form body, or HTTP Basic."
    )
    client_id_param: str = Field(
        default="client_id",
        description="The form field the client id goes in, for vendors that rename it (TikTok: client_key).",
    )

    @model_validator(mode="after")
    def _grant_inputs(self) -> VendorOAuth2:
        if self.grant == "refresh_token" and not self.refresh_token:
            raise ValueError("the refresh_token grant needs a refresh_token template")
        if not self.token_url.startswith("https://"):
            raise ValueError(MSG_VENDOR_CATALOG_SCHEME.format(url=self.token_url))
        for name, value in (("token_url", self.token_url), ("scope", self.scope or "")):
            if "{" in value or "}" in value:
                raise ValueError(MSG_VENDOR_OAUTH_LITERAL.format(field=name, value=value))
        return self

    def templates(self) -> list[str]:
        return [t for t in (self.client_id, self.client_secret, self.refresh_token) if t]


class VendorInjection(_Strict):
    """One value placed on every request: a header, a query parameter or a top-level JSON body field.

    A ``body`` injection makes the request carry a JSON object even when the
    caller sent none, and overwrites a caller field of the same name.
    """

    location: Literal["header", "query", "body"] = Field(alias="in")
    name: str = Field(min_length=1)
    value: str = Field(description="A template, e.g. 'Bearer {access_token}' or '{api_key}'.")
    optional: bool = Field(
        default=False, description="Skip this one, rather than fail, when a value it names is not set."
    )


class VendorBasicAuth(_Strict):
    """HTTP Basic auth, rendered from templates."""

    username: str
    password: str = ""


class VendorAuth(_Strict):
    """Everything a vendor's calls carry to authenticate."""

    credentials: dict[str, VendorCredential] = Field(default_factory=dict)
    oauth2: VendorOAuth2 | None = None
    basic: VendorBasicAuth | None = None
    inject: list[VendorInjection] = Field(default_factory=list)

    def templates(self) -> list[str]:
        found = [injection.value for injection in self.inject]
        if self.basic is not None:
            found += [self.basic.username, self.basic.password]
        if self.oauth2 is not None:
            found += self.oauth2.templates()
        return found
