"""DeepL text translation provider."""

import logging

import requests

logger = logging.getLogger(__name__)

DEEPL_API_URL = "https://api.deepl.com/v2"
DEEPL_FREE_API_URL = "https://api-free.deepl.com/v2"

# Map locale codes to DeepL target language codes
LOCALE_TO_DEEPL = {
    "zh-Hans": "ZH-HANS",
    "zh-Hant": "ZH-HANT",
    "en": "EN-US",
    "pt": "PT-PT",
    "pt-BR": "PT-BR",
}


class DeepLTranslator:
    """Translate text using the DeepL API."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("DeepL API key is required")
        self._api_key = api_key
        self._base_url = DEEPL_FREE_API_URL if api_key.endswith(":fx") else DEEPL_API_URL

    def translate(
        self,
        texts: list[str],
        target_locale: str,
        source_lang: str = "EN",
    ) -> list[str]:
        """Translate a batch of texts to the target locale.

        Args:
            texts: List of strings to translate.
            target_locale: Target locale code (e.g., "zh-Hans").
            source_lang: Source language code (default "EN").

        Returns:
            List of translated strings in the same order as input.
        """
        if not texts:
            return []

        target_lang = LOCALE_TO_DEEPL.get(target_locale, target_locale.upper())

        resp = requests.post(
            f"{self._base_url}/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            json={
                "text": texts,
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        return [t["text"] for t in result.get("translations", [])]

    def translate_one(
        self,
        text: str,
        target_locale: str,
        source_lang: str = "EN",
    ) -> str:
        """Translate a single string."""
        results = self.translate([text], target_locale, source_lang)
        return results[0] if results else text
