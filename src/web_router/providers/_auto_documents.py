"""Extract ``auto``'s document step: URLs that name a file are parsed before the web chain is tried.

Kept apart from :mod:`web_router.providers.auto`, which mixes it in, so that file stops growing.

A web backend handed a PDF returns the page its viewer renders, or nothing.
So a URL whose path ends in a document extension goes to the document parser
first (LiteParse unless ``document_parser`` names another, ``None`` for none),
and only what it could not read goes down the chain. The parser runs ahead of
the chain rather than in it: a router orders the chain's own members, and one
that has never heard of the parser would drop it.

The step is skipped, and every URL goes down the chain as before, when the
parser is not installed or cannot serve the request: an HTML extract, or an
option the parser does not map. It is skipped too when the call's hook
refuses the parser (a cost cap its unknown price does not fit), so a budget
holds it as it holds a chain member.

Only public URLs go to the parser. LiteParse fetches from this machine,
where the web chain's cloud backends fetch from theirs, so a private or
loopback document URL goes down the chain as it did before the step.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlsplit

from web_router.constants import PROVIDER_LITEPARSE
from web_router.constants_liteparse import DOCUMENT_EXTENSIONS
from web_router.exceptions import DeadlineExceededError, ProviderError
from web_router.providers._chain import RECOVERABLE, _member_options
from web_router.providers.base import ExtractProvider, _Collected
from web_router.routing.attempts import admitted, noted
from web_router.routing.contract import ToolName
from web_router.routing.url_policy import public_only
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_TARGET

EXTRACT: ToolName = "extract"

_Chain = Callable[[list[str]], ExtractResponse]
_AsyncChain = Callable[[list[str]], Awaitable[ExtractResponse]]


def is_document(url: str) -> bool:
    """Whether ``url``'s path ends in a document extension; the query and fragment are not read."""
    return PurePosixPath(urlsplit(url).path).suffix.lower() in DOCUMENT_EXTENSIONS


class DocumentsFirst:
    """Mixed into :class:`~web_router.providers.auto.AutoExtractProvider`; see the module docstring."""

    name: ClassVar[str]
    _members_config: dict[str, dict[str, Any]]

    def __init__(self, *, document_parser: str | None = PROVIDER_LITEPARSE, **kwargs: Any) -> None:
        """
        Args:
            document_parser: The extract backend that reads document URLs before
                the web chain does; ``None`` sends them down the chain.
        """
        super().__init__(**kwargs)
        self.document_parser = document_parser
        self._parser: ExtractProvider | None = None
        self._parser_lock = threading.Lock()

    def _documents_first(self, request: ExtractRequest, urls: list[str], chain: _Chain) -> ExtractResponse:
        """``chain``'s answer for ``urls``, with each document URL the parser reads served by it instead."""
        documents, parser = self._split(request, urls)
        sent = request.model_copy(update={"urls": documents})
        if parser is None or not admitted(EXTRACT, parser.name, sent):
            return chain(urls)
        try:
            parsed = parser.extract(sent)
        except RECOVERABLE as exc:
            noted(EXTRACT, parser.name, exc)
            parsed = _unread(parser.name, documents, exc)
        merge = _Merge(self.name, urls, documents, parsed)
        if merge.rest:
            try:
                merge.collected.add(merge.rest, chain(merge.rest))
            except (ProviderError, DeadlineExceededError) as exc:
                merge.collected.fail(merge.rest, exc)
        return merge.resolved()

    async def _adocuments_first(self, request: ExtractRequest, urls: list[str], chain: _AsyncChain) -> ExtractResponse:
        """Async counterpart of :meth:`_documents_first`."""
        documents, parser = self._split(request, urls)
        sent = request.model_copy(update={"urls": documents})
        if parser is None or not admitted(EXTRACT, parser.name, sent):
            return await chain(urls)
        try:
            parsed = await parser.aextract(sent)
        except RECOVERABLE as exc:
            noted(EXTRACT, parser.name, exc)
            parsed = _unread(parser.name, documents, exc)
        merge = _Merge(self.name, urls, documents, parsed)
        if merge.rest:
            try:
                merge.collected.add(merge.rest, await chain(merge.rest))
            except (ProviderError, DeadlineExceededError) as exc:
                merge.collected.fail(merge.rest, exc)
        return merge.resolved()

    def _split(self, request: ExtractRequest, urls: list[str]) -> tuple[list[str], ExtractProvider | None]:
        """The public document URLs, and the parser to read them; no parser when none is set, built or able to serve."""
        documents = [url for url in urls if is_document(url) and public_only(url) is None]
        if not documents or self.document_parser is None:
            return documents, None
        parser = self._parser_member()
        if parser is None or request.format not in parser.supported_formats:
            return documents, None
        # `provider_options` and a vendor mode are another backend's; the parser would drop them unread.
        if (
            request.provider_options
            or request.mode
            or request.specified_parameters() - {"format"} - parser.supported_parameters
        ):
            return documents, None
        return documents, parser

    def _parser_member(self) -> ExtractProvider | None:
        """The parser, built once; ``None`` when its extra is missing or it cannot be built."""
        from web_router.registry import create_extract_provider

        name = self.document_parser
        with self._parser_lock:
            if self._parser is None and name is not None:
                try:
                    self._parser = create_extract_provider(name, **_member_options(self._members_config, name, {}))
                except RECOVERABLE:
                    return None
            return self._parser

    def _close_parser(self) -> None:
        if self._parser is not None:
            self._parser.close()

    async def _aclose_parser(self) -> None:
        if self._parser is not None:
            await self._parser.aclose()


def _unread(parser: str, documents: list[str], exc: BaseException) -> ExtractResponse:
    """A parser call that raised, as an answer with each document failed, keeping each one's own kind (a 404's)."""
    failures = getattr(exc, "url_failures", None) or [ExtractFailure.from_error(url, exc) for url in documents]
    return ExtractResponse(provider=parser, failures=failures)


class _Merge:
    """The parser's answer and the chain's, as one response.

    A document whose failure is the target's own (a 404) is not sent down the chain: a web backend would fetch the
    same error. The parser's answer goes in first only when it read something, so ``provider`` names who served.
    """

    def __init__(self, owner: str, urls: list[str], documents: list[str], parsed: ExtractResponse) -> None:
        kept = [failure for failure in parsed.failures if failure.kind == ERROR_KIND_TARGET]
        read = [document for document in parsed.documents if document.content.strip()]
        done = {document.url for document in read} | {failure.url for failure in kept}
        self._done = sorted(done, key=documents.index)
        self._answer = parsed.model_copy(update={"documents": read, "failures": kept})
        self.rest = [url for url in urls if url not in done]
        self.collected = _Collected(owner, urls)
        self._added = bool(read)
        if self._added:
            self.collected.add(self._done, self._answer)

    def resolved(self) -> ExtractResponse:
        if self._done and not self._added:
            self.collected.add(self._done, self._answer)
        return self.collected.resolved()
