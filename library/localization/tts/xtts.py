"""Coqui XTTS v2 on RunPod — voice cloning TTS provider (upgrade path)."""

import base64
import logging
import time
from pathlib import Path

import requests

from .base import TTSProvider, Voice

logger = logging.getLogger(__name__)

RUNPOD_API_URL = "https://api.runpod.ai/v2"


class XTTSProvider(TTSProvider):
    """Coqui XTTS v2 deployed on RunPod serverless — GPU-intensive voice cloning."""

    def __init__(self, api_key: str, endpoint_id: str):
        if not api_key:
            raise ValueError("RunPod API key is required")
        if not endpoint_id:
            raise ValueError("RunPod XTTS endpoint ID is required")
        self._api_key = api_key
        self._endpoint_id = endpoint_id

    @property
    def name(self) -> str:
        return "xtts"

    def requires_gpu(self) -> bool:
        return True

    def available_voices(self, language: str) -> list[Voice]:
        """XTTS uses voice cloning — voices are dynamically created from reference audio."""
        return [Voice(id="clone", name="Voice Clone", language=language, gender="neutral")]

    def synthesize(self, text: str, language: str, voice: str, output_path: Path) -> Path:
        """Generate audio via RunPod XTTS endpoint.

        Args:
            text: Text to synthesize.
            language: Target language code.
            voice: Path to reference audio for voice cloning, or "default".
            output_path: Where to write the output audio.

        Returns:
            Path to the generated audio file.
        """
        logger.info("Synthesizing %d chars via XTTS (lang=%s)", len(text), language)

        payload = {
            "input": {
                "text": text,
                "language": language,
            }
        }

        # If voice is a file path, include reference audio for cloning
        ref_path = Path(voice)
        if ref_path.exists():
            payload["input"]["speaker_wav_b64"] = base64.b64encode(
                ref_path.read_bytes()
            ).decode()

        run_url = f"{RUNPOD_API_URL}/{self._endpoint_id}/run"
        resp = requests.post(
            run_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        job_id = resp.json()["id"]

        # Poll for completion
        result = self._poll_job(job_id)

        # Decode audio from base64 response
        audio_b64 = result.get("audio_b64", "")
        if not audio_b64:
            raise RuntimeError("XTTS returned no audio data")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(audio_b64))
        return output_path

    def _poll_job(self, job_id: str, max_wait: int = 600) -> dict:
        """Poll a RunPod job until completion or timeout."""
        status_url = f"{RUNPOD_API_URL}/{self._endpoint_id}/status/{job_id}"
        start = time.monotonic()
        poll_interval = 2

        while time.monotonic() - start < max_wait:
            resp = requests.get(
                status_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            if status == "COMPLETED":
                return data.get("output", {})
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"RunPod XTTS job {status}: {data.get('error', 'unknown')}")

            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 10)

        raise TimeoutError(f"RunPod XTTS job did not complete within {max_wait}s")
