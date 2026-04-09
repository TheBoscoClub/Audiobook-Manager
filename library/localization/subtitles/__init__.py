"""Subtitle generation and timestamp alignment."""

from .vtt_generator import generate_vtt, VTTCue
from .sync import align_translations

__all__ = ["generate_vtt", "VTTCue", "align_translations"]
