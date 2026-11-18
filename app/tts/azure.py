"""Azure Speech text-to-speech over the REST API.

Uses `httpx` rather than `azure-cognitiveservices-speech` on purpose: the SDK
ships native binaries, needs specific glibc/OpenSSL versions (it breaks on
Alpine), and adds ~100MB to the image. The REST surface is two endpoints.

Deliberately narrow: this class knows how to send SSML and get bytes back, and
how to ask which styles a voice supports. Caching, rate limiting, budget
accounting and broadcasting all live in the layers above it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import httpx

__all__ = [
    "AzureSpeechClient",
    "AzureTTSError",
    "AzureRateLimited",
    "AzureTTSConfigError",
    "VoiceInfo",
    "OUTPUT_FORMATS",
]

log = logging.getLogger(__name__)

# 24kHz/48kbps mono MP3 is the sweet spot here: clean enough for speech, small
# enough that Redis storage and egress to every juror stay negligible.
OUTPUT_FORMATS = {
    "mp3-24k-48k": "audio-24khz-48kbitrate-mono-mp3",
    "mp3-24k-96k": "audio-24khz-96kbitrate-mono-mp3",
    "mp3-16k-32k": "audio-16khz-32kbitrate-mono-mp3",
    "ogg-24k": "ogg-24khz-16bit-mono-opus",
}

# Azure rejects requests without a User-Agent.
_USER_AGENT = "courtroom-mystery-tts/1.0"


class AzureTTSError(RuntimeError):
    """Synthesis failed. Caller should fall back, not retry."""


class AzureRateLimited(AzureTTSError):
    """HTTP 429. On F0 this means the 20-per-60-seconds cap was hit."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AzureTTSConfigError(AzureTTSError):
    """Missing or invalid key/region — a deploy problem, not a runtime one."""


@dataclass(frozen=True)
class VoiceInfo:
    short_name: str
    locale: str
    gender: str
    styles: frozenset[str]
    roles: frozenset[str]


class AzureSpeechClient:
    """Thin async client for the Azure Speech TTS REST endpoints."""

    def __init__(
        self,
        key: str,
        region: str,
        *,
        output_format: str = "mp3-24k-48k",
        timeout: float = 20.0,
        voice_cache_ttl: float = 6 * 3600,
        max_retries: int = 1,
    ) -> None:
        if not key or not region:
            raise AzureTTSConfigError(
                "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must both be set"
            )
        self._key = key
        self._region = region.strip().lower()
        self._format = OUTPUT_FORMATS.get(output_format, output_format)
        self._timeout = timeout
        self._max_retries = max(0, max_retries)

        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

        self._voices: dict[str, VoiceInfo] | None = None
        self._voices_fetched_at = 0.0
        self._voices_ttl = voice_cache_ttl
        self._voices_lock = asyncio.Lock()

    # ----------------------------------------------------------------- #
    # Endpoints
    # ----------------------------------------------------------------- #

    @property
    def synth_url(self) -> str:
        return (
            f"https://{self._region}.tts.speech.microsoft.com"
            "/cognitiveservices/v1"
        )

    @property
    def voices_url(self) -> str:
        return (
            f"https://{self._region}.tts.speech.microsoft.com"
            "/cognitiveservices/voices/list"
        )

    @property
    def output_format(self) -> str:
        return self._format

    @property
    def content_type(self) -> str:
        return "audio/ogg" if self._format.startswith("ogg") else "audio/mpeg"

    @property
    def file_extension(self) -> str:
        return ".ogg" if self._format.startswith("ogg") else ".mp3"

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(self._timeout),
                        headers={"User-Agent": _USER_AGENT},
                        limits=httpx.Limits(max_connections=8),
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ----------------------------------------------------------------- #
    # Synthesis
    # ----------------------------------------------------------------- #

    async def synthesize(self, ssml: str) -> bytes:
        """POST SSML, return audio bytes.

        Retries once on 429 and on 5xx (Azure returns 5xx when a voice has no
        capacity in the region, which is transient). Anything else raises
        immediately so the caller can fall back to browser speech rather than
        making the player wait.
        """
        client = await self._http()
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._format,
        }

        attempt = 0
        while True:
            try:
                response = await client.post(
                    self.synth_url,
                    content=ssml.encode("utf-8"),
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise AzureTTSError(f"transport error talking to Azure: {exc}") from exc

            if response.status_code == 200:
                audio = response.content
                if not audio:
                    raise AzureTTSError("Azure returned 200 with an empty body")
                return audio

            if response.status_code in (401, 403):
                raise AzureTTSConfigError(
                    f"Azure rejected the key for region {self._region} "
                    f"(HTTP {response.status_code}) — check AZURE_SPEECH_KEY "
                    "and that the region matches the resource"
                )

            if response.status_code == 400:
                # Almost always malformed SSML or an unsupported style on this
                # voice. Log the body: Azure's message is specific and useful.
                raise AzureTTSError(
                    "Azure rejected the SSML (HTTP 400): "
                    f"{response.text[:400]}"
                )

            if response.status_code == 429:
                retry_after = _retry_after(response)
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(retry_after or _backoff(attempt))
                    continue
                raise AzureRateLimited(
                    "Azure rate limit hit (F0 allows 20 transactions per "
                    "60 seconds and the limit is not adjustable)",
                    retry_after=retry_after,
                )

            if 500 <= response.status_code < 600:
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise AzureTTSError(
                    f"Azure server error {response.status_code}: "
                    f"{response.text[:200]}"
                )

            raise AzureTTSError(
                f"unexpected Azure response {response.status_code}: "
                f"{response.text[:200]}"
            )

    # ----------------------------------------------------------------- #
    # Voice capabilities
    # ----------------------------------------------------------------- #

    async def voices(self, *, force: bool = False) -> dict[str, VoiceInfo]:
        """All voices in the region, keyed by ShortName, with their StyleList.

        Cached in memory. Call once at startup: the SSML builder needs this to
        avoid sending a style the chosen voice doesn't support, which is a 400.
        """
        fresh = (
            self._voices is not None
            and (time.monotonic() - self._voices_fetched_at) < self._voices_ttl
        )
        if fresh and not force:
            return self._voices  # type: ignore[return-value]

        async with self._voices_lock:
            if self._voices is not None and not force:
                if (time.monotonic() - self._voices_fetched_at) < self._voices_ttl:
                    return self._voices

            client = await self._http()
            response = await client.get(
                self.voices_url,
                headers={"Ocp-Apim-Subscription-Key": self._key},
            )
            if response.status_code != 200:
                raise AzureTTSError(
                    f"voices/list returned {response.status_code}: "
                    f"{response.text[:200]}"
                )

            parsed: dict[str, VoiceInfo] = {}
            for entry in response.json():
                name = entry.get("ShortName")
                if not name:
                    continue
                parsed[name] = VoiceInfo(
                    short_name=name,
                    locale=entry.get("Locale", ""),
                    gender=entry.get("Gender", ""),
                    styles=frozenset(entry.get("StyleList") or ()),
                    roles=frozenset(entry.get("RolePlayList") or ()),
                )

            self._voices = parsed
            self._voices_fetched_at = time.monotonic()
            log.info("azure tts: cached %d voices for %s", len(parsed), self._region)
            return parsed

    async def supported_styles(self, voice: str) -> frozenset[str] | None:
        """Styles for one voice. `None` means the voice is unknown in this
        region, which the SSML builder treats as "be optimistic"."""
        try:
            info = (await self.voices()).get(voice)
        except AzureTTSError as exc:
            log.warning("azure tts: style lookup failed, proceeding blind: %s", exc)
            return None
        return info.styles if info else None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    """Short jittered backoff. Kept small — a player is waiting."""
    return min(4.0, (2**attempt) * 0.5) + random.uniform(0, 0.4)
