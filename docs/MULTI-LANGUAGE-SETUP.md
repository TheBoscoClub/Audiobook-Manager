# Multi-Language Setup and Installation Guide

A comprehensive guide for adding multi-language support to your Audiobook Manager installation. This document covers architecture, provider configuration, cost expectations, and step-by-step instructions for adding new languages.

---

## Table of Contents

- [Overview and Scope](#overview-and-scope)
- [Architecture Overview](#architecture-overview)
- [Provider Setup Instructions](#provider-setup-instructions)
  - [DeepL (Translation)](#deepl-translation)
  - [Vast.ai (GPU for Whisper STT and XTTS TTS)](#vastai-gpu-for-whisper-stt-and-xtts-tts)
  - [RunPod (Serverless GPU)](#runpod-serverless-gpu)
  - [Local GPU (Optional)](#local-gpu-optional)
- [Configuration Reference](#configuration-reference)
- [Adding a New Language](#adding-a-new-language)
- [Cost and Time Investment](#cost-and-time-investment)
- [Translation Asset Portability](#translation-asset-portability)
- [Dependencies and Requirements](#dependencies-and-requirements)
- [Attribution](#attribution)
- [Troubleshooting](#troubleshooting)

---

## Overview and Scope

Audiobook Manager (v8.2.1) includes a full localization system that translates both the web interface and audiobook content itself. The system currently ships with English (`en`) and Simplified Chinese (`zh-Hans`), but the architecture supports adding more locales without code changes.

### What Is Translated

| Category | Examples | Mechanism |
|----------|----------|-----------|
| UI text | Navigation, buttons, labels, headings | Locale JSON files (1,038 keys per language) |
| Tooltips | All interactive elements | Locale JSON files |
| Book descriptions | Synopses, author bios, series info | DeepL API (neural machine translation) |
| Announcement banners | Admin-authored notices shown to patrons | DeepL API |
| Help pages | User-facing documentation | Locale JSON files |
| Error messages | User-visible errors and validation | Locale JSON files |
| Subtitles (VTT) | Per-chapter synchronized captions | STT pipeline (Whisper transcription + DeepL translation) |
| Translated audio narration | Full audiobook narration in target language | TTS pipeline (edge-tts or XTTS voice cloning) |

### What Is NOT Translated

| Category | Reason |
|----------|--------|
| Admin/backoffice UI (`utilities.html`) | Used exclusively by the system administrator. Translating admin-only pages adds maintenance burden with zero patron benefit -- no library user ever sees the backoffice. This is a deliberate design choice: 100% of user-facing content is translated, while the admin tools that only the operator uses remain in English. |
| System logs | Machine-readable, consumed by operators and log aggregators |
| API JSON responses | Structured data consumed by code, not end users |
| CLI output | Used only by the administrator at the terminal |
| Internal error messages | Developer-facing diagnostics in logs and stack traces |

### Current Language Support

| Locale Code | Language | Status |
|-------------|----------|--------|
| `en` | English | Default, complete |
| `zh-Hans` | Simplified Chinese | Complete (1,038 UI keys + book metadata + CJK search/sort) |

The architecture supports any BCP 47 locale code. Adding a new language requires no code changes -- only a locale file and configuration.

---

## Architecture Overview

The localization system is a three-stage pipeline that converts English audiobook content into translated text and narrated audio:

```
Source Audio (English)
    |
    v
[STT] Speech-to-Text (Whisper)
    |  Transcribes English audio to timestamped text
    v
[Translation] DeepL API
    |  Translates English text to target language
    v
[TTS] Text-to-Speech (edge-tts or XTTS)
    |  Synthesizes translated text into narrated audio
    v
Translated Audio + VTT Subtitles
```

### Pipeline Components

The localization module lives in `library/localization/` (~3,900 lines of Python across 32 files) and is organized into subpackages:

| Subpackage | Purpose |
|------------|---------|
| `stt/` | Speech-to-text providers (Whisper via Vast.ai, RunPod, local GPU, local CPU) |
| `translation/` | DeepL translation, glossary management, quota tracking |
| `tts/` | Text-to-speech providers (edge-tts, XTTS via Vast.ai/RunPod) |
| `subtitles/` | VTT subtitle generation and chapter synchronization |
| `metadata/` | Book metadata translation (title, author, description) and Douban lookup |
| `glossary/` | Domain-specific translation glossaries for consistency |

### STT Providers (Speech-to-Text)

The STT layer uses OpenAI's Whisper model. When `AUDIOBOOKS_STT_PROVIDER` is set to `auto` (the default), the system selects a provider based on workload characteristics:

| Provider | Best For | Tradeoffs |
|----------|----------|-----------|
| **Vast.ai Whisper** | Long-form audiobook transcription | Most reliable throughput. Dedicated GPU instances. Requires manual instance management. |
| **RunPod Whisper** | Burst workloads, occasional use | Serverless (scales to zero, pay only when processing). Can be resource-constrained under heavy load. |
| **Local GPU Whisper** | Testing, small batches | Uses host GPU (NVIDIA CUDA or AMD ROCm). Risk of system instability under heavy transcription loads. |
| **Local CPU Whisper** | Fallback only | Always available, no external dependencies. Very slow -- unsuitable for full library transcription. |

The workload-aware selection system (`library/localization/selection.py`) distinguishes between short clips (prefer local to avoid cold-start latency) and long-form batch work (prefer remote GPU for throughput).

### Translation Provider

**DeepL API** is used for all text translation. DeepL consistently produces the most natural Chinese renderings compared to alternatives. The translation layer includes:

- Quota tracking to stay within API limits
- Glossary support for domain-specific terms (proper nouns, series names)
- Batch processing to minimize API calls

### TTS Providers (Text-to-Speech)

| Provider | Quality | Cost | Use Case |
|----------|---------|------|----------|
| **edge-tts** (default) | High -- Microsoft Neural TTS voices | Free, no API key | Standard narration. Excellent quality for most languages. |
| **XTTS (Coqui)** | Highest -- preserves original narrator voice | GPU rental ($0.20-0.50/hr) | Voice cloning. Reproduces the original narrator's characteristics in the target language. Requires GPU (Vast.ai or RunPod). |

### Fallback System

If a remote provider (Vast.ai, RunPod) is unreachable, the system automatically falls back to a local provider for the current request. Local provider failures are not retried -- the error is real and propagates to the caller. This means a misconfigured remote provider degrades gracefully rather than blocking the entire pipeline.

---

## Provider Setup Instructions

### DeepL (Translation)

DeepL handles all text translation (UI strings, book metadata, subtitle text).

1. **Sign up** at [deepl.com/pro](https://www.deepl.com/pro) and obtain an API authentication key.

2. **Choose a plan**:

   | Plan | Character Limit | Cost | Notes |
   |------|----------------|------|-------|
   | Free | 500,000 chars/month | $0 | Sufficient for most single-library installations |
   | Pro | Unlimited (pay-per-use) | ~$20/million chars | Required for large libraries or continuous translation |

3. **Configure the API key**:

   Add to `~/.config/api-keys.env`:
   ```bash
   # DeepL — translation API key for audiobook localization
   AUDIOBOOKS_DEEPL_API_KEY=your-key-here
   ```

4. **Verify**: The localization pipeline will automatically use DeepL when the key is present. No additional configuration is needed.

**Note**: DeepL also offers an STT service, but it is NOT recommended for audiobooks because it rejects audio files larger than 100 MB. Most audiobook chapters exceed this limit.

### Vast.ai (GPU for Whisper STT and XTTS TTS)

Vast.ai is a peer-to-peer GPU marketplace where you rent dedicated GPU instances. It provides the most reliable throughput for long-form audiobook transcription and voice-cloned narration.

1. **Sign up** at [vast.ai](https://vast.ai/) and add credits ($5-10 minimum to start).

2. **Find and rent a GPU instance**:
   - Search the marketplace for instances with a Whisper-compatible Docker image
   - Look for GPUs with at least 8 GB VRAM (RTX 3090, A100, A40, etc.)
   - Typical cost: $0.20-0.50/hour depending on GPU model

3. **Configure Whisper (STT)**:

   Once your instance is running, note its IP address and port, then add to `~/.config/api-keys.env` or `/etc/audiobooks/audiobooks.conf`:
   ```bash
   # Vast.ai — Whisper STT GPU instance
   AUDIOBOOKS_VASTAI_WHISPER_HOST=203.0.113.42
   AUDIOBOOKS_VASTAI_WHISPER_PORT=8000
   ```

4. **Configure XTTS (TTS, optional)**:

   If you want voice-cloned narration (preserves the original narrator's voice), deploy an XTTS instance:
   ```bash
   # Vast.ai — XTTS voice cloning GPU instance
   AUDIOBOOKS_VASTAI_XTTS_HOST=203.0.113.42
   AUDIOBOOKS_VASTAI_XTTS_PORT=8020
   ```

5. **IMPORTANT: Shut down instances when not in use.** GPU rental is per-hour. An idle RTX 3090 at $0.30/hour costs $7.20/day or $216/month if left running. Start instances before a translation batch, shut them down when done.

### RunPod (Serverless GPU)

RunPod offers serverless GPU endpoints that scale to zero -- you only pay for actual compute time, with no idle charges. Good for burst or occasional workloads.

1. **Sign up** at [runpod.io](https://www.runpod.io/) and add credits.

2. **Deploy a Whisper serverless endpoint** from the RunPod template marketplace.

3. **Configure**:

   ```bash
   # RunPod — API key and serverless endpoint IDs
   AUDIOBOOKS_RUNPOD_API_KEY=your-runpod-api-key
   AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT=your-whisper-endpoint-id
   ```

4. **Optional XTTS endpoint** (for voice-cloned narration):

   ```bash
   AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT=your-xtts-endpoint-id
   ```

5. **Cost**: Approximately $0.00026/second of GPU time. Serverless cold starts add 10-30 seconds of latency on the first request after a period of inactivity.

### Local GPU (Optional)

If your host machine has an NVIDIA (CUDA) or AMD (ROCm) GPU, you can run Whisper locally. This is useful for testing but can strain host resources during heavy transcription.

1. **Start a local Whisper service** on your host (details depend on your GPU and framework).

2. **Configure**:

   ```bash
   # Local GPU — Whisper service on the host (e.g., over libvirt network for VMs)
   AUDIOBOOKS_WHISPER_GPU_HOST=192.168.122.1
   AUDIOBOOKS_WHISPER_GPU_PORT=8765
   ```

3. **Considerations**:
   - Long transcription jobs can cause system instability if the GPU is shared with desktop/display tasks
   - Local GPU is automatically deprioritized for long-form work when remote providers are configured
   - Useful as a testing and development tool rather than a production workhorse

---

## Configuration Reference

All localization settings are environment variables, read from `/etc/audiobooks/audiobooks.conf` or `~/.config/api-keys.env`. API keys should always go in `~/.config/api-keys.env` (permissions `600`, owner-only).

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_DEFAULT_LOCALE` | `en` | Default locale for the UI when no user preference is set |
| `AUDIOBOOKS_SUPPORTED_LOCALES` | `en,zh-Hans` | Comma-separated list of enabled locale codes |

### STT (Speech-to-Text)

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_STT_PROVIDER` | `auto` | Provider selection: `auto`, `whisper`, or `deepl`. Auto selects based on workload and provider availability. |

### TTS (Text-to-Speech)

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_TTS_PROVIDER` | `edge-tts` | Provider: `edge-tts`, `xtts-runpod`, or `xtts-vastai` |
| `AUDIOBOOKS_TTS_VOICE_ZH` | `zh-CN-XiaoxiaoNeural` | Microsoft Neural TTS voice for Chinese narration (edge-tts) |

For additional languages, set `AUDIOBOOKS_TTS_VOICE_<LANG>` where `<LANG>` is the uppercase language subtag. For example, `AUDIOBOOKS_TTS_VOICE_JA` for Japanese, `AUDIOBOOKS_TTS_VOICE_KO` for Korean.

To list all available edge-tts voices:
```bash
edge-tts --list-voices
```

### API Keys

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_DEEPL_API_KEY` | (none) | DeepL API authentication key |
| `AUDIOBOOKS_RUNPOD_API_KEY` | (none) | RunPod API key |

### Provider Endpoints

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT` | (none) | RunPod serverless Whisper endpoint ID |
| `AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT` | (none) | RunPod serverless XTTS endpoint ID |
| `AUDIOBOOKS_VASTAI_WHISPER_HOST` | (none) | Vast.ai Whisper instance IP/hostname |
| `AUDIOBOOKS_VASTAI_WHISPER_PORT` | `8000` | Vast.ai Whisper instance port |
| `AUDIOBOOKS_VASTAI_XTTS_HOST` | (none) | Vast.ai XTTS instance IP/hostname |
| `AUDIOBOOKS_VASTAI_XTTS_PORT` | `8020` | Vast.ai XTTS instance port |
| `AUDIOBOOKS_WHISPER_GPU_HOST` | `192.168.122.1` | Local GPU Whisper service host |
| `AUDIOBOOKS_WHISPER_GPU_PORT` | `8765` | Local GPU Whisper service port |

### Metadata Enrichment

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_DOUBAN_API_KEY` | (none) | Douban Books API key for Chinese book metadata lookup (optional, API access has been restricted since 2019) |

---

## Adding a New Language

Adding a new language requires no code changes. Follow these steps:

### Step 1: Create the Locale File

Copy the English locale file and translate all keys:

```bash
cp library/locales/en.json library/locales/<locale>.json
```

For example, to add Japanese:
```bash
cp library/locales/en.json library/locales/ja.json
```

The locale file contains 1,038 keys organized by UI section. Each key maps to a translated string:

```json
{
  "nav.library": "Library",
  "nav.collections": "Collections",
  "nav.settings": "Settings",
  "player.play": "Play",
  "player.pause": "Pause",
  ...
}
```

You can translate the file manually, use the DeepL API programmatically, or use any translation tool of your choice. Every key must have a translation -- missing keys fall back to the English value at runtime.

### Step 2: Register the Locale

Add your new locale to `AUDIOBOOKS_SUPPORTED_LOCALES` in `/etc/audiobooks/audiobooks.conf`:

```bash
AUDIOBOOKS_SUPPORTED_LOCALES="en,zh-Hans,ja"
```

Restart the API service after changing this value:
```bash
sudo systemctl restart audiobook-api.service
```

### Step 3: Configure a TTS Voice

If you want translated audio narration, set the TTS voice for your language. First, find available voices:

```bash
edge-tts --list-voices | grep ja-JP
```

Example output:
```
ja-JP-NanamiNeural
ja-JP-KeitaNeural
```

Then set the voice in `/etc/audiobooks/audiobooks.conf`:
```bash
AUDIOBOOKS_TTS_VOICE_JA="ja-JP-NanamiNeural"
```

### Step 4: Test

1. Log in to the web UI
2. Open user preferences (profile settings)
3. Select your new locale from the language dropdown
4. Verify all UI text renders in the target language
5. Check that book descriptions translate on demand (requires DeepL API key)

### Step 5: CJK Considerations

If your new language uses CJK characters (Chinese, Japanese, Korean), no additional work is needed. Audiobook Manager already includes:

- **CJK-aware search**: Full-text search handles CJK character boundaries correctly
- **Collation sort**: Library sorting uses locale-appropriate ordering (e.g., pinyin for Chinese, stroke order, etc.)
- **Font rendering**: The web UI loads CJK-capable font stacks

---

## Cost and Time Investment

This section provides honest numbers from the project's real-world development experience so prospective admins can make informed decisions.

### Total Project Investment (November 2025 -- April 2026)

Audiobook Manager is a substantial engineering project. The localization system is one component of a much larger whole:

| Category | Estimate | Notes |
|----------|----------|-------|
| Developer time (total project) | ~1,000+ hours | Architecture, coding, testing, debugging, infrastructure. Includes all features, not just localization. |
| Human labor value | ~$70,000 | At a senior *NIX engineer's rate (~$70/hour based on $145k/year salary) |
| AI development partner | ~$500-1,000 | Anthropic Claude Max plan (~$100-200/month over 5 months). Claude was a development partner from the beginning -- architecture, code, security, testing. |
| **Total project cost** | **~$71,000-72,000** | Overwhelmingly human time |

### Localization-Specific Costs

The translation/multilingual subsystem represents a meaningful fraction of the total effort:

| Category | Estimate | Notes |
|----------|----------|-------|
| Developer time on localization | 150-250 hours | i18n architecture, STT/TTS pipeline, locale files, testing, provider integration, subtitle generation |
| Developer labor value | ~$10,500-17,500 | At ~$70/hour |
| DeepL API | $0-50 | Free tier (500k chars/month) was sufficient. 1,038 UI strings + book descriptions consumed a fraction of the free tier. |
| GPU rental (STT + TTS) | $150-450 | For a library of 600-800 audiobooks (~2,000-4,000 hours of audio). Varies by GPU pricing and audio length. |
| **Total localization cost** | **~$10,650-18,000** | Mostly developer time |

### GPU Cost Breakdown

For a library of ~600-800 audiobooks:

| Task | GPU Hours | Cost Range | Notes |
|------|-----------|------------|-------|
| STT transcription (Whisper) | 50-150 hrs | $10-75 | Depends on audio length and GPU speed. An A100 transcribes ~10x real-time. |
| TTS narration (edge-tts) | 0 | $0 | Free. Microsoft Neural TTS, no API key, no GPU required. |
| TTS narration (XTTS voice cloning) | 200-600 hrs | $40-300 | Only if you want narrator voice preservation. Much more compute-intensive than edge-tts. |
| **Total GPU cost** | -- | **$10-375** | edge-tts is free; XTTS adds significant cost |

### What a New Admin Should Expect

| Scenario | Time | Cost |
|----------|------|------|
| **Using a shipped language** (en or zh-Hans) with existing locale files | Hours | $0 (edge-tts) or $150-450 (XTTS) in GPU for audio narration |
| **Adding a new language** with DeepL + edge-tts | 1-3 days | $0-50 (DeepL free tier + free TTS). GPU for STT: $10-75. |
| **Adding a new language** with XTTS voice cloning | 1-3 days + GPU processing time | $150-450 depending on library size |
| **Building this from scratch** (as this project did) | Months of engineering | $10,000+ in developer time alone |

The key takeaway: the expensive part is building the infrastructure, not using it. Once the pipeline exists, adding a language is a configuration task, not an engineering project.

---

## Translation Asset Portability

Translation work (VTT subtitles, TTS audio files, metadata translations) costs real money in GPU time. The `audiobook-translations` CLI tool lets you export and import these assets between environments (dev, test, QA, production) without re-translating.

### Export

Export all translation assets to a portable tarball:

```bash
audiobook-translations export -o translations.tar.gz
```

Export only a specific locale:

```bash
audiobook-translations export -o zh-translations.tar.gz --locale zh-Hans
```

The export bundles:
- VTT subtitle files (English source + translated)
- TTS audio files
- Database rows (metadata translations, collection translations, string translations)
- A manifest mapping audiobook IDs to titles for cross-environment matching

### Import

Import a translation archive into a different environment:

```bash
audiobook-translations import -a translations.tar.gz
```

The import process:
1. Reads the manifest from the archive
2. Matches books by title between source and target databases
3. Extracts VTT and audio files to the correct book directories
4. Inserts/replaces translation database rows
5. Marks imported books as completed in the translation queue

Books that exist in the archive but not in the target database are skipped with a warning.

### Custom Database Path

Both commands accept `--db` to specify a non-default database path:

```bash
audiobook-translations export --db /path/to/audiobooks.db -o export.tar.gz
audiobook-translations import --db /path/to/audiobooks.db -a export.tar.gz
```

If `--db` is not specified, the tool uses `$AUDIOBOOKS_DATABASE` from your configuration.

---

## Dependencies and Requirements

### Required

| Dependency | Minimum Version | Purpose |
|------------|----------------|---------|
| Python | 3.12+ (3.14 recommended) | Localization module runtime |
| ffmpeg | 7.0+ | Audio conversion, chapter detection, format transcoding |
| SQLite | 3.38+ (with JSON1) | Translation metadata storage |
| `edge-tts` | 7.0+ | Default TTS provider (Microsoft Neural TTS) |
| `requests` | 2.33+ | HTTP client for DeepL API, GPU provider APIs, and all remote calls |
| `pypinyin` | 0.55+ | Mandarin pinyin conversion for CJK sort and search (zh-Hans locale) |

### Optional

| Dependency | Purpose |
|------------|---------|
| `openai-whisper` | Local CPU fallback for STT (very slow but always available) |
| CUDA toolkit | NVIDIA GPU acceleration for local Whisper |
| ROCm | AMD GPU acceleration for local Whisper |

### Python Package Installation

The localization dependencies are included in the project's `requirements.txt` and installed automatically during `install.sh` or `upgrade.sh`. For manual installation:

```bash
pip install edge-tts requests pypinyin
```

For local Whisper fallback (optional):
```bash
pip install openai-whisper
```

---

## Attribution

The localization system was built using the following open-source and commercial services:

| Component | Role | License/Terms |
|-----------|------|---------------|
| [OpenAI Whisper](https://github.com/openai/whisper) | Speech-to-text transcription | MIT License |
| [DeepL](https://www.deepl.com/) | Neural machine translation | Commercial API (free tier available) |
| [Microsoft Edge TTS](https://github.com/rany2/edge-tts) | Neural text-to-speech synthesis | MIT License (library); Microsoft terms (service) |
| [XTTS / Coqui TTS](https://github.com/coqui-ai/TTS) | Multilingual voice cloning | MPL-2.0 License |
| [Vast.ai](https://vast.ai/) | Peer-to-peer GPU marketplace | Commercial |
| [RunPod](https://www.runpod.io/) | Serverless GPU platform | Commercial |
| [Hugging Face](https://huggingface.co/) | Model hosting (Whisper, XTTS models) | Various open licenses |
| [Anthropic Claude](https://www.anthropic.com/) | AI development partner (architecture, code, testing) | Claude Code CLI with Max plan |

---

## Troubleshooting

### Provider Connection Issues

**Symptom**: Translation jobs fail with connection errors or timeouts.

| Provider | Common Cause | Fix |
|----------|-------------|-----|
| Vast.ai | Instance not running or IP changed | Check instance status on vast.ai dashboard. Instance IPs change on restart -- update config. |
| RunPod | Serverless cold start timeout | First request after idle period takes 10-30 seconds. Increase client timeout or send a warm-up request. |
| Local GPU | Service not started | Verify the Whisper service is running on the configured host and port. |
| DeepL | Invalid or expired API key | Verify key at [deepl.com/account](https://www.deepl.com/account). Free keys end with `:fx`. |

**Fallback behavior**: When a remote provider fails, the system falls back to local processing once per request. If you see "falling back to local" in logs, your remote provider is unreachable but translation is still proceeding (slowly).

### DeepL Rate Limits

**Symptom**: Translation stops partway through with HTTP 429 or 456 errors.

- **Free tier**: 500,000 characters/month. The quota tracker in `library/localization/translation/quota.py` monitors usage.
- **Fix**: Wait for quota reset (monthly) or upgrade to DeepL Pro (pay-per-use, no hard limit).
- **Workaround**: Export partially-completed translations, then resume next month.

### GPU Cold Starts

**Symptom**: First transcription request takes 30-120 seconds before processing begins.

- **Vast.ai**: Dedicated instances have no cold start once running. The delay is model loading on first use.
- **RunPod serverless**: Cold start is inherent to serverless -- the GPU spins up on demand. Subsequent requests within the keep-alive window are fast.
- **Mitigation**: For batch processing, send a short test file first to warm the instance before queuing long audiobooks.

### CJK Font Rendering

**Symptom**: Chinese/Japanese/Korean characters display as boxes or tofu in the web UI.

- The web UI uses system font stacks with CJK fallbacks. If your browser or OS lacks CJK fonts, install them:
  ```bash
  # Arch/CachyOS
  sudo pacman -S noto-fonts-cjk

  # Debian/Ubuntu
  sudo apt install fonts-noto-cjk

  # Fedora
  sudo dnf install google-noto-sans-cjk-fonts
  ```
- Clear browser cache after font installation.

### Subtitle Sync Issues

**Symptom**: VTT subtitles are out of sync with audio playback.

- Subtitle timestamps come from Whisper's word-level alignment. Sync quality depends on:
  - Audio quality (clean recordings sync better than noisy ones)
  - Whisper model size (larger models produce better timestamps)
- **Fix**: Re-transcribe with a larger Whisper model or a higher-quality GPU provider.

### Translation Quality

**Symptom**: Translated text reads unnaturally or contains errors.

- DeepL quality varies by language pair. English-to-Chinese is generally excellent.
- **Glossary support**: Add domain-specific terms to `library/localization/glossary/` to override DeepL's default translations for proper nouns, series names, and specialized vocabulary.
- **Manual correction**: Edit translated strings directly in the locale JSON file or the database. Manual edits are preserved across re-translations.

### edge-tts Voice Issues

**Symptom**: TTS audio sounds robotic or uses the wrong voice.

- List available voices: `edge-tts --list-voices | grep <language-code>`
- Different voices have different quality levels. `XiaoxiaoNeural` (Chinese) and `NanamiNeural` (Japanese) are among the highest quality.
- Set the voice explicitly in your configuration rather than relying on defaults.

### Import Fails with "No matching books found"

**Symptom**: `audiobook-translations import` reports 0 matched books.

- The import process matches books by title between the source and target databases. If titles differ (e.g., due to re-scanning or metadata updates), no match is found.
- **Fix**: Ensure both environments have the same audiobooks scanned before importing. The tool prints which books were unmatched.
