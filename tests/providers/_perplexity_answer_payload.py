"""A Perplexity Agent API answer, trimmed from the live call made on 2026-11-01 with `user_location.country: "GB"`."""

from __future__ import annotations

from typing import Any

RESPONSE_ID = "resp_107a569e-fd55-4708-8f0b-8f386355ac50"
TEXT = (
    "In the UK, call **999** or **112** for emergency services. Both are free and connect you to "
    "police, fire, or ambulance services.[3][10]"
)


def result(result_id: int, url: str, title: str, snippet: str) -> dict[str, Any]:
    return {
        "date": "2024-03-26",
        "id": result_id,
        "last_updated": "2026-10-05",
        "snippet": snippet,
        "source": "web",
        "title": title,
        "url": url,
    }


def answered(text: str = TEXT, status: str = "completed") -> dict[str, Any]:
    return {
        "id": RESPONSE_ID,
        "object": "response",
        "status": status,
        "model": "openai/gpt-6-luna",
        "effort": "fast@2026-10-16.0",
        "output": [
            {
                "type": "search_results",
                "results": [
                    result(
                        1,
                        "https://en.wikipedia.org/wiki/Emergency_telephone_number",
                        "Emergency telephone number",
                        "An emergency telephone number is a number that allows a caller to contact local "
                        "emergency services.",
                    ),
                    result(
                        3,
                        "https://en.wikipedia.org/wiki/999_(emergency_telephone_number)",
                        "999 (emergency telephone number)",
                        "999 is the official emergency services number for the United Kingdom.",
                    ),
                    result(
                        10,
                        "https://www.eastamb.nhs.uk/your-service/999",
                        "999 | EEAST - East of England Ambulance Service",
                        "In a medical emergency you should stay calm and dial 999 or 112.",
                    ),
                ],
            },
            {
                "type": "message",
                "id": "msg_9a4f10fd-c911-46bd-b30e-5b20c2cfb8ff",
                "role": "assistant",
                "status": status,
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        ],
        "usage": {"cost": {"currency": "USD", "total_cost": 0.00128}},
    }
