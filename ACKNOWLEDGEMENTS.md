# Acknowledgements

The maintainer uses the following external services and open-source tools in their personal deployment of Audiobook-Manager. None are required; they're listed here as grateful attribution. Other operators can substitute any equivalent.

## Translation

- **[DeepL](https://www.deepl.com)** — neural machine translation for UI strings, book metadata, and subtitle text. The maintainer's deployment runs on the **API Free** tier (500,000 characters/month).

## GPU / inference (operator's personal choice)

- **[RunPod](https://runpod.io)** — serverless GPU endpoints for Whisper STT and XTTS narration. Used in the maintainer's production **until 2026-08**; that account is now decommissioned and no STT backend is currently configured. The client code remains in the tree for operators who want to use it.

Audiobook-Manager's STT layer is provider-agnostic — it accepts any Whisper-compatible backend (RunPod, self-hosted `whisper-gpu`, CPU `faster-whisper`, or anything else you can reach from Python).

## Development tooling

- **[Gstack](https://github.com/garrytan/gstack)** — open-source tooling used during development.

## Third-party library licenses (GPL-family)

Audiobook-Manager depends on three GPL-family Python libraries. The project distributes exclusively as source code (GitHub) and as a user-built Docker image whose build context contains the full source. This satisfies GPL source-disclosure requirements because **the source is the distribution**.

### mutagen 1.48.1 — GPL-2.0-or-later (SPDX: `GPL-2.0-or-later`)

- **Project**: <https://github.com/quodlibet/mutagen>
- **Used by**: `library/scripts/google_play_processor.py` — reads and writes audio file tags (FLAC, MP3, Opus) for Google Play audiobook metadata extraction and cover-art embedding
- **License compliance**: `mutagen` is a direct runtime dependency. GPL-2.0-or-later permits distribution of works that use it provided source is available. Source distribution via GitHub satisfies this. A pure-Python MIT alternative (`tinytag`) was evaluated; `mutagen` was retained because `tinytag` does not support write operations (cover-art embedding requires write access to Opus container tags).

### edge-tts 7.2.8 — LGPL-3.0-or-later (SPDX: `LGPL-3.0-or-later`)

- **Project**: <https://github.com/rany2/edge-tts>
- **Used by**: `library/localization/tts/edge_tts_provider.py` — provides a Microsoft Neural TTS backend for the localization pipeline (text-to-speech synthesis)
- **License compliance**: `edge-tts` is linked as an unmodified Python library dependency. LGPL-3.0 permits use in larger works without requiring the larger work to be GPL-licensed, provided the library itself is not modified. Audiobook-Manager does not modify `edge-tts` source; the LGPL linking exception applies.

### text-unidecode 1.3 — GPL-2.0-or-later / Artistic-2.0 (SPDX: `GPL-2.0-or-later OR Artistic-2.0`)

- **Project**: <https://github.com/kmike/text-unidecode>
- **Used by**: Transitive dependency of `python-slugify`, which is a transitive dependency of `library/scripts/google_play_processor.py` (slug generation for file naming)
- **License compliance**: `text-unidecode` carries a dual GPL-2.0-or-later / Artistic-2.0 license; operators may elect the Artistic-2.0 license, which places no restrictions on use in proprietary or differently-licensed works. Under either license, source distribution via GitHub satisfies disclosure requirements.
