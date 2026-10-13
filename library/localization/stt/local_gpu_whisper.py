"""Local GPU Whisper STT provider.

Calls the host's whisper-gpu.service over HTTP. The service runs on the
host where the local GPU lives (NVIDIA + CUDA or enterprise AMD + ROCm —
see docs/MULTI-LANGUAGE-SETUP.md for supported hardware); this provider
can run anywhere (VM, container, or host) as long as it can reach the
service endpoint.

Architecturally identical to the RunPod provider but with zero latency
and no billing — the GPU is local.
"""

import logging
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests
from common_utils.secret_resolver import resolve_secret

from .base import STTProvider, Transcript, WordTimestamp

logger = logging.getLogger(__name__)

# Endpoints already warned about (cleartext bearer). The pipeline builds a new
# provider per selection and may run from request threads, so warn once per
# process per redacted endpoint, under a lock.
_warned_cleartext: set[str] = set()
_warned_lock = threading.Lock()

WHISPER_LANGUAGES = {
    "en",
    "zh",
    "de",
    "es",
    "ru",
    "ko",
    "fr",
    "ja",
    "pt",
    "tr",
    "pl",
    "ca",
    "nl",
    "ar",
    "sv",
    "it",
    "id",
    "hi",
    "fi",
    "vi",
    "he",
    "uk",
    "el",
    "ms",
    "cs",
    "ro",
    "da",
    "hu",
    "ta",
    "no",
    "th",
    "ur",
    "hr",
    "bg",
    "lt",
    "la",
    "mi",
    "ml",
    "cy",
    "sk",
    "te",
    "fa",
    "lv",
    "bn",
    "sr",
    "az",
    "sl",
    "kn",
    "et",
    "mk",
}


def redact_endpoint(url: str) -> str:
    """Return ``scheme://host[:port]`` only — safe for logs and exceptions.

    Drops userinfo, path, query and fragment so a credential embedded in an
    operator-supplied URL can never reach a log line. Unparseable input
    yields a fixed placeholder, never the raw text.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "<invalid-endpoint>"
    if not host or not parts.scheme:
        return "<invalid-endpoint>"
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


class LocalGPUWhisperSTT(STTProvider):
    """Local GPU Whisper via the host's whisper-gpu service."""

    def __init__(
        self,
        host: str = "",
        port: int = 8765,
        base_url: str = "",
        token: str | None = None,
    ):
        """Build the client.

        ``base_url`` (full URL incl. scheme, http or https) overrides
        host/port. ``token`` is sent as ``Authorization: Bearer``; when
        ``None`` it is resolved from ``AUDIOBOOKS_WHISPER_GPU_TOKEN`` or
        ``AUDIOBOOKS_WHISPER_GPU_TOKEN_FILE``. With neither set the
        behaviour is plain ``http://host:port`` with no auth header.
        """
        base_url = (base_url or "").strip()
        if base_url:
            try:
                parts = urlsplit(base_url)
                _ = parts.port  # raises ValueError on a malformed port
            except ValueError:
                raise ValueError("AUDIOBOOKS_WHISPER_GPU_URL is not a valid URL") from None
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError(
                    "AUDIOBOOKS_WHISPER_GPU_URL must be an http:// or https:// "
                    f"URL with a host (got scheme {parts.scheme!r})"
                )
            if parts.query or parts.fragment:
                raise ValueError(
                    "AUDIOBOOKS_WHISPER_GPU_URL must be a base URL without a "
                    "query string or fragment"
                )
            if parts.username is not None or parts.password is not None or "@" in parts.netloc:
                raise ValueError(
                    "AUDIOBOOKS_WHISPER_GPU_URL must not contain a username or "
                    "password — use AUDIOBOOKS_WHISPER_GPU_TOKEN instead"
                )
            self._base_url = base_url.rstrip("/")
            scheme = parts.scheme
        elif host:
            self._base_url = f"http://{host}:{port}"
            scheme = "http"
        else:
            raise ValueError(
                "LocalGPUWhisperSTT requires a host — set "
                "AUDIOBOOKS_WHISPER_GPU_HOST to the reachable address of "
                "the whisper-gpu service (or AUDIOBOOKS_WHISPER_GPU_URL)."
            )
        if token is None:
            token = resolve_secret("AUDIOBOOKS_WHISPER_GPU_TOKEN")
        if token and scheme == "http":
            ep = redact_endpoint(self._base_url)
            with _warned_lock:
                first = ep not in _warned_cleartext
                _warned_cleartext.add(ep)
            if first:
                logger.warning(
                    "Whisper GPU bearer token is configured but the endpoint %s is "
                    "plain http — the token travels unencrypted unless this hop is "
                    "an SSH tunnel or other encrypted link",
                    ep,
                )
        # Never log or repr this value.
        self._auth_kwargs: dict = {"headers": {"Authorization": f"Bearer {token}"}} if token else {}

    @property
    def endpoint(self) -> str:
        """Redacted ``scheme://host[:port]`` for logs and messages."""
        return redact_endpoint(self._base_url)

    @property
    def name(self) -> str:
        return "local-gpu-whisper"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        return None

    def is_available(self) -> bool:
        """Check if the whisper-gpu service is running and reachable."""
        try:
            resp = requests.get(f"{self._base_url}/health", timeout=3, **self._auth_kwargs)
            return resp.ok and resp.json().get("status") == "ok"
        except (requests.ConnectionError, requests.Timeout):  # fmt: skip
            return False

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Transcribing %s via local GPU Whisper (lang=%s)", audio_path.name, language)

        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self._base_url}/transcribe",
                files={"file": (audio_path.name, f)},
                data={"language": language},
                timeout=1800,
                **self._auth_kwargs,
            )

        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            raise RuntimeError(f"Whisper GPU service error: {result['error']}")

        words = []
        for w in result.get("words", []):
            words.append(
                WordTimestamp(
                    word=w.get("word", "").strip(),
                    start_ms=int(w.get("start", 0) * 1000),
                    end_ms=int(w.get("end", 0) * 1000),
                )
            )

        duration_ms = int(result.get("duration", 0) * 1000)
        if not duration_ms and words:
            duration_ms = words[-1].end_ms

        logger.info(
            "Local GPU transcription complete: %d words, %.1fs (wall: %.1fs)",
            len(words),
            duration_ms / 1000,
            result.get("elapsed_seconds", 0),
        )

        return Transcript(
            words=words,
            language=result.get("language", language),
            provider="local-gpu-whisper-large-v3",
            duration_ms=duration_ms,
        )
