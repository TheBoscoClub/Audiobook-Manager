"""
Localization package for audiobook translation and subtitle generation.

Provides:
- STT (speech-to-text) provider interface with DeepL and Whisper backends
- Text translation via DeepL
- VTT subtitle generation and timestamp alignment
- TTS (text-to-speech) provider interface with edge-tts and XTTS backends
- Book metadata lookup (Douban, DeepL fallback)
- End-to-end pipeline orchestration
"""
