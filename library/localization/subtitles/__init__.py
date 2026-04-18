"""Subtitle generation and timestamp alignment."""

from .sync import align_translations
from .vtt_generator import VTTCue, generate_vtt

__all__ = ["generate_vtt", "VTTCue", "align_translations"]
