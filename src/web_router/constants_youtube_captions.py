"""Settings for the opt-in YouTube captions extract provider."""

from __future__ import annotations

import re

CAPTIONS_EXECUTABLE = "yt-dlp"
CAPTIONS_TIMEOUT = 30.0
CAPTIONS_MAX_BYTES = 8 * 1024 * 1024
CAPTIONS_LANGUAGE = "language"
CAPTIONS_DEFAULT_LANGUAGE = "en"
CAPTIONS_FILENAME = "captions"
CAPTIONS_FORMAT = "json3"
CAPTIONS_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CAPTIONS_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
CAPTIONS_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
CAPTIONS_SHORT_HOST = "youtu.be"
CAPTIONS_WATCH_PATH = "/watch"
CAPTIONS_SHORT_PATHS = frozenset({"shorts", "live", "embed"})
CAPTIONS_VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"
CAPTIONS_ARGS = (
    "--ignore-config",
    "--no-plugin-dirs",
    "--no-remote-components",
    "--compat-options",
    "no-certifi",
    "--no-cache-dir",
    "--no-playlist",
    "--skip-download",
    "--write-subs",
    "--write-auto-subs",
    "--js-runtimes",
    "node",
    "--print",
    "%(title)j",
    "--no-simulate",
)

MSG_CAPTIONS_URL = "expected a public YouTube video URL (watch, short link, Shorts, live or embed)"
MSG_CAPTIONS_LANGUAGE = "language must be a single YouTube language tag such as 'en' or 'pt-BR'"
MSG_CAPTIONS_OPTIONS = "the only YouTube captions provider option is 'language'"
MSG_CAPTIONS_INSTALL = "Install yt-dlp on PATH, for example with uv tool install 'yt-dlp[default]'."
MSG_CAPTIONS_MISSING = "no captions available in {language!r} for this video"
MSG_CAPTIONS_EMPTY = "the {language!r} caption track contains no text"
MSG_CAPTIONS_SIZE = "the caption track exceeds the maximum size of {size} bytes"
MSG_CAPTIONS_INVALID = "yt-dlp returned invalid JSON3 captions"
MSG_CAPTIONS_FAILED = "yt-dlp failed to fetch captions: {detail}"
MSG_CAPTIONS_TIMEOUT = "yt-dlp did not finish within {seconds:g}s"
