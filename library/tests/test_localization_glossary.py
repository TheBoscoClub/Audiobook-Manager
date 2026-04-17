"""Tests for the DeepL glossary manager.

Covers ``localization/translation/glossary.py`` — YAML parsing, hash caching,
TSV generation, and HTTP push behaviour. The real DeepL API is never called
(``requests.post`` is fully mocked).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest
import requests

from localization.translation.glossary import (
    GLOSSARY_SOURCE_LANG,
    GLOSSARY_TARGET_LANG,
    GlossaryError,
    GlossaryManager,
    _entries_to_tsv,
    _hash_entries,
    _parse_yaml_glossary,
)
from localization.translation.quota import QuotaTracker


# --- YAML parsing ------------------------------------------------------------


class TestParseYamlGlossary:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _parse_yaml_glossary(tmp_path / "nope.yaml") == {}

    def test_parses_key_value_lines(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "g.yaml"
        yaml_file.write_text(
            "# comment line\n\naudiobook: 有声书\nseries: \"系列\"\nauthor: '作者'\n",
            encoding="utf-8",
        )
        entries = _parse_yaml_glossary(yaml_file)
        assert entries == {
            "audiobook": "有声书",
            "series": "系列",
            "author": "作者",
        }

    def test_ignores_comments_and_blank_and_invalid(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "g.yaml"
        yaml_file.write_text(
            "# header comment\n"
            "\n"
            "noColonHere\n"
            "valid: ok\n"
            ": novalue\n"  # empty key
            "emptyvalue:\n"  # empty value
            "  # indented comment\n",
            encoding="utf-8",
        )
        entries = _parse_yaml_glossary(yaml_file)
        assert entries == {"valid": "ok"}


# --- TSV + hash helpers ------------------------------------------------------


class TestEntriesHelpers:
    def test_entries_to_tsv_uses_tab_separator(self) -> None:
        tsv = _entries_to_tsv({"book": "书", "series": "系列"})
        assert tsv == "book\t书\nseries\t系列"

    def test_hash_is_stable_and_order_independent(self) -> None:
        h1 = _hash_entries({"a": "1", "b": "2"})
        h2 = _hash_entries({"b": "2", "a": "1"})
        assert h1 == h2
        # And it's a real SHA-256 hex digest.
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_hash_differs_when_content_differs(self) -> None:
        assert _hash_entries({"a": "1"}) != _hash_entries({"a": "2"})


# --- GlossaryManager ---------------------------------------------------------


class _StubTracker:
    """In-memory stand-in for ``QuotaTracker``'s glossary accessors."""

    def __init__(
        self, cached_id: str | None = None, cached_hash: str | None = None
    ) -> None:
        self._id = cached_id
        self._hash = cached_hash
        self.set_calls: list[tuple[str, str]] = []

    def get_glossary(self) -> tuple[str | None, str | None]:
        return self._id, self._hash

    def set_glossary(self, glossary_id: str, source_hash: str) -> None:
        self._id = glossary_id
        self._hash = source_hash
        self.set_calls.append((glossary_id, source_hash))


def _as_tracker(stub: _StubTracker) -> QuotaTracker:
    """Cast the duck-typed stub back to the real type for mypy."""
    return cast(QuotaTracker, stub)


def _write_glossary(path: Path, entries: dict[str, str]) -> Path:
    path.write_text(
        "\n".join(f"{k}: {v}" for k, v in entries.items()) + "\n",
        encoding="utf-8",
    )
    return path


class TestGlossaryManagerConstruction:
    def test_requires_api_key(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="API key"):
            GlossaryManager(
                api_key="",
                base_url="https://api.deepl.com/v2",
                tracker=_as_tracker(_StubTracker()),
                glossary_path=tmp_path / "missing.yaml",
            )

    def test_strips_trailing_slash_from_base_url(self, tmp_path: Path) -> None:
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2/",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=tmp_path / "missing.yaml",
        )
        # internal attribute inspection — safer than hitting the network
        assert gm._base_url == "https://api.deepl.com/v2"


class TestGlossaryManagerLoad:
    def test_load_entries_reads_yaml(self, tmp_path: Path) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=yaml_file,
        )
        assert gm.load_entries() == {"book": "书"}


class TestGlossaryManagerEnsure:
    def test_no_entries_returns_none(self, tmp_path: Path) -> None:
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=tmp_path / "empty.yaml",  # missing file
        )
        assert gm.ensure() is None

    def test_cache_hit_skips_push(self, tmp_path: Path, requests_mock) -> None:
        entries = {"book": "书", "series": "系列"}
        yaml_file = _write_glossary(tmp_path / "g.yaml", entries)
        cached_hash = hashlib.sha256(
            "\n".join(f"{k}={v}" for k, v in sorted(entries.items())).encode("utf-8")
        ).hexdigest()
        tracker = _StubTracker(cached_id="gid-cached", cached_hash=cached_hash)
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(tracker),
            glossary_path=yaml_file,
        )
        glossary_id = gm.ensure()
        assert glossary_id == "gid-cached"
        assert tracker.set_calls == []
        assert not requests_mock.called

    def test_cache_miss_pushes_and_caches(self, tmp_path: Path, requests_mock) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        tracker = _StubTracker()  # nothing cached
        captured: dict = {}

        def _record(request, context) -> dict:
            captured["headers"] = dict(request.headers)
            captured["body"] = request.text
            return {"glossary_id": "new-gid"}

        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            json=_record,
        )
        gm = GlossaryManager(
            api_key="sekret",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(tracker),
            glossary_path=yaml_file,
        )
        assert gm.ensure() == "new-gid"
        assert tracker.set_calls and tracker.set_calls[0][0] == "new-gid"
        # Auth header must carry DeepL-Auth-Key token.
        assert captured["headers"]["Authorization"] == "DeepL-Auth-Key sekret"
        # Source/target lang constants must be passed verbatim.
        assert GLOSSARY_SOURCE_LANG in captured["body"]
        assert GLOSSARY_TARGET_LANG in captured["body"]

    def test_hash_drift_triggers_rebuild(self, tmp_path: Path, requests_mock) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        tracker = _StubTracker(cached_id="old-gid", cached_hash="stale")
        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            json={"glossary_id": "rebuilt-gid"},
        )
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(tracker),
            glossary_path=yaml_file,
        )
        assert gm.ensure() == "rebuilt-gid"


class TestGlossaryManagerRefresh:
    def test_refresh_no_entries_returns_none(self, tmp_path: Path) -> None:
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=tmp_path / "missing.yaml",
        )
        assert gm.refresh() is None

    def test_refresh_pushes_even_when_cache_matches(
        self, tmp_path: Path, requests_mock
    ) -> None:
        entries = {"book": "书"}
        yaml_file = _write_glossary(tmp_path / "g.yaml", entries)
        cached_hash = _hash_entries(entries)
        tracker = _StubTracker(cached_id="cached-gid", cached_hash=cached_hash)
        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            json={"glossary_id": "forced-gid"},
        )
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(tracker),
            glossary_path=yaml_file,
        )
        assert gm.refresh() == "forced-gid"
        assert tracker.set_calls == [("forced-gid", cached_hash)]


class TestGlossaryManagerPushErrors:
    def test_http_error_raises_glossary_error(
        self, tmp_path: Path, requests_mock
    ) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            status_code=500,
            text="internal error",
        )
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=yaml_file,
        )
        with pytest.raises(GlossaryError, match="glossary push failed"):
            gm.ensure()

    def test_connection_error_raises_glossary_error(
        self, tmp_path: Path, requests_mock
    ) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            exc=requests.ConnectionError("net down"),
        )
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=yaml_file,
        )
        with pytest.raises(GlossaryError):
            gm.ensure()

    def test_missing_glossary_id_raises(self, tmp_path: Path, requests_mock) -> None:
        yaml_file = _write_glossary(tmp_path / "g.yaml", {"book": "书"})
        requests_mock.post(
            "https://api.deepl.com/v2/glossaries",
            json={"unexpected": "shape"},
        )
        gm = GlossaryManager(
            api_key="x",
            base_url="https://api.deepl.com/v2",
            tracker=_as_tracker(_StubTracker()),
            glossary_path=yaml_file,
        )
        with pytest.raises(GlossaryError, match="glossary_id"):
            gm.ensure()
