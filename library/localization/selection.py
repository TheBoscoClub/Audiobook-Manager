"""Workload hints for STT/TTS provider selection.

Different workloads want different providers. A 30-second UI preview
clip benefits from local (no cold-start), while a 10-hour audiobook
benefits from remote GPU throughput. Callers pass a ``WorkloadHint``
to the STT/TTS factories; the factory uses it to order the candidate
providers.
"""

from enum import Enum


class WorkloadHint(Enum):
    """Rough shape of the work a caller needs done.

    - SHORT_CLIP: short, interactive work (<30s). Prefer local to avoid
      GPU instance cold-start latency and billing minimum charges.
    - LONG_FORM: long-running batch work (chapters, full books). Prefer
      remote GPU for throughput and higher-quality models.
    - ANY: caller has no preference — factory uses its default order
      (remote-if-configured, else local).
    """

    SHORT_CLIP = "short_clip"
    LONG_FORM = "long_form"
    ANY = "any"
