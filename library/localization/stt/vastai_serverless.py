"""Vast.ai serverless Whisper speech-to-text provider (D+C hybrid).

Vast.ai serverless uses a two-step dispatch: the caller POSTs to the
routing endpoint with an endpoint name + bid cost, and the router
returns a URL pointing at a live worker. The caller then POSTs the
audio payload directly to that worker's OpenAI-compatible API.

This is fundamentally different from RunPod serverless, which uses a
``/run`` + ``/status/{job_id}`` polling pattern. Both patterns are
wrapped behind the same STTProvider contract so callers never see the
difference.
"""

import logging
from pathlib import Path

import requests

from .base import STTProvider, Transcript, WordTimestamp
from .whisper_stt import WHISPER_LANGUAGES

logger = logging.getLogger(__name__)

VAST_ROUTE_URL = "https://run.vast.ai/route/"

# Default bid cost (USD/hr equivalent) for worker routing. Higher values win
# against other tenants during spot auctions; low enough that it only matters
# under heavy load. Override via the constructor if needed.
DEFAULT_ROUTE_COST = 0.10


class VastaiServerlessSTT(STTProvider):
    """Vast.ai serverless Whisper endpoint (warm-pool or backlog).

    Args:
        api_key: Vast.ai serverless API key.
        endpoint_name: The named endpoint to route to (e.g., a
            streaming endpoint with min_workers>=1 or a backlog
            endpoint with min_workers=0).
        route_cost: Bid cost passed to the routing call.
    """

    def __init__(self, api_key: str, endpoint_name: str, route_cost: float = DEFAULT_ROUTE_COST):
        if not api_key:
            raise ValueError("Vast.ai serverless API key is required")
        if not endpoint_name:
            raise ValueError("Vast.ai serverless endpoint name is required")
        self._api_key = api_key
        self._endpoint_name = endpoint_name
        self._route_cost = route_cost

    @property
    def name(self) -> str:
        return f"vastai-serverless:{self._endpoint_name}"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        """Vast.ai is pay-per-use — no monthly cap."""
        return None

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe via Vast.ai serverless routing + worker POST."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        if not self.supports_language(language):
            raise ValueError(f"Language '{language}' not supported by Whisper")

        logger.info(
            "Transcribing %s via Vast.ai serverless (endpoint=%s, lang=%s)",
            audio_path.name,
            self._endpoint_name,
            language,
        )

        worker_url = self._route_to_worker()
        result = self._call_worker(worker_url, audio_path, language)

        words = _parse_word_timestamps(result)
        duration_ms = _extract_duration_ms(result, words)
        return Transcript(
            words=words,
            language=result.get("language", language),
            provider=f"vastai-serverless-{self._endpoint_name}",
            duration_ms=duration_ms,
        )

    def _route_to_worker(self) -> str:
        """Ask the Vast.ai router for a live worker URL."""
        resp = requests.post(
            VAST_ROUTE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"endpoint": self._endpoint_name, "cost": self._route_cost},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        worker_url = data.get("url") or data.get("worker_url") or data.get("endpoint_url")
        if not worker_url:
            raise RuntimeError(f"Vast.ai routing returned no worker URL: {data}")
        return str(worker_url).rstrip("/")

    def _call_worker(self, worker_url: str, audio_path: Path, language: str) -> dict:
        """POST the audio payload to the routed worker and return its JSON."""
        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{worker_url}/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (audio_path.name, f)},
                data={"language": language, "response_format": "verbose_json"},
                timeout=(30, 300),
            )
        resp.raise_for_status()
        return resp.json()


def _extract_raw_words(result: dict) -> list[dict]:
    """Return a flat list of word dicts, handling both response shapes.

    faster-whisper returns top-level ``words``; whisper.cpp servers
    return nested ``segments[].words[]``.
    """
    raw_words = result.get("words") or []
    if raw_words:
        return raw_words
    nested: list[dict] = []
    for seg in result.get("segments", []):
        nested.extend(seg.get("words", []))
    return nested


def _parse_word_timestamps(result: dict) -> list[WordTimestamp]:
    """Convert raw word dicts into ``WordTimestamp`` objects, dropping empties."""
    words: list[WordTimestamp] = []
    for w in _extract_raw_words(result):
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        words.append(
            WordTimestamp(
                word=text,
                start_ms=int(float(w.get("start", 0)) * 1000),
                end_ms=int(float(w.get("end", 0)) * 1000),
            )
        )
    return words


def _extract_duration_ms(result: dict, words: list[WordTimestamp]) -> int:
    """Derive duration_ms, falling back to the last word's ``end_ms``."""
    duration_ms = int(float(result.get("duration", 0)) * 1000)
    if not duration_ms and words:
        duration_ms = words[-1].end_ms
    return duration_ms
