"""Firecrawl search replies recorded from the live API, for tests/providers/test_firecrawl_search.py.

Both were recorded on 2026-11-24 (``python asyncio tutorial``): ``RECORDED``
with ``limit: 3``, ``RECORDED_DEEP`` with ``limit: 2`` and ``scrapeOptions``,
keeping its first result. Descriptions and the page body are cut short, the
scraped page's metadata keeps five of its keys, and the first description is
padded back past a snippet's length to stand for Firecrawl's long ones (live,
it ran to 628 characters, the third to 51,831). The shapes are Firecrawl's own.
"""

from __future__ import annotations

from typing import Any

RECORDED: dict[str, Any] = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                "title": "A conceptual overview of asyncio",
                "description": "# A conceptual overview of `asyncio` [¶](https://docs.python.org/3/howto/"
                "a-conceptual-overview-of-asyncio.html#a-conceptual-overview-of-asyncio)\n- This in-depth "
                "[YouTube tutorial series](https://www.youtube.com/watch?v=Xbl7XjFYsN4) on\n`asyncio` created by "
                "Python core team member, Łukasz Langa." + " The event loop runs tasks." * 40,
                "position": 1,
            },
            {
                "url": "https://realpython.com/async-io-python/",
                "title": "Python's asyncio: A Hands-On Walkthrough",
                "description": "# Python's asyncio: A Hands-On Walkthrough\nRecommended Course\n\nPython\u2019s "
                "`asyncio` library enables you to write concurrent code using the `async` and `await` keywords.",
                "position": 2,
            },
            {
                "url": "https://www.youtube.com/watch?v=oAkLSJNr5zY",
                "title": "Python Tutorial: AsyncIO - Complete Guide to Asynchronous ...",
                "description": "# Python Tutorial: AsyncIO - Complete Guide to Asynchronous Programming",
                "position": 3,
            },
        ]
    },
    "creditsUsed": 2,
    "id": "01a20dd6-1586-7147-8390-8b9599a07fc4",
}

RECORDED_DEEP: dict[str, Any] = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                "title": "A conceptual overview of asyncio",
                "description": "# A conceptual overview of `asyncio`",
                "position": 1,
                "markdown": '### Navigation\n\n- [index](https://docs.python.org/3/genindex.html "General Index")\n'
                '- [modules](https://docs.python.org/3/py-modindex.html "Python Module Index")',
                "metadata": {
                    "title": "A conceptual overview of asyncio — Python 3.15.2 documentation",
                    "sourceURL": "https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html",
                    "statusCode": 200,
                    "language": "en",
                    "creditsUsed": 1,
                },
            }
        ]
    },
    "creditsUsed": 4,
    "id": "01a20ddc-fcaa-73aa-9e20-a79b61e9b9b6",
}
