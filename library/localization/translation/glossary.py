"""DeepL glossary management.

Reads the domain glossary from ``library/localization/glossary/en-zh.yaml``
and pushes it to DeepL via the ``/v2/glossaries`` endpoint. The glossary
ID is cached in the ``deepl_quota`` table (one row per install) so we
do not rebuild the glossary on every process start.

Rebuild semantics: the YAML file's content hash is also stored. If the
file has not changed since the last push, :meth:`GlossaryManager.ensure`
is a no-op. :meth:`GlossaryManager.refresh` forces a push regardless of
the hash (used by the admin endpoint).

DeepL's glossary API accepts TSV payloads — this module generates that
format on the fly from the parsed YAML, so the YAML file is the single
source of truth and contributors never have to touch TSV.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import requests

from .quota import QuotaTracker

logger = logging.getLogger(__name__)

# Match the DeepL language code convention used by deepl_translate.LOCALE_TO_DEEPL.
GLOSSARY_SOURCE_LANG = "EN"
GLOSSARY_TARGET_LANG = "ZH"  # DeepL glossaries use the base ZH code


class GlossaryError(RuntimeError):
    """Raised when a glossary operation fails unrecoverably."""


def _default_glossary_path() -> Path:
    return Path(__file__).resolve().parents[1] / "glossary" / "en-zh.yaml"


def _parse_yaml_glossary(path: Path) -> dict[str, str]:
    """Minimal YAML parser covering our glossary file format.

    We avoid pulling in PyYAML just for a flat ``key: value`` file. Any
    line that isn't a comment or blank must match ``english: chinese``.
    Quoted values are supported for keys or values containing colons.
    """
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().strip('"').strip("'")
        value = value.strip().strip('"').strip("'")
        if key and value:
            entries[key] = value
    return entries


def _entries_to_tsv(entries: dict[str, str]) -> str:
    return "\n".join(f"{k}\t{v}" for k, v in entries.items())


def _hash_entries(entries: dict[str, str]) -> str:
    payload = "\n".join(f"{k}={v}" for k, v in sorted(entries.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GlossaryManager:
    """Ensures a DeepL glossary exists and is in sync with the YAML file."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        tracker: QuotaTracker,
        glossary_path: Path | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DeepL API key is required for GlossaryManager")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._tracker = tracker
        self._path = glossary_path or _default_glossary_path()

    # -- public API ------------------------------------------------------

    def load_entries(self) -> dict[str, str]:
        return _parse_yaml_glossary(self._path)

    def ensure(self) -> str | None:
        """Return a usable glossary ID, creating one only if needed."""
        entries = self.load_entries()
        if not entries:
            return None
        current_hash = _hash_entries(entries)
        cached_id, cached_hash = self._tracker.get_glossary()
        if cached_id and cached_hash == current_hash:
            return cached_id
        return self._push(entries, current_hash)

    def refresh(self) -> str | None:
        """Force-rebuild the glossary from the YAML source."""
        entries = self.load_entries()
        if not entries:
            return None
        return self._push(entries, _hash_entries(entries))

    # -- internals -------------------------------------------------------

    def _push(self, entries: dict[str, str], source_hash: str) -> str | None:
        tsv = _entries_to_tsv(entries)
        try:
            resp = requests.post(
                f"{self._base_url}/glossaries",
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                data={
                    "name": "audiobook-library-en-zh",
                    "source_lang": GLOSSARY_SOURCE_LANG,
                    "target_lang": GLOSSARY_TARGET_LANG,
                    "entries": tsv,
                    "entries_format": "tsv",
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json() or {}
        except requests.RequestException as exc:
            logger.warning("DeepL glossary push failed: %s", exc)
            raise GlossaryError(f"glossary push failed: {exc}") from exc
        glossary_id = payload.get("glossary_id")
        if not glossary_id:
            raise GlossaryError("DeepL did not return a glossary_id")
        self._tracker.set_glossary(glossary_id, source_hash)
        logger.info("Pushed %d-entry DeepL glossary (id=%s)", len(entries), glossary_id)
        return glossary_id
