"""Microsoft edge-tts provider (default, free, no GPU required).

edge-tts uses asyncio internally, which deadlocks inside gunicorn's
gevent worker (monkey-patched threading + asyncio event loop conflict).
Synthesis runs as a subprocess via the edge-tts CLI to isolate it.
"""

import logging
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import sys
import tempfile
from pathlib import Path

from .base import TTSProvider, Voice

logger = logging.getLogger(__name__)

_TTS_TIMEOUT = 600  # 10 minutes max per synthesis call

# Curated voice list for supported languages
EDGE_VOICES = {
    "zh": [
        Voice(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", language="zh-CN", gender="female"),
        Voice(id="zh-CN-YunyangNeural", name="Yunyang", language="zh-CN", gender="male"),
        Voice(id="zh-CN-XiaoyiNeural", name="Xiaoyi", language="zh-CN", gender="female"),
        Voice(id="zh-CN-YunjianNeural", name="Yunjian", language="zh-CN", gender="male"),
    ],
    "en": [
        Voice(id="en-US-JennyNeural", name="Jenny", language="en-US", gender="female"),
        Voice(id="en-US-GuyNeural", name="Guy", language="en-US", gender="male"),
    ],
}


class EdgeTTSProvider(TTSProvider):
    """Microsoft edge-tts — free, no GPU, near-instant generation."""

    is_local = True

    @property
    def name(self) -> str:
        return "edge-tts"

    def requires_gpu(self) -> bool:
        return False

    def available_voices(self, language: str) -> list[Voice]:
        lang_prefix = language.split("-")[0].lower()
        return EDGE_VOICES.get(lang_prefix, [])

    def synthesize(self, text: str, language: str, voice: str, output_path: Path) -> Path:
        """Generate audio using edge-tts CLI subprocess.

        Runs as a separate process to avoid gevent/asyncio deadlocks.
        For long texts, writes to a temp file and passes --file.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Synthesizing %d chars via edge-tts (voice=%s)", len(text), voice)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(text)
            text_path = tf.name

        try:
            result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    voice,
                    "--file",
                    text_path,
                    "--write-media",
                    str(output_path),
                ],
                capture_output=True,
                # `errors="replace"` — edge-tts can occasionally emit non-UTF-8
                # bytes in its stderr (e.g. from underlying aiohttp / azure
                # SDK warnings); strict decoding would raise UnicodeDecodeError
                # before we even see the real exit code. Replacement keeps
                # the failure message readable while preventing a confusing
                # decode-time crash that masks the actual TTS issue.
                encoding="utf-8",
                errors="replace",
                timeout=_TTS_TIMEOUT,
            )
        finally:
            Path(text_path).unlink(missing_ok=True)

        if result.returncode != 0:
            raise RuntimeError(f"edge-tts failed (exit {result.returncode}): {result.stderr[:300]}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"edge-tts produced empty output at {output_path}")

        return output_path
