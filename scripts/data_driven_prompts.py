"""The requests the data-driven router's golden file is built from: the same list on every run.

Each request is an opener, a lead word, an ask, a place and a tail, drawn so that every value of every feature
turns up many times; a few fixed odd ones follow. Add words here, never remove them: a golden file is only as
good as the requests in it.
"""

from __future__ import annotations

import random

OPENERS = ("", "", "", "please ", "Kindly ", "Can you ", "could you ", "I need you to ", "I'd like you to help me ")
OPENERS += ("help me to ", "let's ", "Lets ", "I want to ", "i wish you to ", "  ", '"', "Hi, ")

LEAD_WORDS = (
    "extract scrape pull parse fetch crawl grab copy add book buy purchase order subscribe sign log register click "
    "fill submit reserve apply open find locate search look_up look_for browse navigate go check view show visit "
    "identify list compile collect gather count track enumerate tabulate give_me_a_list_of give_me_list analyze "
    "analyse research conduct write summarize summarise compare explain discuss evaluate assess investigate review "
    "describe who whom whose what when where which why how is are was were do does did can could has have had will "
    "would should according_to in_which on_what for_whom latest best the python cheapest tell_me get download "
    "booking lists finder extraction"
)
"""The words a request may lead with, an underscore for a space: every verb the features know, and question words."""

LEADS = LEAD_WORDS.split()

ASKS = (
    "the price of a PS5",
    "all the laptops under $500",
    "the top 10 CRM tools",
    "top five running shoes",
    "the CEO of Acme",
    "every supplier in Spain",
    "each entry in the table",
    "a report on EV battery supply chains",
    "the pros and cons of nuclear power",
    "trends across European energy markets",
    "an in-depth overview of RAG",
    "a comprehensive comparison of Postgres and MySQL",
    "which teams won the cup in the 1990s",
    "why the sky is blue",
    "how does a heat pump work",
    "the impact of tariffs on steel",
    "any flights to Lisbon next week",
    "a table for two on Friday",
    "the opening hours",
    "the survey results and their implications",
    "the names and emails",
    "count the open issues",
    "a poem about cats",
    "",
)

PLACES = (
    "",
    "",
    "",
    " on https://example.com",
    " at https://example.com/",
    " from https://example.com/products/item-1",
    " on https://shop.example.com/en/",
    " at https://example.com/en-us",
    " from https://example.com/index.html",
    " on https://example.com/home",
    " https://example.com/a/b?c=1#d",
    " (https://example.com/blog/post).",
    " at https://example.com/pricing,",
    " on www.example.com",
    " at example.com",
    " on HTTP://EXAMPLE.COM/Docs",
    " from https://example.com/fr_fr/ and https://example.com/x",
)

TAILS = (
    "",
    "",
    "",
    " and click submit",
    " then log in",
    " and login first",
    " and add it to my cart",
    " and add 2 tickets to the basket",
    " and sort by price",
    " and fill out the form",
    " then check out",
    " and select the cheapest",
    " and go to the next page",
    " and open the first result",
    " then sign up for the newsletter",
    " as a list",
    "?",
    ".\nThen compile everything.",
)

FIXED = (
    "",
    " ",
    "?",
    "latest funding round for Acme",
    "compare the pricing plans of the top 5 CRM tools",
    "extract the table from https://example.com/prices",
    "log in to gamestop and add a PS5 to the cart",
    "https://example.com",
    "https://example.com/some/page",
    "WHAT IS THE CAPITAL OF FRANCE",
    "¿Cuál es la capital de Francia?",
    "東京の人口は",
    "Please, please find me all hotels in Rome",
    "I need you to help me to book a flight",
    "In what year did the Berlin Wall fall?",
    "according to the report, who won",
    "listing of apartments",
    "researchers found a new species",
    "extract the report from https://example.com/a/b",
    "find every report and click submit",
    "go to https://example.com/ and list every report then click submit",
)


def prompts(count: int = 2000, seed: int = 0) -> list[str]:
    """``count`` distinct requests, the fixed ones first, the same for the same ``count`` and ``seed``."""
    rng = random.Random(seed)
    out = dict.fromkeys(FIXED)
    while len(out) < count:
        lead = rng.choice(LEADS).replace("_", " ")
        parts = (rng.choice(OPENERS), lead, " ", rng.choice(ASKS), rng.choice(PLACES), rng.choice(TAILS))
        out.setdefault("".join(parts))
    return list(out)
