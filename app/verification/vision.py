"""Optional AI vision check using the Gemini REST API.

The model reads the stamp photo and reports what it sees (country, year, denomination). The
verification service compares that with what the seller claimed. This is a consistency check,
not proof that a physical stamp is genuine.

Any failure (no key, timeout, bad response) raises VisionUnavailable, and the caller marks the
check as skipped. Listing creation never depends on a third-party API being up.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You are helping verify a postage stamp listing on a collectors' marketplace.
Look at the image and answer ONLY with JSON matching this shape:
{"is_postage_stamp": bool, "country": str|null, "year": int|null,
 "denomination": str|null, "notes": str}
Use null when something is not visible. "notes" is one short sentence."""


class VisionUnavailable(Exception):
    """The vision check could not run. The message explains why."""


@dataclass(frozen=True)
class VisionResult:
    is_postage_stamp: bool
    country: str | None
    year: int | None
    denomination: str | None
    notes: str


def analyse_stamp_image(
    image_path: str | Path, *, api_key: str | None, model: str, timeout: float = 20
) -> VisionResult:
    if not api_key:
        raise VisionUnavailable("GEMINI_API_KEY is not configured")

    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }

    try:
        response = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
    except requests.RequestException as exc:
        raise VisionUnavailable(f"vision API request failed: {exc.__class__.__name__}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VisionUnavailable("vision API returned an unexpected response") from exc

    return _parse(data)


def _parse(data: dict) -> VisionResult:
    if not isinstance(data, dict):
        raise VisionUnavailable("vision API returned an unexpected response")
    year = data.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return VisionResult(
        is_postage_stamp=bool(data.get("is_postage_stamp", False)),
        country=data.get("country"),
        year=year,
        denomination=data.get("denomination"),
        notes=str(data.get("notes", ""))[:300],
    )
