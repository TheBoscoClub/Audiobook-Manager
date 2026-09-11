"""
Localization package for audiobook translation and subtitle generation.

Provides:
- STT (speech-to-text) provider interface with Whisper backends
- Machine-translation provider interface (no backend currently configured)
- VTT subtitle generation and timestamp alignment
- TTS (text-to-speech) provider interface with edge-tts and XTTS backends
- End-to-end pipeline orchestration
"""
