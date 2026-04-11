"""Microsoft edge-tts provider (default, free, no GPU required)."""

import asyncio
import logging
from pathlib import Path

from .base import TTSProvider, Voice

logger = logging.getLogger(__name__)

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
        """Generate audio using edge-tts.

        Args:
            text: Text to synthesize.
            language: Language code (e.g., "zh-CN").
            voice: Voice ID (e.g., "zh-CN-XiaoxiaoNeural").
            output_path: Where to write the output audio file.

        Returns:
            Path to the generated audio file.
        """
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def _synthesize():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))

        logger.info("Synthesizing %d chars via edge-tts (voice=%s)", len(text), voice)
        asyncio.run(_synthesize())
        return output_path
