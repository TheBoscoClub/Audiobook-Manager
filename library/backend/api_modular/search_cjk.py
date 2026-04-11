"""
CJK-aware sort and search helpers for the library grid.

Context
-------
SQLite's default text comparison orders Chinese characters by UTF-8
codepoint, which is essentially random from a user's perspective. The
library grid therefore needs:

1. A sort key derived from Mandarin pinyin (tone marks stripped,
   lowercase, joined) — implemented here via `pinyin_sort_key()`.
2. A search strategy that works when the user's query has no word
   boundaries. SQLite FTS5's `unicode61` tokenizer under-tokenizes
   CJK (it treats a whole run of Han characters as one token). The
   pragmatic fix is client-side character bigrams wrapped in a LIKE
   chain — implemented here via `cjk_bigram_like_clause()`.

Tradeoffs
---------
- Pinyin sort: we strip tones (Style.NORMAL) because the four tones
  would otherwise split otherwise-identical readings across four sort
  buckets. Tone-stripped pinyin sorts "李" / "王" / "张" as
  li / wang / zhang, which matches user expectations.
- Heteronyms (一 字 多 音) are resolved by `lazy_pinyin`'s default
  frequency-based pick. Good enough for sort.
- Bigram search: converts `"西游"` into `("%西游%",)` and a single
  LIKE; longer queries like `"西游记"` become two bigrams
  `"西游"` + `"游记"` AND-ed together. Any query of length 1 falls
  back to a single LIKE on that character.

These helpers are intentionally self-contained so unit tests can call
them without touching the database.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PYPINYIN_AVAILABLE: bool
try:
    from pypinyin import Style, lazy_pinyin  # type: ignore

    _PYPINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover — handled gracefully at runtime
    _PYPINYIN_AVAILABLE = False
    lazy_pinyin = None  # type: ignore
    Style = None  # type: ignore


def pinyin_sort_key(text: str | None) -> str | None:
    """Return a lowercase, tone-stripped pinyin join of `text`.

    Returns None if `text` is empty/None or if pypinyin is not installed
    (caller should fall back to the existing English sort column).

    Examples:
        "张三"   -> "zhangsan"
        "李四"   -> "lisi"
        "西游记" -> "xiyouji"
        "Alice" -> "alice"   (ASCII passes through lowercased)
    """
    if not text:
        return None
    if not _PYPINYIN_AVAILABLE:
        return None
    try:
        parts = lazy_pinyin(text, style=Style.NORMAL)
        key = "".join(parts).strip().lower()
        return key or None
    except Exception:  # pragma: no cover — defensive, pypinyin rarely raises
        logger.exception("pinyin_sort_key failed for text=%r", text)
        return None


def _is_cjk_char(ch: str) -> bool:
    """True if `ch` is in a CJK Unified Ideographs block we care about."""
    cp = ord(ch)
    # CJK Unified Ideographs (main block) + Extension A + Compatibility
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
    )


def contains_cjk(text: str) -> bool:
    """True if any character in `text` is a CJK ideograph."""
    return any(_is_cjk_char(c) for c in text)


def cjk_bigrams(text: str) -> list[str]:
    """Split `text` into overlapping character bigrams.

    - "西游"   -> ["西游"]
    - "西游记" -> ["西游", "游记"]
    - "三国演义" -> ["三国", "国演", "演义"]
    - "一"    -> ["一"] (single char fallback)
    - ""      -> []

    Whitespace is stripped; ASCII and punctuation are preserved as-is
    because FTS already handles them well.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) == 1:
        return [cleaned]
    return [cleaned[i:i + 2] for i in range(len(cleaned) - 1)]


def cjk_bigram_like_clause(
    column: str, query: str
) -> tuple[str, list[str]]:
    """Build a parameterized LIKE chain that matches `query` by bigram.

    Returns (sql_fragment, params). The caller AND-s the fragment into
    the WHERE clause. Every bigram must appear in `column` — this gives
    a much higher precision than a single LIKE on the full query.

    Example:
        cjk_bigram_like_clause("title", "西游记")
        -> ("(title LIKE ? AND title LIKE ?)", ["%西游%", "%游记%"])

    Empty query -> ("1=1", []) so callers can compose unconditionally.
    """
    bigrams = cjk_bigrams(query)
    if not bigrams:
        return "1=1", []
    clauses = " AND ".join(f"{column} LIKE ?" for _ in bigrams)
    params = [f"%{b}%" for b in bigrams]
    return f"({clauses})", params
