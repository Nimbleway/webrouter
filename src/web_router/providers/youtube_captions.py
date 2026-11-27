"""Fetch public YouTube caption tracks using a locally installed yt-dlp."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

from web_router.constants_youtube_captions import (
    CAPTIONS_ARGS,
    CAPTIONS_DEFAULT_LANGUAGE,
    CAPTIONS_EXECUTABLE,
    CAPTIONS_FILENAME,
    CAPTIONS_FORMAT,
    CAPTIONS_HOSTS,
    CAPTIONS_LANGUAGE,
    CAPTIONS_LANGUAGE_TAG,
    CAPTIONS_MAX_BYTES,
    CAPTIONS_SHORT_HOST,
    CAPTIONS_SHORT_PATHS,
    CAPTIONS_TIMEOUT,
    CAPTIONS_VIDEO_ID,
    CAPTIONS_VIDEO_URL,
    CAPTIONS_WATCH_PATH,
    MSG_CAPTIONS_EMPTY,
    MSG_CAPTIONS_FAILED,
    MSG_CAPTIONS_INSTALL,
    MSG_CAPTIONS_INVALID,
    MSG_CAPTIONS_LANGUAGE,
    MSG_CAPTIONS_MISSING,
    MSG_CAPTIONS_OPTIONS,
    MSG_CAPTIONS_SIZE,
    MSG_CAPTIONS_TIMEOUT,
    MSG_CAPTIONS_URL,
)
from web_router.exceptions import ConfigurationError, DeadlineExceededError, ProviderError, UnsupportedParameterError
from web_router.providers._deadline import current_deadline
from web_router.providers.base import ExtractProvider
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    Provider,
)
from web_router.types_errors import ERROR_KIND_BAD_REQUEST, ERROR_KIND_NOT_FOUND, ERROR_KIND_TIMEOUT


class YouTubeCaptionsExtractProvider(ExtractProvider):
    """Read one language's captions as plain text, without an API key."""

    name: ClassVar[str] = Provider.YOUTUBE_CAPTIONS.value
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset({"text"})
    default_format: ClassVar[ExtractFormat] = "text"

    def validate(self, request: ExtractRequest) -> None:
        """Refuse unsupported formats and arbitrary yt-dlp options."""
        super().validate(request)
        unknown = set(request.provider_options) - {CAPTIONS_LANGUAGE}
        if unknown:
            raise UnsupportedParameterError(self.name, unknown, detail=MSG_CAPTIONS_OPTIONS)
        language = request.provider_options.get(CAPTIONS_LANGUAGE, CAPTIONS_DEFAULT_LANGUAGE)
        if not isinstance(language, str) or not CAPTIONS_LANGUAGE_TAG.fullmatch(language):
            raise UnsupportedParameterError(self.name, {CAPTIONS_LANGUAGE}, detail=MSG_CAPTIONS_LANGUAGE)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        with TemporaryDirectory() as directory:
            command, track, language = self._command(request, urls[0], Path(directory))
            wait = _wait()
            try:
                done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=wait)
            except subprocess.TimeoutExpired as exc:
                raise self._timeout(wait, exc) from exc
            return self._response(urls[0], track, language, done.returncode, done.stdout, done.stderr)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        with TemporaryDirectory() as directory:
            command, track, language = self._command(request, urls[0], Path(directory))
            wait = _wait()
            process = await asyncio.create_subprocess_exec(
                *command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), wait)
            except TimeoutError as exc:
                raise self._timeout(wait, exc) from exc
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            return self._response(urls[0], track, language, process.returncode, stdout, stderr)

    def _command(self, request: ExtractRequest, url: str, directory: Path) -> tuple[list[str], Path, str]:
        video_id = _video_id(url)
        executable = shutil.which(CAPTIONS_EXECUTABLE)
        if executable is None:
            raise ConfigurationError(MSG_CAPTIONS_INSTALL)
        language = str(request.provider_options.get(CAPTIONS_LANGUAGE, CAPTIONS_DEFAULT_LANGUAGE))
        command = [
            executable,
            *CAPTIONS_ARGS,
            "--sub-format",
            CAPTIONS_FORMAT,
            "--sub-langs",
            f"^{re.escape(language)}$",
            "-o",
            str(directory / f"{CAPTIONS_FILENAME}.%(ext)s"),
            "--",
            CAPTIONS_VIDEO_URL.format(video_id=video_id),
        ]
        return command, directory / f"{CAPTIONS_FILENAME}.{language}.{CAPTIONS_FORMAT}", language

    def _timeout(self, wait: float, exc: BaseException) -> DeadlineExceededError | ProviderError:
        deadline = current_deadline()
        if deadline is not None:
            return DeadlineExceededError(self.name, deadline.seconds)
        return ProviderError(
            self.name, MSG_CAPTIONS_TIMEOUT.format(seconds=wait), original=exc, kind=ERROR_KIND_TIMEOUT
        )

    def _response(
        self, url: str, track: Path, language: str, code: int | None, stdout: bytes, stderr: bytes
    ) -> ExtractResponse:
        if code != 0:
            detail = stderr.decode(errors="replace").strip()[-500:] or f"exit code {code}"
            raise ProviderError(self.name, MSG_CAPTIONS_FAILED.format(detail=detail))
        if not track.is_file():
            raise ProviderError(self.name, MSG_CAPTIONS_MISSING.format(language=language), kind=ERROR_KIND_NOT_FOUND)
        with track.open("rb") as stream:
            contents = stream.read(CAPTIONS_MAX_BYTES + 1)
        if len(contents) > CAPTIONS_MAX_BYTES:
            raise ProviderError(self.name, MSG_CAPTIONS_SIZE.format(size=CAPTIONS_MAX_BYTES))
        try:
            payload = json.loads(contents)
            text = _caption_text(payload)
        except (ValueError, TypeError) as exc:
            raise ProviderError(self.name, MSG_CAPTIONS_INVALID, original=exc) from exc
        if not text:
            raise ProviderError(self.name, MSG_CAPTIONS_EMPTY.format(language=language), kind=ERROR_KIND_NOT_FOUND)
        document = ExtractedDocument(
            url=url, content=text, format="text", title=_title(stdout), extra={"language": language}
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])


def captions_installed() -> bool:
    """Whether a local yt-dlp executable is on PATH."""
    return shutil.which(CAPTIONS_EXECUTABLE) is not None


def _wait() -> float:
    deadline = current_deadline()
    return deadline.left() if deadline is not None else CAPTIONS_TIMEOUT


def _video_id(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password and parsed.port is None:
            parts = parsed.path.strip("/").split("/")
            video_id = _id_from_path(parsed.hostname, parts, parsed.path, parsed.query)
            if CAPTIONS_VIDEO_ID.fullmatch(video_id):
                return video_id
    except ValueError as exc:
        raise ProviderError(Provider.YOUTUBE_CAPTIONS.value, MSG_CAPTIONS_URL, kind=ERROR_KIND_BAD_REQUEST) from exc
    raise ProviderError(Provider.YOUTUBE_CAPTIONS.value, MSG_CAPTIONS_URL, kind=ERROR_KIND_BAD_REQUEST)


def _id_from_path(host: str | None, parts: list[str], path: str, query: str) -> str:
    if host == CAPTIONS_SHORT_HOST and len(parts) == 1:
        return parts[0]
    if host not in CAPTIONS_HOSTS:
        return ""
    if path == CAPTIONS_WATCH_PATH:
        ids = parse_qs(query, keep_blank_values=True).get("v", [])
        return ids[0] if len(ids) == 1 else ""
    return parts[1] if len(parts) == 2 and parts[0] in CAPTIONS_SHORT_PATHS else ""


def _caption_text(payload: Any) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError(MSG_CAPTIONS_INVALID)
    lines: list[str] = []
    for event in payload["events"]:
        if not isinstance(event, dict) or not isinstance(segments := event.get("segs"), list):
            continue
        text = "".join(segment.get("utf8", "") for segment in segments if isinstance(segment, dict))
        for line in text.splitlines():
            line = line.strip()
            if line and (not lines or lines[-1] != line):
                lines.append(line)
    return "\n".join(lines)


def _title(stdout: bytes) -> str:
    try:
        title = json.loads(stdout)
    except ValueError:
        return ""
    return title if isinstance(title, str) else ""
