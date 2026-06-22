"""Audio transcription via Groq's OpenAI-compatible API.

Used by the voice/audio message handler to turn Telegram voice notes into
text that is then fed into the regular agent flow.
"""

import logging
from typing import IO

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "whisper-large-v3-turbo"


class TranscriptionError(RuntimeError):
    """Raised when the upstream API returns a non-success response."""


class GroqTranscriber:
    """Thin async wrapper around Groq's `audio/transcriptions` endpoint."""

    def __init__(
        self,
        http: aiohttp.ClientSession,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 60.0,
    ) -> None:
        """Store the HTTP session, credentials, model, and request timeout."""
        self._http = http
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec

    async def transcribe(
        self,
        audio: bytes | IO[bytes],
        filename: str,
        *,
        language: str | None = None,
    ) -> str:
        """Send `audio` to Groq for transcription.

        `audio` accepts raw bytes or any binary file-like object. The file-like
        path lets the caller stream a large recording from disk without
        loading it into memory.
        """
        url = f"{self._base_url}/audio/transcriptions"
        form = aiohttp.FormData()
        form.add_field(
            "file",
            audio,
            filename=filename,
            content_type="application/octet-stream",
        )
        form.add_field("model", self._model)
        form.add_field("response_format", "json")
        if language:
            form.add_field("language", language)

        async with self._http.post(
            url,
            data=form,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=aiohttp.ClientTimeout(total=self._timeout_sec),
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise TranscriptionError(
                    f"groq transcription failed: HTTP {resp.status} {body[:500]}"
                )
            data = await resp.json()

        return (data.get("text") or "").strip()
