"""Audible enrichment provider.

Queries the Audible public catalog API using an ASIN and returns structured
metadata. Refactored from enrich_single.py Audible helpers.
"""

import json
import re
import time
import urllib.error
import urllib.request

from scripts.enrichment.base import EnrichmentProvider

# ── Audible API constants ──
AUDIBLE_API = "https://api.audible.com/1.0/catalog/products"
MARKETPLACE = "AF2M0KC94RCEA"
ALL_RESPONSE_GROUPS = ",".join(
    [
        "contributors",
        "category_ladders",
        "media",
        "product_attrs",
        "product_desc",
        "product_extended_attrs",
        "product_plan_details",
        "product_plans",
        "rating",
        "review_attrs",
        "reviews",
        "sample",
        "series",
        "sku",
        "relationships",
    ]
)

_last_call_time: float = 0.0
_RATE_LIMIT_DELAY: float = 0.3


def _rate_limit() -> None:
    """Enforce a minimum delay between Audible API calls."""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _RATE_LIMIT_DELAY:
        time.sleep(_RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _fetch_audible_product(asin: str) -> dict | None:
    """Query Audible API for full product data. Retries once on 429."""
    _rate_limit()
    url = f"{AUDIBLE_API}/{asin}?response_groups={ALL_RESPONSE_GROUPS}&marketplace={MARKETPLACE}"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(  # nosec B310 - fixed HTTPS Audible API; no user-controlled scheme
            req, timeout=15
        ) as resp:
            data = json.loads(resp.read())
            return data.get("product")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            time.sleep(5)
            try:
                # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                with urllib.request.urlopen(  # nosec B310 - fixed HTTPS Audible API; no user-controlled scheme
                    req, timeout=15
                ) as resp:
                    data = json.loads(resp.read())
                    return data.get("product")
            except Exception:
                return None
        return None
    except (urllib.error.URLError, TimeoutError):
        return None


def _parse_sequence(seq_str: str) -> float | None:
    """Parse a series sequence string into a float, e.g. '5' -> 5.0."""
    if not seq_str:
        return None
    try:
        return float(seq_str)
    except ValueError:
        m = re.search(r"[\d.]+", seq_str)
        if m:
            try:
                return float(m.group())
            except ValueError:
                pass
    return None


def _extract_categories(product: dict) -> list[dict]:
    """Extract category ladder data into a flat list of dicts."""
    categories = []
    for ladder in product.get("category_ladders", []):
        ladder_items = ladder.get("ladder", [])
        if not ladder_items:
            continue
        path_parts: list[str] = []
        for item in ladder_items:
            name = item.get("name", "")
            cat_id = item.get("id", "")
            if name:
                path_parts.append(name)
                categories.append(
                    {
                        "category_path": " > ".join(path_parts),
                        "category_name": name,
                        "root_category": path_parts[0],
                        "depth": len(path_parts),
                        "audible_category_id": cat_id,
                    }
                )
    return categories


def _extract_editorial_reviews(product: dict) -> list[dict]:
    """Extract editorial reviews into a list of {review_text, source} dicts."""
    reviews = []
    for review in product.get("editorial_reviews", []):
        text = review if isinstance(review, str) else review.get("review", "")
        source = review.get("source", "") if isinstance(review, dict) else ""
        if text:
            reviews.append({"review_text": text, "source": source})
    return reviews


def _extract_rating(product: dict) -> dict:
    """Extract overall, performance, and story ratings plus counts."""
    rating = product.get("rating", {})
    return {
        "rating_overall": rating.get("overall_distribution", {}).get("display_average_rating"),
        "rating_performance": rating.get("performance_distribution", {}).get(
            "display_average_rating"
        ),
        "rating_story": rating.get("story_distribution", {}).get("display_average_rating"),
        "num_ratings": rating.get("num_reviews"),
        "num_reviews": rating.get("overall_distribution", {}).get("num_ratings"),
    }


def _get_best_image_url(product: dict) -> str | None:
    """Return the highest-resolution product image URL available."""
    images = product.get("product_images", {})
    for size in ["2400", "1024", "500", "252"]:
        if size in images:
            return images[size]
    if images:
        return next(iter(images.values()))
    return None


_AUDIBLE_SCALAR_FIELDS = (
    "subtitle",
    "language",
    "format_type",
    "release_date",
    "publisher_summary",
    "sample_url",
    "content_type",
)


def _apply_series_from_product(result: dict, book: dict, product: dict) -> None:
    """Populate series / series_sequence when not already on the book."""
    series_list = product.get("series", [])
    if not series_list or book.get("series"):
        return
    first_series = series_list[0]
    result["series"] = first_series.get("title", "")
    seq = _parse_sequence(first_series.get("sequence", ""))
    if seq is not None:
        result["series_sequence"] = seq


def _apply_scalar_fields_from_product(result: dict, product: dict) -> None:
    """Copy simple truthy scalar fields; handle runtime + adult flag separately."""
    for field in _AUDIBLE_SCALAR_FIELDS:
        if product.get(field):
            result[field] = product[field]
    if product.get("runtime_length_min") is not None:
        result["runtime_length_min"] = product["runtime_length_min"]
    if product.get("sku"):
        result["audible_sku"] = product["sku"]
    if product.get("is_adult_product") is not None:
        result["is_adult_product"] = product["is_adult_product"]


def _apply_ratings_from_product(result: dict, product: dict) -> None:
    """Copy all non-None rating fields."""
    for key, value in _extract_rating(product).items():
        if value is not None:
            result[key] = value


def _apply_image_from_product(result: dict, product: dict) -> None:
    """Pick the best image URL if present."""
    image_url = _get_best_image_url(product)
    if image_url:
        result["audible_image_url"] = image_url


def _apply_structured_lists_from_product(result: dict, product: dict) -> None:
    """Copy categories / editorial_reviews lists when non-empty."""
    categories = _extract_categories(product)
    if categories:
        result["categories"] = categories
    editorial_reviews = _extract_editorial_reviews(product)
    if editorial_reviews:
        result["editorial_reviews"] = editorial_reviews


def _apply_author_asins_from_product(result: dict, product: dict) -> None:
    """Extract {name, asin} pairs for authors that have an ASIN."""
    authors = product.get("authors", [])
    if not authors:
        return
    author_asins = [
        {"name": a.get("name", ""), "asin": a.get("asin", "")} for a in authors if a.get("asin")
    ]
    if author_asins:
        result["author_asins"] = author_asins


def _apply_narrators_from_product(result: dict, product: dict) -> None:
    """Extract narrator flat column + junction list."""
    narrators = product.get("narrators", [])
    if not narrators:
        return
    narrator_names = [n.get("name", "") for n in narrators if n.get("name")]
    if not narrator_names:
        return
    result["narrator"] = ", ".join(narrator_names)
    result["narrator_list"] = [
        {"name": n.get("name", ""), "asin": n.get("asin", "")} for n in narrators if n.get("name")
    ]


class AudibleProvider(EnrichmentProvider):
    """Enrichment provider backed by the Audible public catalog API."""

    name = "audible"

    def can_enrich(self, book: dict) -> bool:
        """Return True if the book has a non-empty ASIN."""
        return bool(book.get("asin"))

    def enrich(self, book: dict) -> dict:
        """Return enrichment data from Audible for the given book.

        Only returns fields with actual data. Series is omitted when the book
        already has a series value (the orchestrator honours first-writer wins,
        but we enforce it here too so callers get a clean delta dict).
        """
        asin = book.get("asin", "")
        if not asin:
            return {}

        product = _fetch_audible_product(asin)
        if not product:
            return {}

        result: dict = {}
        _apply_series_from_product(result, book, product)
        _apply_scalar_fields_from_product(result, product)
        _apply_ratings_from_product(result, product)
        _apply_image_from_product(result, product)
        _apply_structured_lists_from_product(result, product)
        _apply_author_asins_from_product(result, product)
        _apply_narrators_from_product(result, product)
        return result
