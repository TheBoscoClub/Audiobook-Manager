"""Base class for enrichment providers."""

from abc import ABC, abstractmethod


class EnrichmentProvider(ABC):
    """Base class for metadata enrichment providers.

    Each provider fills fields that are currently empty/null.
    Later providers never overwrite earlier ones.
    """

    name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", ""):
            raise TypeError(f"{cls.__name__} must define a non-empty 'name' attribute")

    def __init__(self):
        if not self.name:
            raise TypeError(
                f"{type(self).__name__} must define a non-empty 'name' attribute"
            )

    @abstractmethod
    def can_enrich(self, book: dict) -> bool:
        """Return True if this provider might have data for this book."""
        ...

    @abstractmethod
    def enrich(self, book: dict) -> dict:
        """Return dict of field_name → value for fields this provider can fill.

        Only return fields that have actual data. The orchestrator handles
        merge logic (only fills empty fields).
        """
        ...
