# Localization R&D Design Spec

**Branch:** `Localization-RND`
**Date:** 2026-04-07
**Priority locale:** zh-Hans (Simplified Chinese / Mandarin)
**Motivation:** Qing (Bosco's partner, Mandarin speaker) wants to navigate the UI, read translated subtitles, and listen to translated audio for English audiobooks.

---

## Scope

Three phases, built in order. Each phase is independently useful — Phase 1 delivers a localized UI, Phase 2 adds subtitles, Phase 3 adds translated audio.

| Phase | Deliverable | Dependencies |
|-------|-------------|--------------|
| 1. UI i18n | Full Mandarin web interface + localized book card metadata | DeepL Pro API, Douban Books API |
| 2. Subtitles | Timestamped dual-language subtitles synced to audio playback | Phase 1 locale infrastructure, DeepL STT, RunPod Whisper (fallback) |
| 3. Translated audio | Pre-generated Mandarin audio per chapter, selectable in player | Phase 2 transcripts, edge-tts (default), Coqui XTTS (upgrade path) |

---

## Phase 1: UI Internationalization

### 1.1 Backend i18n (Unified JSON Catalogs)

**Decision (2026-04-08):** Flask-Babel rejected in favor of unified JSON catalogs.
Flask-Babel's strength is Jinja template integration, which is irrelevant here (pure
JSON API, no templates). Using Flask-Babel would require maintaining two parallel i18n
systems (`.po` for Python, JSON for JS). Unified JSON catalogs let both backend and
frontend share one set of translation files with zero compilation steps. If the project
ever scales to 5+ languages with external translators, JSON can be mechanically
converted to `.po` format at that point.

**String extraction and catalogs:**

- Create `library/localization/i18n.py` — lightweight module with `t(key, locale)` lookup, loads from shared JSON catalogs
- Create `library/web-v2/locales/en.json` and `library/web-v2/locales/zh-Hans.json` — shared by both backend and frontend
- Backend serves locale files via `GET /api/i18n/<locale>` for frontend consumption
- Extract translatable strings from Python API modules (~600 strings across 19 files) into `en.json`
- Translate via DeepL Pro API with a glossary for audiobook-specific terms:

| English | Chinese | Notes |
|---------|---------|-------|
| audiobook | 有声书 | |
| narrator | 朗读者 | |
| chapter | 章节 | |
| collection | 合集 | |
| series | 系列 | |
| playback speed | 播放速度 | |
| bookmark | 书签 | |
| library | 图书馆 | |

- All API error/success messages wrapped in `t(key, locale)` calls

**Locale detection priority (highest to lowest):**

1. User's explicit setting in `user_settings` table
2. `Accept-Language` HTTP header
3. System default (`en`)

### 1.2 Frontend i18n (Vanilla JS)

Since the frontend is vanilla JS (no React/Vue), use a lightweight custom i18n approach:

- Create `library/web-v2/js/i18n.js` — a translation loader that:
  - Fetches locale JSON files from `/api/i18n/<locale>`
  - Provides a `t(key, params)` function for string lookup with interpolation
  - Caches loaded translations in memory
- Create `library/web-v2/locales/en.json` and `library/web-v2/locales/zh-Hans.json`
- Refactor all 13 HTML files to use `data-i18n="key"` attributes on translatable elements
- Refactor JS files to replace hardcoded strings with `t('key')` calls
- Add locale switcher in the account settings panel and shell header

**String categories to extract (~800-1000 total):**

- Form labels and placeholders (~100)
- Button text and tooltips (~80)
- Navigation tabs and menu items (~30)
- Error and success notifications (~120)
- Player controls and labels (~40)
- Sort/filter options (~25)
- Help text and documentation (~150)
- Admin/back-office labels (~200)
- Accessibility labels (~50)
- Modal dialogs and confirmations (~50)

### 1.3 CJK Typography

Add CJK font stack to CSS:

```css
:root {
  --font-cjk: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}
```

Apply `var(--font-cjk)` as a fallback in all `font-family` declarations. CJK characters are typically wider — verify layout doesn't break with Chinese strings (buttons, nav tabs, table headers).

**User-controlled font sizing:**

- Add a font size slider/stepper in account settings and as a quick-access control in the shell header (near the locale switcher)
- Range: 12px to 28px in 2px increments, stored in `user_settings.font_size`
- Applies globally via CSS custom property `--user-font-size` on `<html>` element
- Subtitle text (inline and side panel) scales independently with its own size control in the player area
- Persisted per-user so Qing's preference survives sessions
- Default: 16px (standard), but respects user's browser zoom as a baseline multiplier

### 1.4 Localized Book Card Metadata

**Data source hierarchy (per book, per locale):**

1. **Admin override** — manually entered Chinese title/author/translator in Back Office (highest priority)
2. **Douban Books lookup** — query Douban API by ISBN or title+author for canonical Chinese metadata
3. **DeepL translation fallback** — machine-translate title and transliterate author name

**Storage:**
New `audiobook_translations` table in the main audiobooks database:

```sql
CREATE TABLE audiobook_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,           -- e.g., 'zh-Hans'
    title TEXT,
    author TEXT,
    translator TEXT,                -- book translator, not our translation
    source TEXT NOT NULL,           -- 'admin', 'douban', 'deepl'
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    UNIQUE(audiobook_id, locale)
);
CREATE INDEX idx_translations_locale ON audiobook_translations(locale);
```

**Display behavior:**

- When user's locale matches a translation entry, show localized title/author on the book card
- Original English always visible (either as subtitle text or tooltip)
- If no translation exists for a book, show English with no tooltip

**Admin Back Office:**

- New "Translations" tab per book in the detail view
- Editable fields: title, author, translator per locale
- "Auto-translate" button that runs the lookup hierarchy and populates fields
- Admin can override/correct any auto-generated translation

### 1.5 Database Migrations

**Auth DB migration** (next sequence number):

```sql
-- Add locale preference and font size to user_settings
ALTER TABLE user_settings ADD COLUMN locale TEXT DEFAULT 'en';
ALTER TABLE user_settings ADD COLUMN font_size INTEGER DEFAULT 16;
```

**Main DB migration** (next sequence number):

```sql
-- Audiobook translations table
CREATE TABLE IF NOT EXISTS audiobook_translations ( ... );
```

### 1.6 Configuration

New variables in `audiobook-config.sh`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUDIOBOOKS_DEFAULT_LOCALE` | `en` | System default locale |
| `AUDIOBOOKS_SUPPORTED_LOCALES` | `en,zh-Hans` | Comma-separated available locales |
| `AUDIOBOOKS_DEEPL_API_KEY` | (from api-keys.env) | DeepL Pro API key |
| `AUDIOBOOKS_DOUBAN_API_KEY` | (empty) | Douban Books API key (if required) |

---

## Phase 2: Subtitle Pipeline

### 2.1 STT Provider Interface

Pluggable speech-to-text with two backends:

```text
library/localization/
    __init__.py
    stt/
        __init__.py
        base.py          # Abstract STTProvider interface
        deepl_stt.py     # DeepL STT (primary, 33K min/month)
        whisper_stt.py   # RunPod Whisper large-v3 (fallback)
    translation/
        __init__.py
        deepl_translate.py  # DeepL text translation
    tts/
        __init__.py
        base.py          # Abstract TTSProvider interface
        edge_tts.py      # Microsoft edge-tts (default)
        xtts.py          # Coqui XTTS on RunPod (upgrade path)
    subtitles/
        __init__.py
        vtt_generator.py # Generate VTT/SRT from timestamped transcript
        sync.py          # Align translated text to original timestamps
    metadata/
        __init__.py
        douban.py        # Douban Books API client
        lookup.py        # Hybrid lookup orchestrator
    pipeline.py          # End-to-end orchestrator
    config.py            # Localization-specific configuration
```

**STTProvider interface:**

```python
class STTProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Returns timestamped word-level transcript."""
        ...

    @abstractmethod
    def supports_language(self, language: str) -> bool: ...

    @abstractmethod
    def usage_remaining(self) -> int | None:
        """Minutes remaining in billing period, or None if unlimited."""
        ...
```

**Provider selection logic:**

1. Check DeepL STT usage remaining
2. If > 60 min remaining, use DeepL STT
3. Otherwise, route to RunPod Whisper
4. Config override: `AUDIOBOOKS_STT_PROVIDER=deepl|whisper|auto` (default: `auto`)

### 2.2 Transcript → Translation → VTT

**Pipeline per chapter:**

1. STT produces word-level timestamps: `[{word, start_ms, end_ms}, ...]`
2. Group words into sentences using punctuation + pause detection
3. Send sentences to DeepL translation (preserving sentence boundaries)
4. Generate dual-language VTT files:
   - `<Chapter>.en.vtt` — original English subtitles
   - `<Chapter>.zh-Hans.vtt` — translated Mandarin subtitles
5. Store VTT file paths in a new `chapter_subtitles` table

**Timestamp alignment strategy:**
Chinese translations are often shorter than English source text. Preserve the original English timing boundaries — each translated subtitle cue inherits the start/end time of its source sentence. This keeps subtitles synchronized with the original audio narration.

### 2.3 Subtitle Storage

```sql
CREATE TABLE chapter_subtitles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,     -- 0-based chapter order
    chapter_title TEXT,
    locale TEXT NOT NULL,               -- 'en', 'zh-Hans'
    vtt_path TEXT NOT NULL,             -- relative to library root
    stt_provider TEXT,                  -- 'deepl', 'whisper'
    translation_provider TEXT,          -- 'deepl' (or null for source language)
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    UNIQUE(audiobook_id, chapter_index, locale)
);
```

**File storage convention:**

```text
Library/
  <Author> - <Title>/
    <Chapter>.opus              # Original audio
    subtitles/
      <Chapter>.en.vtt          # English transcript
      <Chapter>.zh-Hans.vtt     # Mandarin translation
```

### 2.4 Player UI — Inline Subtitles

Rendered below the existing player controls in `shell.html`:

- Subtitle container: `<div id="subtitle-display">` below `#shell-player`
- Shows current cue in both languages (English top, Mandarin bottom)
- Auto-scrolls with playback position via `timeupdate` event on the `<audio>` element
- Toggle visibility with a subtitle button (CC icon) in player controls
- Font sizing: slightly larger for CJK characters (1.1em vs 1em for Latin)

### 2.5 Player UI — Side Panel Transcript

Collapsible panel alongside the main content area:

- Full chapter transcript with all cues listed vertically
- Dual-language: English line, then Mandarin line (stacked, not side-by-side) per cue
- Current cue highlighted with accent color background
- Auto-scroll to keep current cue in view (with smooth scrolling)
- Click any cue to seek audio to that timestamp
- Toggle panel with a "Transcript" button in the player toolbar
- Panel width: 350px on desktop, full-width drawer on mobile

### 2.6 Batch Processing

Subtitle generation runs as background batch jobs:

- New API endpoint: `POST /api/localization/subtitles/generate` (admin only)
  - Parameters: `audiobook_id`, `locale`, `chapters` (all or specific indices)
  - Returns job ID for progress tracking
- Progress tracked via WebSocket (existing `websocket.py` infrastructure)
- Job queue managed by existing `audiobook-converter.service` pattern or a new `audiobook-localizer.service`
- Rate limiting: respect DeepL STT monthly cap, queue overflow jobs for next billing cycle

---

## Phase 3: Translated Audio (Speech-to-Speech)

### 3.1 TTS Provider Interface

```python
class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, language: str, voice: str, output_path: Path) -> Path:
        """Generate audio file from text. Returns path to generated file."""
        ...

    @abstractmethod
    def available_voices(self, language: str) -> list[Voice]: ...

    @abstractmethod
    def requires_gpu(self) -> bool: ...
```

**Default provider: edge-tts**

- Voices for zh-CN: `zh-CN-XiaoxiaoNeural` (female), `zh-CN-YunyangNeural` (male)
- No GPU required, near-instant generation
- Free (Microsoft's public TTS API)

**Upgrade path: Coqui XTTS v2**

- Deploy on RunPod as serverless endpoint
- Voice cloning: feed 10-30 seconds of original narrator's voice
- GPU-intensive: ~10-30 min per hour of audio on A40
- Config: `AUDIOBOOKS_TTS_PROVIDER=edge-tts|xtts|auto`

### 3.2 Translation → Audio Pipeline

Per chapter:

1. Read translated text from Phase 2 transcript (or re-translate if needed)
2. Split into TTS-friendly chunks (respect sentence boundaries, max ~500 chars per chunk for edge-tts)
3. Generate audio per chunk via TTS provider
4. Concatenate chunks with crossfade using ffmpeg
5. Encode final chapter audio as Opus: `Library/<Book>/translated/<Chapter>.zh-Hans.opus`
6. Store metadata in DB

### 3.3 Translated Audio Storage

```sql
CREATE TABLE chapter_translations_audio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    locale TEXT NOT NULL,
    audio_path TEXT NOT NULL,            -- relative to library root
    tts_provider TEXT NOT NULL,          -- 'edge-tts', 'xtts'
    tts_voice TEXT,                      -- e.g., 'zh-CN-XiaoxiaoNeural'
    duration_seconds REAL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    UNIQUE(audiobook_id, chapter_index, locale)
);
```

**File storage:**

```text
Library/
  <Author> - <Title>/
    <Chapter>.opus                      # Original audio
    subtitles/
      <Chapter>.en.vtt
      <Chapter>.zh-Hans.vtt
    translated/
      <Chapter>.zh-Hans.opus            # Translated audio
```

### 3.4 Player Language Toggle

- New toggle in player controls: flag icon or language dropdown
- Options: "Original (English)" / "Mandarin (普通话)"
- Switching language:
  - Swaps the `<audio>` source to the translated audio file
  - Updates subtitle display to match (or keeps dual-language)
  - Preserves current playback position (seeks to equivalent timestamp)
- If translated audio doesn't exist for a chapter, toggle is disabled with tooltip explaining why

### 3.5 Batch Processing

Same pattern as Phase 2:

- `POST /api/localization/audio/generate` (admin only)
- Background job queue via systemd service
- Progress via WebSocket
- Config: voice selection per book or globally

---

## Cross-Cutting Concerns

### Security

- DeepL API key stored in `~/.config/api-keys.env` (per existing convention), never in source
- RunPod API key same treatment
- Douban API key same treatment
- All localization API endpoints require admin role (generation is expensive)
- Read endpoints (fetching subtitles, translated audio) follow existing auth model (admin_or_localhost)
- Input validation on locale codes: whitelist against `AUDIOBOOKS_SUPPORTED_LOCALES`
- VTT files served with `Content-Type: text/vtt; charset=utf-8`, not user-controlled paths (prevent path traversal)

### Performance

- Translation results cached in DB (never re-translate the same book+locale)
- VTT files served directly by Caddy (static files), not proxied through Flask
- Translated audio streamed same as original (existing streaming infrastructure)
- Batch jobs run at low priority (`nice` / `ionice`) to avoid impacting playback

### Testing

- Unit tests: mock DeepL/RunPod APIs, test VTT generation, timestamp alignment, locale detection
- Integration tests (VM): full pipeline with a short test audio file, verify VTT output, verify player loads subtitles
- Test audio: create a 30-second synthetic English audio file for pipeline testing (avoids using production audiobooks in tests)

### Configuration Summary

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUDIOBOOKS_DEFAULT_LOCALE` | `en` | System-wide default |
| `AUDIOBOOKS_SUPPORTED_LOCALES` | `en,zh-Hans` | Available locales |
| `AUDIOBOOKS_DEEPL_API_KEY` | (from api-keys.env) | DeepL Pro API |
| `AUDIOBOOKS_STT_PROVIDER` | `auto` | `deepl`, `whisper`, `auto` |
| `AUDIOBOOKS_TTS_PROVIDER` | `edge-tts` | `edge-tts`, `xtts` |
| `AUDIOBOOKS_TTS_VOICE_ZH` | `zh-CN-XiaoxiaoNeural` | Default Mandarin voice |
| `AUDIOBOOKS_RUNPOD_API_KEY` | (from api-keys.env) | RunPod serverless |
| `AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT` | (empty) | RunPod Whisper endpoint ID |
| `AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT` | (empty) | RunPod XTTS endpoint ID |
| `AUDIOBOOKS_DOUBAN_API_KEY` | (empty) | Douban Books API |

---

## Known Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Douban Books API access restricted (API keys are hard to obtain since 2019) | No canonical Chinese metadata lookup | Fall back to DeepL translation for all books; investigate alternative sources (WorldCat, CNKI, or web scraping with rate limiting) |
| DeepL STT 33K min/month cap hit during bulk processing | Subtitle generation stalls mid-library | Auto-routing to RunPod Whisper (already in design); usage dashboard in admin UI |
| Edge-tts Microsoft API rate limits or deprecation | Translated audio generation blocked | XTTS on RunPod as fallback; edge-tts is widely used and stable as of 2026 |
| CJK text layout breaks in existing UI | Visual regressions on Chinese locale | Phase 1 includes layout testing; Chinese strings are typically shorter than English equivalents |

## Out of Scope (for this R&D branch)

- Locales beyond zh-Hans (architecture supports it, but only Mandarin is implemented)
- Real-time translation (all translation is pre-generated batch)
- Voice cloning (XTTS interface stubbed but not deployed)
- Automated CI/CD for translation catalog updates
- RTL language support
- Multi-narrator voice assignment (single voice per translated book)
