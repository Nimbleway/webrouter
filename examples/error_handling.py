"""Every way a call can fail, and what to do about each.

Every exception derives from `WebRouterError`, so one `except` catches the lot
when you do not care which went wrong. They are **siblings, not a hierarchy** --
`ProviderNotInstalledError` is not a kind of `ConfigurationError` -- so catch
the base class unless you mean one specific case.

Most of this runs offline and needs no credentials.

Run it with::

    make setup   # in your webrouter clone: every extra
    python examples/error_handling.py
"""

from __future__ import annotations

from web_router import (
    ConfigurationError,
    Provider,
    ProviderError,
    ProviderNotFoundError,
    ProviderNotInstalledError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouter,
    WebRouterError,
)

KEYLESS = Provider.YOU_MCP
"""Needs no credential, so it constructs offline -- handy for showing validation errors."""

KEYLESS_EXTRACT = Provider.JINA
"""The same, for the extract side."""


def unknown_provider() -> None:
    """A name nothing is registered under. The message lists every name that is.

    A string, deliberately: `Provider.NIMBLE` could not express this typo, which
    is the argument for writing the enum everywhere you type a name yourself.
    """
    print("\n1. ProviderNotFoundError -- typo in the provider name")
    try:
        WebRouter("nimlbe")
    except ProviderNotFoundError as exc:
        print(f"   {exc}")
        print(f"   available: {', '.join(exc.available)}")


def missing_extra() -> None:
    """The provider is known, its optional dependency is not installed."""
    print("\n2. ProviderNotInstalledError -- the extra is not installed")
    try:
        WebRouter(Provider.NIMBLE).search("anything")
    except ProviderNotInstalledError as exc:
        # The message carries the exact pip command, so it is worth printing.
        print(f"   {exc}")
    except WebRouterError:
        print("   (nimble is installed here, so this one cannot be shown)")


def missing_credential() -> None:
    """A provider you named, with no key for it. Never silently downgraded."""
    print("\n3. ConfigurationError -- no key for a provider you named")
    try:
        WebRouter(Provider.TAVILY, provider_config={Provider.TAVILY: {"api_key": None}}).search("anything")
    except ConfigurationError as exc:
        # Names the variable to set and the provider_config alternative.
        print(f"   {exc}")
    except WebRouterError as exc:
        # Either TAVILY_API_KEY is set, or the extra is missing.
        print(f"   (cannot be shown here -- {type(exc).__name__})")


def unmapped_option() -> None:
    """An option this backend does not map. Raised rather than dropped.

    This is the rule the package exists to enforce: a search that quietly
    ignored `country="US"` would return results you would trust and should not.
    """
    print("\n4. UnsupportedParameterError -- the backend does not map an option you set")
    try:
        WebRouter(KEYLESS).search("anything", country="US")
    except UnsupportedParameterError as exc:
        print(f"   {exc}")
        print(f"   unmapped: {exc.parameters}")
        print("   -> pass it through provider_options, or map it (see CONTRIBUTING.md)")


def unsupported_format() -> None:
    """A format this backend cannot return. Raised rather than substituted.

    The same rule one layer down: code that pipes `document.content` into a
    markdown parser has no way to notice it was handed HTML instead.
    """
    print("\n5. UnsupportedFormatError -- the backend cannot return the format you asked for")
    try:
        # Tavily cannot return HTML, and its anonymous tier needs no credential,
        # so this raises before anything is fetched, anywhere.
        tavily = WebRouter(Provider.TAVILY, provider_config={Provider.TAVILY: {"allow_keyless": True}})
        tavily.extract("https://example.com", format="html")
    except UnsupportedFormatError as exc:
        print(f"   {exc}")
        print(f"   it can return: {exc.supported}")
        print("   -> ask for one of those, or name a backend that returns html")


def a_url_that_could_not_be_fetched() -> None:
    """Not an exception at all, when something else came back.

    A partly successful extract returns what it got and reports the rest, so
    one dead link does not cost you the pages that worked. Only a call where
    *nothing* came back raises.
    """
    print("\n6. ExtractResponse.failures -- a partial extract reports rather than raises (needs network)")
    try:
        response = WebRouter(KEYLESS_EXTRACT).extract(["https://example.com", "https://not-a-real-domain-zzz9.invalid"])
    except WebRouterError as exc:
        print(f"   {type(exc).__name__}: {exc}")
        return

    print(f"   got {len(response)} document(s) from {response.provider}")
    for failure in response.failures:
        print(f"   failed: {failure.url} -- {failure.error[:80]}")
    print("   -> check `failures`; a call that returned nothing at all raises instead")


def backend_failure() -> None:
    """The backend itself failed. The original error is wrapped, not swallowed."""
    print("\n7. ProviderError -- the backend failed (needs network)")
    try:
        # A host that does not serve this API: a connection error, wrapped.
        WebRouter(KEYLESS, provider_config={KEYLESS: {"base_url": "https://127.0.0.1:9"}}).search("anything")
    except ProviderError as exc:
        print(f"   {exc}")
        # __cause__ and .original both hold the underlying httpx/SDK exception,
        # so you can inspect a status code or retry on a timeout.
        print(f"   original: {type(exc.original).__name__}")
    except WebRouterError as exc:
        print(f"   {type(exc).__name__}: {exc}")


def catch_them_all() -> None:
    """In real code this is usually all you want."""
    print("\n8. one except for the lot")
    try:
        WebRouter("nimlbe").search("anything")  # the typo again
    except WebRouterError as exc:
        print(f"   {type(exc).__name__}: {exc}")


def main() -> None:
    unknown_provider()
    missing_extra()
    missing_credential()
    unmapped_option()
    unsupported_format()
    a_url_that_could_not_be_fetched()
    backend_failure()
    catch_them_all()


if __name__ == "__main__":
    main()
