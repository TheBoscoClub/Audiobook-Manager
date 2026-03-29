# v8 Design: Collections Overhaul + User Preferences

**Status**: Brainstorming complete, approved by user. Implementation deferred to v8.
**Date**: 2026-03-27

## Collections System Overhaul

### Core Change

Replace hardcoded `COLLECTION_TREE` in `collections.py` with a dynamic system that builds the collection hierarchy from enrichment data. Clean break — no fallback to old pattern-matching.

### Approach: Hybrid (Option C)

Fixed top-level categories, auto-generated subcategories from enrichment data.

### Top-Level Categories (Fixed)

| Category | Source | Children auto-generated from |
|----------|--------|------------------------------|
| **Fiction** | Fixed | Fiction genres in `audiobook_genres` |
| **Nonfiction** | Fixed | Nonfiction genres in `audiobook_genres` |
| **Series** | Fixed | Distinct `series` values, type-badged, with "X of Y" counts |
| **Eras** | Fixed | Distinct eras in `audiobook_eras` |
| **Topics** | Fixed | Distinct topics in `audiobook_topics` |
| **Podcasts** | Fixed (special) | `content_type` filter |
| **Great Courses** | Fixed (special) | Author filter |
| **Lectures** | Fixed (special) | `content_type` filter |

### Series Collection Behavior

- Single Series collection (not split by content type)
- Each series entry has a **content type badge** (Audiobook, Lecture, Podcast)
- Shows **"X of Y books"** ownership count (e.g., "3 of 8 books")
- Total series length comes from enrichment data
- All series shown, including single-book series
- Books within a series sorted by `series_sequence` (first to last)

### Eras and Topics: Dual Role

- Exist as **top-level categories** (browsable collections)
- Also available as **cross-cutting filters** within any other collection
- e.g., browsing Fiction > Mystery, you can filter by "19th Century" or "war"

### Migration Strategy: Clean Break (Option A)

- Delete entire `COLLECTION_TREE` and all hardcoded genre patterns
- Rebuild collections entirely from enrichment data
- Books without enrichment data are invisible in collections (intentional)
- Missing books signal need for re-enrichment

## User Preferences System

### Data Model: Key-Value Table

```sql
user_preferences (user_id, preference_key, preference_value)
```

Server-side only — requires authentication. Localhost users get defaults.

### Preference Categories

#### Browsing & Display

- **Sort order** — persists last-selected sort between visits
- **View mode** — grid vs list
- **Items per page** — 20, 50, 100
- **Default landing collection** — which collection opens first
- **Content filter** — hide adult content

#### Playback

- **Default playback speed** — 1x, 1.25x, 1.5x, 2x
- **Sleep timer default** — off, 15min, 30min, 60min
- **Auto-play next in series** — yes/no

#### Accessibility

- **Font size** — 14px / 16px / 18px / 20px (CSS custom properties, rem-based)
- **Contrast** — text/background contrast level
- **Background opacity** — dark panel opacity
- **Line spacing** — line-height 1.2 / 1.4 / 1.6
- **Reduce animations** — manual toggle (supplements `prefers-reduced-motion`)
- **High contrast mode** — pre-built accessibility variant (one toggle)
- **Foreground color temperature** — warm gold (default) vs cooler cream/white

### Preferences UI (Option C: Split)

- **Gear icon in header** — quick-access panel for accessibility settings (font size, contrast, opacity, etc.)
- **Account page** — browsing and playback preferences

### Art Deco Theme

- Non-negotiable — the Art Deco aesthetic is the app's identity
- Font family choices NOT offered (core to identity)
- Color scheme overhaul NOT offered
- Accessibility options work WITHIN the Art Deco framework

## Documentation Requirements

- ALL changes documented in ARCHITECTURE.md, README.md, CHANGELOG.md, CSS-CUSTOMIZATION.md
- In-app help page updated
- Tutorial updated — accessibility options emphasized early in onboarding flow
- FAQ updated — accessibility section near the top
- API docs updated in code

## Decisions Log

| Question | Decision | Rationale |
|----------|----------|-----------|
| How dynamic should collections be? | Hybrid (C) | Predictable nav + auto-generated content |
| Sort persistence scope? | Server-side only (C) | Auth required, localhost gets defaults |
| Series minimum threshold? | Show all (C) with counts | "3 of 8 books" even for single-book series |
| Series split by content type? | No — single list with type badges (A) | Avoids fragmentation |
| Eras/Topics role? | Both top-level AND filters (C) | Different browsing intents served |
| Preferences data model? | Key-value table | 14+ preferences makes column-per-pref impractical |
| Preferences UI location? | Split (C) | Accessibility in header gear icon, rest in Account |
| Migration strategy? | Clean break (A) | Enrichment data is ready, no safety net needed |
