"""Obscura provider: page contents rendered by the Obscura headless browser, run on this machine.

Extract only, and keyless: each URL is one ``obscura fetch`` of a binary the
caller installed (github.com/h4ckf0r0day/obscura), which loads the page, runs its
scripts and prints the result. So it reads pages that only fill in after their
JavaScript runs, without Chromium and without a call to any vendor. Opt-in: it is
in no ``auto`` chain, so a caller names it, as ``local`` is named for a browser.
The binary blocks loopback and private addresses on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import shutil
import subprocess
from typing import Any, ClassVar
from urllib.parse import urlsplit

from web_router.constants import ENV_OBSCURA_PATH, PROVIDER_OBSCURA
from web_router.constants_obscura import (
    ENV_OBSCURA_ALLOW_PRIVATE_NETWORK,
    MSG_OBSCURA_BAD_PATH,
    MSG_OBSCURA_EMPTY,
    MSG_OBSCURA_EXITED,
    MSG_OBSCURA_NO_PROVIDER_OPTIONS,
    MSG_OBSCURA_NO_SCHEME,
    MSG_OBSCURA_NOT_FOUND,
    MSG_OBSCURA_NOT_TEXT,
    MSG_OBSCURA_SCHEME,
    MSG_OBSCURA_TIMED_OUT,
    OBSCURA_COMMAND,
    OBSCURA_EXIT_GRACE,
    OBSCURA_EXTRACT_DEFAULT_FORMAT,
    OBSCURA_EXTRACT_FORMATS,
    OBSCURA_FETCH_ARGV,
    OBSCURA_FETCH_TIMEOUT,
    OBSCURA_MAX_REPLACEMENT_SHARE,
    OBSCURA_PDF_MAGIC,
    OBSCURA_SCHEMES,
    OBSCURA_STDERR_TAIL,
)
from web_router.exceptions import (
    ConfigurationError,
    DeadlineExceededError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers._deadline import current_deadline
from web_router.providers.base import ExtractProvider
from web_router.types import EXTRACT_FORMAT_ONLY, ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_BAD_REQUEST, ERROR_KIND_TIMEOUT


class ObscuraExtractProvider(ExtractProvider):
    """Fetches page contents with a local Obscura binary, one process per URL."""

    name: ClassVar[str] = PROVIDER_OBSCURA
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(OBSCURA_EXTRACT_FORMATS)
    default_format: ClassVar[ExtractFormat] = OBSCURA_EXTRACT_DEFAULT_FORMAT

    def __init__(self, *, executable_path: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            executable_path: The ``obscura`` binary to run. Defaults to ``OBSCURA_PATH``, then ``obscura`` on
                ``PATH``; looked up at each call, where a named path that is not an executable file raises
                :class:`~web_router.exceptions.ConfigurationError` rather than falling through.
            **kwargs: Stored as :attr:`options`, as on every provider.
        """
        super().__init__(**kwargs)
        self.executable_path = executable_path

    def validate(self, request: ExtractRequest) -> None:
        """As every provider's, and ``provider_options`` refused: none of the binary's flags are passed through."""
        super().validate(request)
        if request.provider_options:
            raise UnsupportedParameterError(self.name, {"provider_options"}, detail=MSG_OBSCURA_NO_PROVIDER_OPTIONS)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        command, wait = self._command(request, urls[0])
        try:
            done = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True, timeout=wait, env=_fetch_environment()
            )
        except subprocess.TimeoutExpired as exc:
            deadline = current_deadline()
            if deadline is not None:
                raise DeadlineExceededError(self.name, deadline.seconds) from exc
            raise self._timed_out(wait, exc) from exc
        return self._to_response(request, urls[0], done.returncode, done.stdout, done.stderr)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        command, wait = self._command(request, urls[0])
        process = await asyncio.create_subprocess_exec(
            *command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_fetch_environment()
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), wait)
        except TimeoutError as exc:
            raise self._timed_out(wait, exc) from exc
        finally:
            # A timeout, or the caller's deadline cancelling this call: never leave the browser running.
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        return self._to_response(request, urls[0], process.returncode, stdout, stderr)

    def _command(self, request: ExtractRequest, url: str) -> tuple[list[str], float]:
        """The argv for one fetch, and how long to wait for it.

        Under a deadline both are its remainder, so the call never outlives it; without one the binary gets its own
        default and a grace period past it to stop on its own before it is killed.
        """
        scheme = urlsplit(url).scheme
        if scheme not in OBSCURA_SCHEMES:
            raise ProviderError(
                self.name,
                MSG_OBSCURA_SCHEME.format(scheme=repr(scheme) if scheme else MSG_OBSCURA_NO_SCHEME),
                kind=ERROR_KIND_BAD_REQUEST,
            )
        deadline = current_deadline()
        seconds = deadline.left() if deadline is not None else OBSCURA_FETCH_TIMEOUT
        wait = seconds if deadline is not None else seconds + OBSCURA_EXIT_GRACE
        filled = {"dump": OBSCURA_EXTRACT_FORMATS[self.format_of(request)], "timeout": math.ceil(seconds), "url": url}
        return [find_obscura(self.executable_path), *(arg.format(**filled) for arg in OBSCURA_FETCH_ARGV)], wait

    def _to_response(
        self, request: ExtractRequest, url: str, code: int | None, stdout: bytes, stderr: bytes
    ) -> ExtractResponse:
        if code != 0:
            detail = stderr.decode(errors="replace").strip()[-OBSCURA_STDERR_TAIL:]
            raise ProviderError(self.name, MSG_OBSCURA_EXITED.format(code=code, detail=detail))
        content = stdout.decode(errors="replace")
        if (
            content.startswith(OBSCURA_PDF_MAGIC)
            or content.count("\ufffd") > len(content) * OBSCURA_MAX_REPLACEMENT_SHARE
        ):
            raise ProviderError(self.name, MSG_OBSCURA_NOT_TEXT)
        if not content.strip():
            raise ProviderError(self.name, MSG_OBSCURA_EMPTY)
        body_format = self.format_of(request)
        document = ExtractedDocument(url=url, content=content, format=body_format)
        raw = {"url": url, "dump": OBSCURA_EXTRACT_FORMATS[body_format], "output": content}
        return ExtractResponse(provider=self.name, documents=[document], raw=[raw])

    def _timed_out(self, seconds: float, exc: BaseException) -> ProviderError:
        return ProviderError(
            self.name, MSG_OBSCURA_TIMED_OUT.format(seconds=seconds), original=exc, kind=ERROR_KIND_TIMEOUT
        )


def _fetch_environment() -> dict[str, str]:
    # The inherited variable bypasses the same guard as the refused --allow-private-network flag.
    return {key: value for key, value in os.environ.items() if key != ENV_OBSCURA_ALLOW_PRIVATE_NETWORK}


def find_obscura(explicit: str | None = None) -> str:
    """The ``obscura`` binary the provider runs: ``explicit``, else ``OBSCURA_PATH``, else the one on ``PATH``.

    Raises:
        ConfigurationError: If a named path is not an executable file, or none is named and none is on ``PATH``.
    """
    named = explicit or os.environ.get(ENV_OBSCURA_PATH)
    if named:
        # As ``local``'s find_browser: ``which`` keeps a relative path's directory, so PATH is never searched for it.
        found = shutil.which(os.path.expanduser(named))
        if found is None:
            raise ConfigurationError(MSG_OBSCURA_BAD_PATH.format(path=named, env_var=ENV_OBSCURA_PATH))
        return found
    found = shutil.which(OBSCURA_COMMAND)
    if found is None:
        raise ConfigurationError(MSG_OBSCURA_NOT_FOUND.format(env_var=ENV_OBSCURA_PATH))
    return found


def obscura_installed() -> bool:
    """Whether :func:`find_obscura` finds a binary, so the provider can run here."""
    try:
        find_obscura()
    except ConfigurationError:
        return False
    return True
