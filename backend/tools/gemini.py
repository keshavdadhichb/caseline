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


def _content_parts(body: dict) -> list[dict]:
    """The live /interactions response puts the answer in `steps`, not the
    `output` field the docs describe: steps is a list where the last entry
    of type "model_output" carries `content`, a list of typed parts. The
    earlier "thought" step also carries a large `signature` string, which a
    naive "find the longest string" probe would return instead of the
    image, so parts are read by position and type rather than by search."""
    parts: list[dict] = []
    for step in body.get("steps") or []:
        if step.get("type") == "model_output":
            parts.extend(step.get("content") or [])
    return parts


def _text_of(body: dict) -> str | None:
    chunks = [p.get("text", "") for p in _content_parts(body) if p.get("type") == "text"]
    joined = " ".join(c for c in chunks if c).strip()
    return joined or None


def _image_of(body: dict) -> str | None:
    for p in _content_parts(body):
        if p.get("data") and p.get("type") in (None, "image", "output_image"):
            return str(p["data"])
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
        text = _text_of(body)
        if not text:
            raise GeminiUnavailable("no text in gemini response")
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
        data = _image_of(body)
        if not data:
            raise GeminiUnavailable("no image in gemini response")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(data)
        return data
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
        data = _image_of(body)  # audio arrives in the same typed-part slot
        if not data:
            raise GeminiUnavailable("no audio in gemini response")
        wav = _pcm_to_wav(base64.b64decode(data))
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
    text = _text_of(body)
    if not text:
        raise GeminiUnavailable("no transcript in gemini response")
    return text


# --------------------------------------------------------------------------
# conversational
# --------------------------------------------------------------------------

CHAT_SYSTEM = (
    "You are the assistant inside Caseline, a tool that finds money-laundering "
    "patterns in bank transaction data. You are handling a message that is NOT a "
    "detection query: a greeting, a thank-you, a question about how the product "
    "works, or general anti-money-laundering background.\n\n"
    "Rules:\n"
    "- Be brief and warm. One or two short paragraphs, no headings, no markdown.\n"
    "- NEVER state a finding, a count, a risk score or an account id. You have not "
    "run any analysis and must not imply that you have.\n"
    "- If the user seems to want an actual analysis, say what they could ask "
    "instead, for example 'Find structuring patterns in the last 30 days'.\n"
    "- You may explain AML concepts generally, but if asked for Caseline's exact "
    "thresholds, say they are shown in the About panel rather than guessing."
)


def chat(message: str, context: str | None = None) -> str:
    """Answer a non-detection message. Deliberately has no access to results:
    it cannot report a finding, only converse and point at the real query."""
    prompt = CHAT_SYSTEM + f"\n\nUser message: {message}"
    if context:
        prompt += f"\n\nProduct facts you may use verbatim:\n{context}"
    body = _post({"model": TEXT_MODEL, "input": [{"type": "text", "text": prompt}]})
    text = _text_of(body)
    if not text:
        raise GeminiUnavailable("no chat text in gemini response")
    return text
