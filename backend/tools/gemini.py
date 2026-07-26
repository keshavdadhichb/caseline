"""gemini — the PRESENTATION layer only. Deliberately isolated.

This module exists to make results easier for a non-specialist to take in:
a plain-language explanation, an illustration to go with it, and speech in
and out. It is a strict add-on and touches nothing in the detection path.

The boundary is the point, so it is stated plainly:
  · Anthropic plans queries and drafts SAR narratives (agent/planner.py,
    tools/sar_drafter.py). Gemini never does either.
  · Detection maths stays deterministic Python. Gemini never computes,
    adjusts or re-ranks a risk score, and never sees a decision it could
    influence.
  · Everything here degrades to a deterministic fallback when the key is
    absent or the call fails, so the demo is never blocked on it. The
    explanation falls back to a template built from the case's own real
    figures; speech falls back to the browser's built-in synthesis.

Cost is the reason for the split: these are high-volume, low-stakes UI
touches, and pointing them at the cheaper model keeps the agent's budget
for the work that actually needs the stronger one.

Requires GEMINI_API_KEY. Nothing here is required for the product to work.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

import httpx

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/interactions"

# Overridable so a model rename does not require a code change.
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.6-flash")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "gemini"
TIMEOUT_SECONDS = 30.0

# The illustration must look like it belongs in the product, so the light
# theme's own tokens are named explicitly in the prompt rather than left to
# the model's taste.
DESIGN_DIRECTION = (
    "Visual style, follow exactly: a calm, institutional editorial illustration for a "
    "bank compliance tool, LIGHT THEME ONLY. Warm off-white paper background (#F2F0EA). "
    "Shapes and line work in muted slate (#494D5F) and soft grey-blue (#6E7385), with "
    "hairline detail in pale blue-grey (#E1E7F2). ONE single accent colour used sparingly "
    "for the most important element only: muted violet (#8458B3). Optional secondary "
    "tints, used lightly: soft rose (#D98BA0) for anything risky, pale sky blue (#A0D2EB). "
    "Flat vector style, generous whitespace, thin even strokes, geometric and diagrammatic. "
    "No gradients, no drop shadows, no 3D, no glossy or neon effects, no photorealism, "
    "no stock-photo people, no clutter. No text, letters, numbers or labels anywhere in "
    "the image. Quiet, precise and professional, in the spirit of a printed financial "
    "report diagram."
)

EXPLAIN_SYSTEM = (
    "You explain anti-money-laundering findings to a smart person who does not work in "
    "compliance. Use plain language and short sentences. Explain jargon the moment you "
    "use it. Never invent a number, a threshold, a date or an account: use only what the "
    "provided JSON contains. If something is not in the JSON, do not mention it. Do not "
    "speculate about guilt; describe what the pattern is and why it was flagged. "
    "Aim for 90 to 150 words, no headings, no bullet points, no markdown."
)


class GeminiUnavailable(RuntimeError):
    """Raised when the key is missing or the API call fails."""


def is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _cache_path(kind: str, material: str) -> Path:
    key = hashlib.sha256(material.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{kind}-{key}"


def _post(payload: dict) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiUnavailable("GEMINI_API_KEY is not set")
    try:
        r = httpx.post(
            API_ROOT,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GeminiUnavailable(f"gemini request failed: {exc}") from exc
    if r.status_code != 200:
        raise GeminiUnavailable(f"gemini returned {r.status_code}: {r.text[:200]}")
    return r.json()


def _first(obj: Any, *keys: str) -> Any:
    """The interactions response nests output under a couple of shapes
    depending on modality; probe the documented ones rather than assuming."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k]:
                return obj[k]
        for v in obj.values():
            found = _first(v, *keys)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _first(v, *keys)
            if found:
                return found
    return None


# --------------------------------------------------------------------------
# explanation
# --------------------------------------------------------------------------

def explain_text(payload: dict, question: str | None = None) -> str:
    """Plain-language explanation of a case or result payload."""
    material = json.dumps({"payload": payload, "q": question}, sort_keys=True, default=str)
    cached = _cache_path("explain", material).with_suffix(".txt")
    try:
        ask = question or "Explain this finding in simple terms."
        body = _post({
            "model": TEXT_MODEL,
            "input": [{"type": "text", "text": f"{EXPLAIN_SYSTEM}\n\n{ask}\n\n{json.dumps(payload, indent=2, default=str)}"}],
        })
        text = _first(body, "output_text", "text")
        if isinstance(text, list):
            text = " ".join(str(t) for t in text)
        if not text or not str(text).strip():
            raise GeminiUnavailable("no text in gemini response")
        text = str(text).strip()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(text)
        return text
    except GeminiUnavailable:
        if cached.exists():
            return cached.read_text()
        raise


def explain_image(subject: str) -> str:
    """Illustration for an explanation, returned as a base64 PNG/JPEG."""
    material = f"{IMAGE_MODEL}|{subject}"
    cached = _cache_path("image", material).with_suffix(".b64")
    try:
        body = _post({
            "model": IMAGE_MODEL,
            "input": [{"type": "text", "text": f"{subject}\n\n{DESIGN_DIRECTION}"}],
            "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "16:9"},
        })
        data = _first(body, "output_image", "data", "inlineData")
        if isinstance(data, dict):
            data = data.get("data")
        if not data:
            raise GeminiUnavailable("no image in gemini response")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(str(data))
        return str(data)
    except GeminiUnavailable:
        if cached.exists():
            return cached.read_text()
        raise


# --------------------------------------------------------------------------
# speech
# --------------------------------------------------------------------------

def _pcm_to_wav(pcm: bytes, rate: int = 24_000, channels: int = 1, width: int = 2) -> bytes:
    """Gemini returns raw PCM; browsers want a container. Prepend a RIFF header."""
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, rate * channels * width, channels * width, width * 8
    ) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def speak(text: str) -> str:
    """Text to speech. Returns a base64 WAV the browser can play directly."""
    material = f"{TTS_MODEL}|{TTS_VOICE}|{text}"
    cached = _cache_path("tts", material).with_suffix(".b64")
    try:
        body = _post({
            "model": TTS_MODEL,
            "input": text,
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": TTS_VOICE}]},
        })
        data = _first(body, "output_audio", "data", "inlineData")
        if isinstance(data, dict):
            data = data.get("data")
        if not data:
            raise GeminiUnavailable("no audio in gemini response")
        wav = _pcm_to_wav(base64.b64decode(str(data)))
        encoded = base64.b64encode(wav).decode()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(encoded)
        return encoded
    except GeminiUnavailable:
        if cached.exists():
            return cached.read_text()
        raise


def transcribe(audio_b64: str, mime_type: str = "audio/webm") -> str:
    """Speech to text for the query bar."""
    body = _post({
        "model": TEXT_MODEL,
        "input": [
            {"type": "text", "text": "Transcribe this speech verbatim. Reply with the transcript only."},
            {"type": "audio", "data": audio_b64, "mime_type": mime_type},
        ],
    })
    text = _first(body, "output_text", "text")
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    if not text or not str(text).strip():
        raise GeminiUnavailable("no transcript in gemini response")
    return str(text).strip()
