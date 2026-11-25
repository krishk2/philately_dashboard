"""The home page chatbot. Uses Gemini when a key is configured, otherwise a canned reply."""

from __future__ import annotations

import requests
from flask import current_app

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = (
    "You are the help assistant of the National Philatelic Community website, where collectors "
    "buy, sell and pre-book Indian postage stamps and postal circles publish new issues and "
    "events. Answer questions about philately and about using the site in at most 3 short "
    "sentences. If you don't know, say so."
)

FALLBACK = (
    "I'm the community help bot. The AI assistant isn't switched on right now, but you can "
    "browse stamps in the Stamp Mela, pre-book new issues in the Stamp Store, or learn the "
    "basics in the Philately Crash Course."
)

MAX_MESSAGE = 500


def reply(message: str) -> str:
    message = (message or "").strip()[:MAX_MESSAGE]
    if not message:
        return "Please type a question."
    key = current_app.config.get("GEMINI_API_KEY")
    if not key:
        return FALLBACK
    try:
        response = requests.post(
            GEMINI_URL.format(model=current_app.config["GEMINI_MODEL"]),
            headers={"x-goog-api-key": key},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": message}]}],
                "generationConfig": {"maxOutputTokens": 200, "temperature": 0.3},
            },
            timeout=current_app.config["GEMINI_TIMEOUT"],
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        current_app.logger.warning("chatbot request failed", exc_info=True)
        return "Sorry, I couldn't reach the assistant just now. Please try again later."
